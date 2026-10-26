import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from pymilvus import MilvusClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import check_database_connection
from app.services.storage.minio_storage import get_storage

router = APIRouter(tags=["系统状态"])
settings = get_settings()


class HealthResponse(BaseModel):
    """基本健康检查结果。"""

    status: str
    service: str


class ReadinessResponse(BaseModel):
    """服务依赖检查结果。"""

    status: str
    database: str
    minio: str
    milvus: str
    unstructured: str


def _check_minio() -> None:
    storage = get_storage()
    if not storage.client.bucket_exists(storage.bucket_name):
        raise RuntimeError("对象存储桶不存在")


def _check_milvus() -> None:
    client = MilvusClient(uri=settings.milvus_uri, timeout=5)
    try:
        client.list_collections()
    finally:
        client.close()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """检查 FastAPI 进程是否运行。"""

    return HealthResponse(
        status="ok",
        service=settings.app_slug,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check() -> ReadinessResponse:
    """检查问答所依赖的数据库、对象存储和向量库。"""

    try:
        await check_database_connection()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL 当前不可用",
        ) from exc
    try:
        await run_in_threadpool(_check_minio)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MinIO 当前不可用或桶尚未创建",
        ) from exc
    try:
        await run_in_threadpool(_check_milvus)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Milvus 当前不可用",
        ) from exc

    unstructured = "disabled"
    if settings.rag_use_unstructured:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(settings.unstructured_api_url.rstrip("/") + "/health")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unstructured hi_res 服务不可用",
            ) from exc
        unstructured = "ok"

    return ReadinessResponse(
        status="ready",
        database="ok",
        minio="ok",
        milvus="ok",
        unstructured=unstructured,
    )
