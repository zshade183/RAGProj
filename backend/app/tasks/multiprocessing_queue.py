"""Bounded multiprocessing consumers for tasks claimed from PostgreSQL.

The PostgreSQL queue remains the durable source of truth. This module only
moves already-claimed work from the worker supervisor to local child processes
through one bounded ``multiprocessing.Queue`` per consumer.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import multiprocessing
from dataclasses import dataclass
from queue import Empty
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

REQUIRED_TASK_FIELDS = (
    "job_id",
    "token",
    "document_id",
    "knowledge_base_id",
    "filename",
    "sha256",
    "bucket_name",
    "object_name",
)


def validate_task(task: Any) -> dict:
    """Reject malformed queue messages before a child starts a pipeline."""
    if not isinstance(task, dict):
        raise TypeError("队列任务必须是字典")
    missing = [name for name in REQUIRED_TASK_FIELDS if not task.get(name)]
    if missing:
        raise ValueError("队列任务缺少字段：" + ", ".join(missing))
    for name in REQUIRED_TASK_FIELDS:
        if not isinstance(task[name], str):
            raise TypeError(f"队列任务字段必须是字符串：{name}")
    return task


def load_executor(path: str) -> Callable[[dict], Any]:
    """Load ``module:callable`` by name so callbacks serialize on spawn platforms."""
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("executor 必须是 module:callable")
    target = importlib.import_module(module_name)
    for part in attribute.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError("executor 不是可调用对象")
    return target


def _consumer_main(task_queue, result_queue, worker_id, executor_path):
    """Consume one task at a time in a dedicated process and event loop."""
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s mp-worker-{worker_id} %(message)s",
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        executor = load_executor(executor_path)
        while True:
            try:
                task = task_queue.get(timeout=0.5)
            except Empty:
                continue
            if task is None:
                break

            try:
                validate_task(task)
            except (TypeError, ValueError) as exc:
                result_queue.put(
                    {
                        "worker_id": worker_id,
                        "job_id": task.get("job_id") if isinstance(task, dict) else "",
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                continue

            try:
                result = executor(task)
                if inspect.isawaitable(result):
                    loop.run_until_complete(result)
            except BaseException as exc:
                logger.exception("Queue consumer failed job=%s", task["job_id"])
                result_queue.put(
                    {
                        "worker_id": worker_id,
                        "job_id": task["job_id"],
                        "status": "failed",
                        "error": type(exc).__name__,
                    }
                )
            else:
                result_queue.put(
                    {
                        "worker_id": worker_id,
                        "job_id": task["job_id"],
                        "status": "succeeded",
                        "error": None,
                    }
                )
    finally:
        try:
            from app.db.session import engine

            loop.run_until_complete(engine.dispose())
        except Exception:
            logger.exception("Queue consumer could not dispose database engine")
        finally:
            loop.close()
            asyncio.set_event_loop(None)


@dataclass
class _Worker:
    worker_id: int
    process: multiprocessing.Process
    task_queue: Any
    task: dict | None = None


async def _call_failure_handler(
    failure_handler: Callable[[dict, str], Awaitable[Any]] | None,
    task: dict,
    message: str,
) -> None:
    if failure_handler is None:
        return
    try:
        await failure_handler(task, message)
    except Exception:
        logger.exception("Parent could not report queue task failure job=%s", task["job_id"])


async def run_task_pool(
    *,
    executor: str,
    claim_task: Callable[[], Awaitable[dict | None]],
    failure_handler: Callable[[dict, str], Awaitable[Any]] | None = None,
    workers: int = 1,
    poll_seconds: float = 1.0,
    result_timeout: float = 0.25,
    exit_when_idle: bool = False,
) -> dict[str, int]:
    """Fan out durable queue claims to bounded multiprocessing consumers.

    The supervisor claims at most one task per idle child. Every child has a
    one-slot queue, so claiming stops before local work can outrun its lease.
    A dead child's in-flight task is handed to ``failure_handler`` so the
    existing database lease/retry semantics can recover it.
    """
    if workers < 1:
        raise ValueError("workers 必须大于 0")
    if poll_seconds <= 0 or result_timeout <= 0:
        raise ValueError("轮询和结果等待时间必须大于 0")

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    pending: dict[str, dict] = {}
    handles: dict[int, _Worker] = {}
    stats = {"claimed": 0, "succeeded": 0, "failed": 0, "worker_restarts": 0}

    def start_worker(worker_id: int) -> None:
        task_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_consumer_main,
            args=(task_queue, result_queue, worker_id, executor),
            name=f"rag-ingest-{worker_id}",
        )
        process.start()
        handles[worker_id] = _Worker(worker_id, process, task_queue)

    def stop_worker(worker: _Worker) -> None:
        if worker.process.is_alive():
            try:
                worker.task_queue.put(None, timeout=0.25)
            except Exception:
                pass
            worker.process.join(timeout=5)
        if worker.process.is_alive():
            worker.process.terminate()
            worker.process.join(timeout=2)
        worker.task_queue.close()
        worker.task_queue.join_thread()

    async def handle_result(result: dict) -> None:
        worker_id = int(result["worker_id"])
        worker = handles.get(worker_id)
        if worker is None:
            return
        task = worker.task
        if task is None or task["job_id"] != result["job_id"]:
            logger.warning("Ignoring stale queue result worker=%s job=%s", worker_id, result["job_id"])
            return
        worker.task = None
        pending.pop(task["job_id"], None)
        if result["status"] == "succeeded":
            stats["succeeded"] += 1
            return
        stats["failed"] += 1
        await _call_failure_handler(
            failure_handler,
            task,
            f"多进程处理失败（{result.get('error') or 'UnknownError'}）",
        )

    try:
        for worker_id in range(workers):
            start_worker(worker_id)

        while True:
            claimed_none = False
            while True:
                try:
                    result = result_queue.get_nowait()
                except Empty:
                    break
                await handle_result(result)

            for worker_id, worker in list(handles.items()):
                if worker.process.is_alive():
                    continue
                if worker.task is not None:
                    task = worker.task
                    pending.pop(task["job_id"], None)
                    stats["failed"] += 1
                    await _call_failure_handler(
                        failure_handler,
                        task,
                        "多进程消费者异常退出；任务将按租约规则重试",
                    )
                stop_worker(worker)
                start_worker(worker_id)
                stats["worker_restarts"] += 1

            for worker in handles.values():
                if worker.task is not None:
                    continue
                try:
                    task = await claim_task()
                except Exception:
                    logger.exception("Could not claim a durable queue task")
                    await asyncio.sleep(poll_seconds)
                    break
                if task is None:
                    claimed_none = True
                    break
                validate_task(task)
                worker.task = task
                pending[task["job_id"]] = task
                stats["claimed"] += 1
                try:
                    await asyncio.to_thread(worker.task_queue.put, task, True, 1.0)
                except Exception as exc:
                    worker.task = None
                    pending.pop(task["job_id"], None)
                    stats["failed"] += 1
                    await _call_failure_handler(
                        failure_handler,
                        task,
                        f"任务无法进入多进程队列（{type(exc).__name__}）",
                    )

            if exit_when_idle and claimed_none and not pending:
                break

            timeout = result_timeout if pending else min(poll_seconds, result_timeout)
            try:
                result = await asyncio.to_thread(result_queue.get, True, timeout)
            except Empty:
                continue
            await handle_result(result)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Stopping multiprocessing ingestion pool")
    finally:
        for worker in list(handles.values()):
            stop_worker(worker)
        result_queue.close()
        result_queue.join_thread()

    return stats
