"""Backfill queued documents while reusing one embedding model in one process."""

import argparse
import asyncio
import json
import logging
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.db.session import async_session_factory, engine
from app.models import ProcessingJob
from app.services.embeddings.bge import BgeEmbedding
from app.tasks import pipeline, queue
from sqlalchemy import func, update

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


async def heartbeat_loop(task, folder, stop: asyncio.Event):
    stage = "download"
    while not stop.is_set():
        progress = folder / "progress.json"
        try:
            stage = json.loads(progress.read_text(encoding="utf-8"))["stage"]
        except (KeyError, OSError, ValueError):
            pass
        if not await queue.heartbeat(task, stage):
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass


async def execute(task, embedder):
    folder = ROOT / "storage" / "processed" / task["document_id"] / task["token"]
    folder.mkdir(parents=True, exist_ok=False)
    pipeline.write_json(folder / "task.json", task)
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(heartbeat_loop(task, folder, stop))
    succeeded = False
    try:
        with (
            (folder / "pipeline.log").open("w", encoding="utf-8") as log,
            redirect_stdout(log),
            redirect_stderr(log),
        ):
            await asyncio.to_thread(pipeline.run, task, folder, embedder)
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        path = (ROOT / result["manifest_path"]).resolve()
        if not path.is_relative_to(folder) or pipeline.digest(path) != result["snapshot_id"]:
            raise ValueError("处理产物校验失败")
        if not await queue.finish(task, result=result):
            raise RuntimeError("租约失效，结果未发布")
        succeeded = True
    except Exception as exc:
        logger.exception("Backfill failed document=%s", task["document_id"])
        pipeline.write_json(folder / "failure.json", {"error": type(exc).__name__})
        await queue.finish(
            task,
            error=(
                f"批量补跑失败（{type(exc).__name__}）；日志："
                f"storage/processed/{task['document_id']}/{task['token']}/pipeline.log"
            ),
        )
    finally:
        stop.set()
        await heartbeat
    return succeeded


async def main() -> None:
    parser = argparse.ArgumentParser(description="顺序补跑 queued 文档，复用单个 BGE 进程")
    parser.add_argument("--limit", type=int, default=0, help="本次最多领取任务数；0 表示直到队列为空")
    parser.add_argument(
        "--reschedule",
        action="store_true",
        help="先把现有 queued 任务的 available_at 重置为当前时间",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    if args.reschedule:
        async with async_session_factory() as session, session.begin():
            result = await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.status == "queued")
                .values(available_at=func.now())
            )
        print(f"已重排 queued 任务 {result.rowcount} 条", flush=True)
    embedder = BgeEmbedding(
        ROOT / "storage" / "models",
        device=settings.worker_embedding_device,
        local_files_only=settings.model_local_files_only,
    )
    report = {"started_at": datetime.now(UTC).isoformat(), "tasks": [], "errors": []}
    processed = 0
    started = time.monotonic()
    try:
        while args.limit <= 0 or processed < args.limit:
            task = await queue.claim()
            if task is None:
                break
            processed += 1
            before = time.monotonic()
            succeeded = await execute(task, embedder)
            report["tasks"].append(
                {
                    "document_id": task["document_id"],
                    "filename": task["filename"],
                    "seconds": round(time.monotonic() - before, 2),
                    "succeeded": succeeded,
                }
            )
            if not succeeded:
                report["errors"].append(
                    {"document_id": task["document_id"], "filename": task["filename"]}
                )
            print(
                f"BACKFILL {processed} {task['filename']} "
                f"{report['tasks'][-1]['seconds']}s",
                flush=True,
            )
    finally:
        report["seconds"] = round(time.monotonic() - started, 2)
        report["processes"] = processed
        report_path = args.report or (
            Path("storage/backfill-reports")
            / f"backfill-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"处理 {processed} 份，耗时 {report['seconds']} 秒；{report_path}")
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
