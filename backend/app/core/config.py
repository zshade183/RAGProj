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
    rag_llm_api_key: SecretStr
    rag_llm_base_url: str
    rag_llm_model: str

    # 联网搜索
    tavily_api_key: SecretStr

    # 本地模型
    embedding_model: str
    embedding_device: Literal["cpu", "cuda"]
    reranker_model: str
    reranker_device: Literal["cpu", "cuda"]
    model_local_files_only: bool = True

    # 持久化处理任务（worker 与 API 使用同一份 .env）
    processing_enabled: bool = True
    worker_poll_seconds: int = 3
    worker_lease_seconds: int = 90
    worker_max_attempts: int = 3
    worker_task_timeout: int = 7200
    worker_embedding_device: Literal["cpu", "cuda", "auto"] = "cpu"
    libreoffice_path: str = r"C:\Program Files\LibreOffice\program\soffice.com"
    tesseract_path: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    ocr_languages: str = "chi_sim+eng"

    rag_use_unstructured: bool = False
    rag_web_enabled: bool = False
    rag_max_rewrites: int = 2
    rag_max_generations: int = 3
    rag_semantic_chunking: bool = True
    rag_semantic_embedding_provider: Literal["bge", "openai"] = "bge"
    rag_semantic_openai_threshold: int = 5000
    openai_embedding_base_url: str = "https://api.openai.com/v1"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_api_key: SecretStr | None = None

    # 文件
    max_upload_size_mb: int


@lru_cache
def get_settings() -> Settings:
    """读取配置并缓存，避免每次调用都重新读取文件。"""

    return Settings()
