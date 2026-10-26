import asyncio
import logging
import shutil
from uuid import UUID

from fastapi import HTTPException
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.document import Document
from app.services.retrieval.hybrid_store import HybridStore
from app.services.retrieval.milvus_store import MilvusStore
from app.services.storage.minio_storage import get_storage

logger = logging.getLogger(__name__)


def _delete_vectors(document_id: UUID) -> int:
    """Purge both collection generations; reads are already blocked by the deleting state."""

    deleted = 0
    for store_type in (HybridStore, MilvusStore):
        store = store_type()
        try:
            deleted += store.delete_document(str(document_id))
        finally:
            store.close()
    return deleted


def _delete_objects(document: Document) -> int:
    storage = get_storage()
    try:
        storage.delete_file(
            bucket_name=document.bucket_name,
            object_name=document.object_name,
        )
    except S3Error as exc:
        if exc.code not in {"NoSuchKey", "NoSuchBucket"}:
            raise
    return storage.delete_prefix(
        bucket_name=document.bucket_name,
        prefix=f"artifacts/{document.id}/",
    )


def _delete_attempt_artifacts(document_id: UUID) -> None:
    from app.tasks.pipeline import ROOT

    processed = (ROOT / "storage" / "processed").resolve()
    target = (processed / str(document_id)).resolve()
    if target.parent != processed or target.name != str(document_id):
        raise ValueError("本地处理产物路径不安全")
    if target.exists():
        shutil.rmtree(target)


async def delete_document(
    *,
    knowledge_base_id: UUID,
    document_id: UUID,
    session: AsyncSession,
) -> None:
    statement = (
        select(Document)
        .where(
            Document.id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
        )
        # 锁定文档，防止其他请求同时处理同一条记录。
        .with_for_update()
        # 从数据库中获取文档，确保其他请求无法同时处理同一条记录。
        .execution_options(populate_existing=True)
    )

    try:
        document = await session.scalar(statement)

        if document is None:
            raise HTTPException(
                status_code=404,
                detail="文档不存在或不属于该知识库",
            )

        from app.models import ProcessingJob

        job = await session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id).with_for_update()
        )
        if job is not None:
            # Removing the fencing row makes every in-flight attempt unable to publish.
            await session.delete(job)
        document.status = "deleting"
        document.error_message = "正在清理原文件、索引与处理产物"
        await session.commit()

        # Leave one heartbeat cycle for a live worker child to observe the cancellation.
        await asyncio.sleep(1.5)

        # Keep a retryable tombstone until every external store has been cleaned.
        document = await session.scalar(statement)

        if document is None:
            await session.rollback()
            return

        if document.status != "deleting":
            raise HTTPException(
                status_code=409,
                detail="文档状态已变化，请重新查询",
            )

        deleted_objects = await run_in_threadpool(_delete_objects, document)
        deleted_vectors = await run_in_threadpool(_delete_vectors, document.id)
        logger.info(
            "Document cleanup: id=%s objects=%s vectors=%s",
            document_id,
            deleted_objects,
            deleted_vectors,
        )

        job = await session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == document_id).with_for_update()
        )
        if job is not None:
            await session.delete(job)
        await session.delete(document)
        await session.commit()
        await run_in_threadpool(_delete_attempt_artifacts, document_id)

    except HTTPException:
        await session.rollback()
        raise

    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error(
            "Document delete database error: id=%s, type=%s",
            document_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="数据库操作未确认完成，请查询文档状态后重试",
        ) from exc

    except Exception as exc:
        await session.rollback()
        logger.error(
            "Document delete cleanup error: id=%s, type=%s",
            document_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="文件或索引清理未完成，文档已停止召回；请稍后重试删除",
        ) from exc
