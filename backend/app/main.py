from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import engine
from app.services.rag.api_service import close_runtime

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """管理应用启动和关闭时使用的资源。"""

    yield
    # 关闭 RAG 运行时：同步函数，放进线程池避免阻塞事件循环。
    await run_in_threadpool(close_runtime)
    # 关闭数据库连接池
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

    # backend/app/main.py -> parents[0]=app, [1]=backend, [2]=项目根目录
    project_root = Path(__file__).resolve().parents[2]

    application.mount(
        "/ui",
        StaticFiles(
            directory=str(project_root / "frontend"),
            html=True,
        ),
        name="frontend",
    )

    return application


app = create_application()
