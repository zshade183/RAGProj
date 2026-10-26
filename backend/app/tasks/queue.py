"""PostgreSQL queue. All multi-row mutations lock document BEFORE job."""

from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models import Document, ProcessingJob


async def enqueue(session, document):
    """Caller owns document lock (or just created it); caller commits atomically."""
    if document.status in {"uploading", "deleting"}:
        raise HTTPException(409, "当前文档不能处理")
    job = await session.scalar(
        select(ProcessingJob).where(ProcessingJob.document_id == document.id).with_for_update()
    )
    if job and job.status in {"queued", "running"}:
        return job
    if job is None:
        job = ProcessingJob(document_id=document.id)
        session.add(job)
    else:
        job.status, job.stage, job.attempts = "queued", "queued", 0
        job.token = job.lease_until = job.error = None
        job.available_at = func.now()
    # Existing active snapshot remains usable during reprocessing.
    document.status = "uploaded"
    document.error_message = None
    await session.flush()
    return job


async def claim():
    settings = get_settings()
    async with async_session_factory() as session, session.begin():
        due = or_(
            and_(ProcessingJob.status == "queued", ProcessingJob.available_at <= func.now()),
            and_(ProcessingJob.status == "running", ProcessingJob.lease_until < func.now()),
        )
        # Lock only the document first; SKIP LOCKED allows independent workers.
        doc = await session.scalar(
            select(Document)
            .join(ProcessingJob, ProcessingJob.document_id == Document.id)
            .where(due, Document.status != "deleting")
            .order_by(ProcessingJob.available_at, ProcessingJob.id)
            .with_for_update(of=Document, skip_locked=True)
            .limit(1)
        )
        if doc is None:
            return None
        job = await session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == doc.id).with_for_update()
        )
        now = await session.scalar(select(func.now()))
        eligible = (job.status == "queued" and job.available_at <= now) or (
            job.status == "running" and job.lease_until is not None and job.lease_until < now
        )
        if not eligible:
            return None
        if job.attempts >= settings.worker_max_attempts:
            job.status = doc.status = "failed"
            job.error = doc.error_message = "重试次数已用完；修复原因后手动重试"
            job.token = job.lease_until = None
            return None
        job.attempts += 1
        job.status, job.stage = "running", "download"
        job.token = uuid4()
        job.lease_until = func.now() + timedelta(seconds=settings.worker_lease_seconds)
        job.error = None
        doc.status, doc.error_message = "parsing", None
        return {
            "job_id": str(job.id),
            "token": str(job.token),
            "document_id": str(doc.id),
            "knowledge_base_id": str(doc.knowledge_base_id),
            "filename": doc.original_filename,
            "sha256": doc.sha256,
            "bucket_name": doc.bucket_name,
            "object_name": doc.object_name,
            "attempt": job.attempts,
        }


async def lock_owned(session, task):
    from uuid import UUID

    doc = await session.scalar(
        select(Document).where(Document.id == UUID(task["document_id"])).with_for_update()
    )
    job = await session.scalar(
        select(ProcessingJob).where(ProcessingJob.id == UUID(task["job_id"])).with_for_update()
    )
    if doc is None or job is None or doc.status == "deleting":
        return None, None
    if job.status != "running" or str(job.token) != task["token"]:
        return None, None
    # A dead/paused process may not resurrect its expired lease.
    now = await session.scalar(select(func.now()))
    if job.lease_until is None or job.lease_until <= now:
        return None, None
    return doc, job


async def heartbeat(task, stage):
    async with async_session_factory() as session, session.begin():
        doc, job = await lock_owned(session, task)
        if job is None:
            return False
        job.stage = stage[:40]
        job.lease_until = func.now() + timedelta(seconds=get_settings().worker_lease_seconds)
        doc.status = (
            "indexing"
            if stage in {"embedding", "indexing", "publish"}
            else ("parsed" if stage == "parsed" else "parsing")
        )
        return True


async def finish(task, result=None, error=None):
    async with async_session_factory() as session, session.begin():
        doc, job = await lock_owned(session, task)
        if job is None:
            return False
        if result is not None:
            doc.active_manifest = result["manifest_path"]
            doc.active_snapshot_id = result["snapshot_id"]
            doc.status, doc.error_message = "ready", None
            job.status, job.stage, job.error = "succeeded", "ready", None
        else:
            message = (error or "处理失败")[:1500]
            job.error = doc.error_message = message
            if job.attempts < get_settings().worker_max_attempts:
                job.status = "queued"
                job.available_at = func.now() + timedelta(seconds=30 * job.attempts)
                doc.status = "uploaded"
            else:
                job.status = doc.status = "failed"
        job.token = job.lease_until = None
        return True
