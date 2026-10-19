import logging
from uuid import UUID

from fastapi import HTTPException
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.document import Document
from app.services.storage.minio_storage import get_storage

logger = logging.getLogger(__name__)


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

        # 当前只允许清理尚未建立解析产物和向量的文档。
        if document.status not in {"uploaded", "failed", "deleting"}:
            raise HTTPException(
                status_code=409,
                detail="当前文档状态不允许删除",
            )

        document.status = "deleting"
        await session.commit()

        # 再次锁定，防止两个删除请求同时处理同一条记录。
        document = await session.scalar(statement)

        if document is None:
            # 另一个请求已经完成删除，本次目标也已达成。
            await session.rollback()
            return

        if document.status != "deleting":
            raise HTTPException(
                status_code=409,
                detail="文档状态已变化，请重新查询",
            )

        storage = get_storage()

        try:
            await run_in_threadpool(
                storage.delete_file,
                bucket_name=document.bucket_name,
                object_name=document.object_name,
            )
        except S3Error as exc:
            # 桶或对象已经不存在时，原文件清理目标已经达成。
            if exc.code not in {"NoSuchKey", "NoSuchBucket"}:
                raise

        await session.delete(document)
        await session.commit()

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
            "Document delete storage error: id=%s, type=%s",
            document_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="文件清理未完成，已保留删除状态，请稍后重试",
        ) from exc
