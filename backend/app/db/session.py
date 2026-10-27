from collections.abc import AsyncGenerator

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
'''
数据库会话管理模块。
'''

from app.core.config import get_settings

# 数据库连接配置
settings = get_settings()

# 数据库连接 URL
database_url = URL.create(
    drivername="postgresql+asyncpg",
    username=settings.postgres_user,
    password=settings.postgres_password.get_secret_value(),
    host=settings.postgres_host,
    port=settings.postgres_port,
    database=settings.postgres_db,
)
# 数据库引擎
engine = create_async_engine(
    database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)
# 数据库会话工厂
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_database_session() -> AsyncGenerator[AsyncSession, None]:
    """为一次 API 请求提供独立的数据库会话。"""

    async with async_session_factory() as session:
        yield session


async def check_database_connection() -> None:
    """执行简单查询，确认 PostgreSQL 可以访问。"""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
