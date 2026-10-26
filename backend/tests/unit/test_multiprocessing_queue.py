from pathlib import Path
from uuid import uuid4

import pytest
from app.tasks.multiprocessing_queue import run_task_pool, validate_task


def queue_task(marker: Path, suffix: str) -> dict:
    return {
        "job_id": f"job-{suffix}",
        "token": str(uuid4()),
        "document_id": str(uuid4()),
        "knowledge_base_id": str(uuid4()),
        "filename": f"{suffix}.md",
        "sha256": "a" * 64,
        "bucket_name": "documents",
        "object_name": f"{suffix}.md",
        "marker": str(marker),
    }


def test_validate_task_rejects_malformed_messages():
    with pytest.raises(TypeError):
        validate_task([])
    with pytest.raises(ValueError, match="job_id"):
        validate_task({})
    with pytest.raises(TypeError, match="document_id"):
        validate_task(
            {
                "job_id": "1",
                "token": "2",
                "document_id": 3,
                "knowledge_base_id": "4",
                "filename": "x",
                "sha256": "a",
                "bucket_name": "b",
                "object_name": "c",
            }
        )


@pytest.mark.asyncio
async def test_worker_pool_consumes_bounded_queue_tasks(tmp_path):
    marker = tmp_path / "completed.txt"
    jobs = [queue_task(marker, str(index)) for index in range(4)]

    async def claim_task():
        return jobs.pop(0) if jobs else None

    stats = await run_task_pool(
        executor="tests.mp_executor:append_task",
        claim_task=claim_task,
        workers=2,
        poll_seconds=0.02,
        result_timeout=0.02,
        exit_when_idle=True,
    )

    assert stats == {"claimed": 4, "succeeded": 4, "failed": 0, "worker_restarts": 0}
    completed = marker.read_text(encoding="utf-8").splitlines()
    assert {line.split(":", 1)[1] for line in completed} == {
        "job-0",
        "job-1",
        "job-2",
        "job-3",
    }
    assert len({line.split(":", 1)[0] for line in completed}) == 2


@pytest.mark.asyncio
async def test_worker_pool_reports_handler_failure(tmp_path):
    jobs = [queue_task(tmp_path / "marker.txt", "failure")]
    failures = []

    async def claim_task():
        return jobs.pop(0) if jobs else None

    async def failure_handler(task, message):
        failures.append((task["job_id"], message))

    stats = await run_task_pool(
        executor="tests.mp_executor:raise_task",
        claim_task=claim_task,
        failure_handler=failure_handler,
        workers=1,
        poll_seconds=0.02,
        result_timeout=0.02,
        exit_when_idle=True,
    )

    assert stats["failed"] == 1
    assert failures and failures[0][0] == "job-failure"
    assert "RuntimeError" in failures[0][1]


@pytest.mark.asyncio
async def test_worker_pool_survives_transient_claim_error():
    attempts = 0

    async def claim_task():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("injected database outage")
        return None

    stats = await run_task_pool(
        executor="tests.mp_executor:append_task",
        claim_task=claim_task,
        workers=1,
        poll_seconds=0.01,
        result_timeout=0.01,
        exit_when_idle=True,
    )

    assert attempts == 2
    assert stats == {"claimed": 0, "succeeded": 0, "failed": 0, "worker_restarts": 0}
