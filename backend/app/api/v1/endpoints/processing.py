from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_database_session
from app.models import Document, ProcessingJob
from app.tasks.queue import enqueue

router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/documents", tags=["处理任务"])
Session = Annotated[AsyncSession, Depends(get_database_session)]


def job_view(doc, job):
    return {
        "document_id": str(doc.id),
        "document_status": doc.status,
        "has_active_snapshot": bool(doc.active_snapshot_id),
        "job_id": str(job.id) if job else None,
        "status": job.status if job else "not_scheduled",
        "stage": job.stage if job else None,
        "attempts": job.attempts if job else 0,
        "error": job.error if job else doc.error_message,
        "updated_at": job.updated_at if job else doc.updated_at,
        "lease_until": job.lease_until if job else None,
    }


@router.get("/{document_id}/processing")
async def processing_status(knowledge_base_id: UUID, document_id: UUID, session: Session):
    doc = await session.scalar(
        select(Document).where(
            Document.id == document_id, Document.knowledge_base_id == knowledge_base_id
        )
    )
    if doc is None:
        raise HTTPException(404, "文档不存在")
    job = await session.scalar(
        select(ProcessingJob).where(ProcessingJob.document_id == document_id)
    )
    return job_view(doc, job)


@router.post("/{document_id}/process", status_code=202)
async def process_document(knowledge_base_id: UUID, document_id: UUID, session: Session):
    doc = await session.scalar(
        select(Document)
        .where(Document.id == document_id, Document.knowledge_base_id == knowledge_base_id)
        .with_for_update()
    )
    if doc is None:
        raise HTTPException(404, "文档不存在")
    job = await enqueue(session, doc)
    await session.commit()
    await session.refresh(job)
    return job_view(doc, job)
