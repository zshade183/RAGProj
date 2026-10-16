from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目的统一配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    app_name: str
    app_slug: str
    app_env: Literal["development", "test", "production"]
    debug: bool
    app_secret_key: SecretStr
    api_v1_prefix: str

    # PostgreSQL
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str

    # Redis
    redis_url: str

    # MinIO
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: SecretStr
    minio_secure: bool
    minio_bucket_documents: str

    # Milvus
    milvus_uri: str
    milvus_collection_prefix: str

    # 文档解析
    unstructured_api_url: str

    # 大模型
    deepseek_api_key: SecretStr
    deepseek_base_url: str
    deepseek_model: str

    # 联网搜索
    tavily_api_key: SecretStr

    # 本地模型
    embedding_model: str
    embedding_device: Literal["cpu", "cuda"]
    reranker_model: str
    reranker_device: Literal["cpu", "cuda"]

    # 文件
    max_upload_size_mb: int


@lru_cache
def get_settings() -> Settings:
    """读取配置并缓存，避免每次调用都重新读取文件。"""

    return Settings()
