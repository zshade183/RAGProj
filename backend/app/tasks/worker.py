"""Run from repository root: python -m app.tasks.worker"""

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time

from app.core.config import get_settings
from app.db.session import engine
from app.tasks.pipeline import ROOT, digest, write_json
from app.tasks.queue import claim, finish, heartbeat

logger = logging.getLogger(__name__)


async def stop_tree(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        await asyncio.to_thread(
            subprocess.run,
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await asyncio.to_thread(process.wait)


async def execute(task):
    settings = get_settings()
    folder = ROOT / "storage" / "processed" / task["document_id"] / task["token"]
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "task.json", task)
    process = None
    started = time.monotonic()
    stage = "download"
    logger.info(
        "Processing document=%s attempt=%s folder=%s", task["document_id"], task["attempt"], folder
    )
    try:
        with (folder / "pipeline.log").open("wb") as log:
            process = await asyncio.to_thread(
                subprocess.Popen,
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "app.tasks.pipeline",
                    "--task",
                    str(folder / "task.json"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            while True:
                progress = folder / "progress.json"
                if progress.exists():
                    try:
                        stage = json.loads(progress.read_text(encoding="utf-8"))["stage"]
                    except (OSError, ValueError, KeyError):
                        pass
                if not await heartbeat(task, stage):
                    await stop_tree(process)
                    logger.warning("Lease lost; result will not be published: %s", task["job_id"])
                    return
                if process.poll() is not None:
                    break
                if time.monotonic() - started > settings.worker_task_timeout:
                    raise TimeoutError("Task wall-clock timeout")
                # Short heartbeat intervals let a delete request fence the live child promptly.
                await asyncio.sleep(1)
        if process.returncode != 0:
            failure = folder / "failure.json"
            kind = (
                json.loads(failure.read_text(encoding="utf-8")).get("error", "ProcessError")
                if failure.exists()
                else "ProcessError"
            )
            raise RuntimeError(f"{stage}: {kind}; exit={process.returncode}")
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        path = (ROOT / result["manifest_path"]).resolve()
        if not path.is_relative_to(folder) or digest(path) != result["snapshot_id"]:
            raise ValueError("Invalid pipeline result")
        published = await finish(task, result=result)
        logger.info("Publication confirmed=%s document=%s", published, task["document_id"])
    except asyncio.CancelledError:
        await stop_tree(process)
        # Lease expiry recovers an interrupted task even if DB is unavailable now.
        raise
    except Exception as exc:
        await stop_tree(process)
        logger.exception("Task failed document=%s", task["document_id"])
        try:
            await finish(
                task,
                error=f"阶段 {stage} 失败（{type(exc).__name__}）；日志：storage/processed/{task['document_id']}/{task['token']}/pipeline.log",
            )
        except Exception:
            logger.exception("Could not persist failure; lease expiry will recover")


async def record_pool_failure(task, message):
    logger.error("Multiprocessing task failure job=%s: %s", task["job_id"], message)
    try:
        await finish(task, error=message)
    except Exception:
        logger.exception("Could not requeue failed multiprocessing task job=%s", task["job_id"])


async def run_serial_worker():
    while True:
        try:
            task = await claim()
            if task:
                await execute(task)
            else:
                await asyncio.sleep(get_settings().worker_poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker loop error; retrying")
            await asyncio.sleep(get_settings().worker_poll_seconds)


async def main(*, use_multiprocessing_queue=False, workers=1):
    settings = get_settings()
    if (
        settings.worker_lease_seconds < 30
        or settings.worker_max_attempts < 1
        or settings.worker_poll_seconds < 1
        or settings.worker_task_timeout < 1
    ):
        raise ValueError("worker 配置无效（租约至少30秒，其余数值必须大于0）")
    if workers < 1:
        raise ValueError("workers 必须大于 0")
    try:
        if use_multiprocessing_queue:
            from app.tasks.multiprocessing_queue import run_task_pool

            stats = await run_task_pool(
                executor="app.tasks.worker:execute",
                claim_task=claim,
                failure_handler=record_pool_failure,
                workers=workers,
                poll_seconds=settings.worker_poll_seconds,
            )
            logger.info("Multiprocessing worker stopped: %s", stats)
        elif workers == 1:
            await run_serial_worker()
        else:
            raise ValueError("多进程 workers 需要启用 --multiprocessing-queue")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="RAG 文档处理 worker")
    parser.add_argument(
        "--multiprocessing-queue",
        action="store_true",
        help="使用有界 multiprocessing.Queue 将数据库任务分发给本地消费者进程",
    )
    parser.add_argument("--workers", type=int, default=1, help="启用多进程队列时的消费者数量")
    args = parser.parse_args()
    try:
        asyncio.run(
            main(
                use_multiprocessing_queue=args.multiprocessing_queue,
                workers=args.workers,
            )
        )
    except KeyboardInterrupt:
        pass
