import hashlib
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.schemas.document import DocumentRead
from app.services.storage.minio_storage import get_storage

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
}


def prepare_file(
    source: BinaryIO,
    destination: Path,
    max_bytes: int,
) -> tuple[int, str]:
    """分块保存临时文件，同时检查大小并计算摘要。"""

    source.seek(0)
    size = 0
    digest = hashlib.sha256()

    with destination.open("wb") as output:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)

            if size > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="文件超过配置的大小限制",
                )

            digest.update(chunk)
            output.write(chunk)

    if size == 0:
        raise HTTPException(
            status_code=422,
            detail="不能上传空文件",
        )

    return size, digest.hexdigest()


# 提交数据库变更；失败时回滚并返回可追踪的文档编号。
async def commit_or_report(
    session: AsyncSession,
    document_id: UUID,
) -> None:
    """提交数据库变更；失败时回滚并返回可追踪的文档编号。"""

    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error(
            "Document database commit failed: id=%s, type=%s",
            document_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "message": "数据库提交失败，请先核查文档状态",
                "document_id": str(document_id),
            },
        ) from exc


# 上传文档
async def upload_document(
    *,
    knowledge_base_id: UUID,
    upload: UploadFile,
    session: AsyncSession,
) -> DocumentRead:
    settings = get_settings()

    knowledge_base = await session.get(
        KnowledgeBase,
        knowledge_base_id,
    )
    if knowledge_base is None:
        raise HTTPException(
            status_code=404,
            detail="知识库不存在",
        )

    # 同时处理 Windows 和 Unix 风格路径，只保留文件名。
    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    filename = filename.strip()

    if not filename or len(filename) > 255 or any(ord(character) < 32 for character in filename):
        raise HTTPException(
            status_code=422,
            detail="文件名为空、过长或包含控制字符",
        )

    suffix = Path(filename).suffix.lower()
    if suffix not in CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="当前支持 PDF、DOCX、PPTX、MD、TXT 和 HTML 文件",
        )

    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    if upload.size is not None and upload.size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail="文件超过配置的大小限制",
        )

    storage = get_storage()
    document_id = uuid4()
    object_name = f"{knowledge_base_id}/{document_id}{suffix}"

    with TemporaryDirectory(prefix="rag-upload-") as temp_dir:
        local_path = Path(temp_dir) / f"source{suffix}"

        size, sha256 = await run_in_threadpool(
            prepare_file,
            upload.file,
            local_path,
            max_bytes,
        )

        document = Document(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            original_filename=filename,
            content_type=CONTENT_TYPES[suffix],
            size_bytes=size,
            sha256=sha256,
            bucket_name=storage.bucket_name,
            object_name=object_name,
            status="uploading",
        )

        session.add(document)

        # 先保存记录，保证上传过程中断时仍有线索可查。
        await commit_or_report(session, document_id)

        try:
            await run_in_threadpool(
                storage.upload_file,
                object_name=object_name,
                file_path=local_path,
                content_type=CONTENT_TYPES[suffix],
            )
        except Exception as exc:
            logger.error(
                "Object upload failed: id=%s, type=%s",
                document_id,
                type(exc).__name__,
            )

            document.status = "failed"
            document.error_message = "文件存储上传失败"

            # 使用唯一对象名，只尝试清理本次上传的对象。
            try:
                await run_in_threadpool(
                    storage.delete_file,
                    object_name=object_name,
                )
            # 清理是尽力而为：任何异常都只记录，不能让原始上传错误被覆盖。
            except Exception:  # noqa: BLE001
                document.error_message += "；对象清理未确认，需要核查"
                logger.warning(
                    "Object cleanup unconfirmed: id=%s",
                    document_id,
                )

            await commit_or_report(session, document_id)

            raise HTTPException(
                status_code=503,
                detail={
                    "message": "文件存储不可用，上传未完成",
                    "document_id": str(document_id),
                },
            ) from exc

        document.status = "uploaded"
        document.error_message = None

        await commit_or_report(session, document_id)
        await session.refresh(document)

        return DocumentRead.model_validate(document)
