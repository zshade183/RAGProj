from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """管理应用启动和关闭时使用的资源。"""

    yield
    await engine.dispose()


def create_application() -> FastAPI:
    """创建并配置 FastAPI 应用。"""

    application = FastAPI(
        title=settings.app_name,
        description="基于 LangGraph 和 Milvus 的企业文档问答系统",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    application.include_router(
        api_router,
        prefix=settings.api_v1_prefix,
    )

    @application.get("/")
    async def root() -> dict[str, str]:
        return {
            "message": f"{settings.app_name}后端已经启动",
            "docs": "/docs",
        }

    return application


app = create_application()
