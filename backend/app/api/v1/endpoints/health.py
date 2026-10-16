from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import check_database_connection

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


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """检查 FastAPI 进程是否运行。"""

    return HealthResponse(
        status="ok",
        service=settings.app_slug,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check() -> ReadinessResponse:
    """检查 FastAPI 和 PostgreSQL 是否都已准备好。"""

    try:
        await check_database_connection()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL 当前不可用",
        ) from exc

    return ReadinessResponse(
        status="ready",
        database="ok",
    )
