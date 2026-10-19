import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from fastapi.responses import FileResponse
from minio.error import S3Error
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send
from urllib3.exceptions import HTTPError

from app.models.document import Document
from app.services.storage.minio_storage import get_storage

logger = logging.getLogger(__name__)


class TemporaryFileResponse(FileResponse):
    """响应结束或发送异常时，清理下载临时目录。"""

    def __init__(
        self,
        *,
        temporary_directory: TemporaryDirectory,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.temporary_directory = temporary_directory

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(self.temporary_directory.cleanup)


async def build_download_response(document: Document) -> FileResponse:
    """下载原文件到临时目录，再交给 FastAPI 发送。"""

    downloadable_statuses = {
        "uploaded",
        "parsing",
        "parsed",
        "indexing",
        "ready",
    }

    if document.status not in downloadable_statuses:
        raise HTTPException(
            status_code=409,
            detail="当前文档状态不允许下载",
        )

    storage = get_storage()
    temporary_directory = TemporaryDirectory(prefix="rag-download-")
    local_path = Path(temporary_directory.name) / "original"

    try:
        await run_in_threadpool(
            storage.download_file,
            bucket_name=document.bucket_name,
            object_name=document.object_name,
            file_path=local_path,
        )

        return TemporaryFileResponse(
            temporary_directory=temporary_directory,
            path=local_path,
            filename=document.original_filename,
            media_type="application/octet-stream",
            content_disposition_type="attachment",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except S3Error as exc:
        await run_in_threadpool(temporary_directory.cleanup)

        logger.error(
            "Document download failed: id=%s, code=%s",
            document.id,
            exc.code,
        )

        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            raise HTTPException(
                status_code=409,
                detail="文档记录存在，但原文件缺失，需要核查存储",
            ) from exc

        raise HTTPException(
            status_code=503,
            detail="文件存储暂时不可用",
        ) from exc

    except (HTTPError, OSError) as exc:
        await run_in_threadpool(temporary_directory.cleanup)

        logger.error(
            "Document download failed: id=%s, type=%s",
            document.id,
            type(exc).__name__,
        )

        raise HTTPException(
            status_code=503,
            detail="下载失败，请检查文件存储连接和本地磁盘",
        ) from exc

    except BaseException:
        temporary_directory.cleanup()
        raise
