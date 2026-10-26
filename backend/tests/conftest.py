"""Non-secret defaults only when a developer has no .env (CI/source review)."""

import os
from pathlib import Path

if not Path(".env").exists():
    defaults = {
        "APP_NAME": "RAG 企业知识库",
        "APP_SLUG": "rag-enterprise-knowledge-base",
        "APP_ENV": "test",
        "DEBUG": "false",
        "APP_SECRET_KEY": "test-only",
        "API_V1_PREFIX": "/api/v1",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "POSTGRES_DB": "rag_test",
        "REDIS_URL": "redis://localhost:6379/0",
        "MINIO_ENDPOINT": "localhost:9000",
        "MINIO_ACCESS_KEY": "test",
        "MINIO_SECRET_KEY": "test-only",
        "MINIO_SECURE": "false",
        "MINIO_BUCKET_DOCUMENTS": "documents",
        "MILVUS_URI": "http://localhost:19530",
        "MILVUS_COLLECTION_PREFIX": "rag",
        "UNSTRUCTURED_API_URL": "http://localhost:8001",
        "RAG_LLM_API_KEY": "test-only",
        "RAG_LLM_BASE_URL": "http://localhost:9999",
        "RAG_LLM_MODEL": "test",
        "TAVILY_API_KEY": "",
        "EMBEDDING_MODEL": "BAAI/bge-large-zh-v1.5",
        "EMBEDDING_DEVICE": "cpu",
        "RERANKER_MODEL": "BAAI/bge-reranker-v2-m3",
        "RERANKER_DEVICE": "cpu",
        "MAX_UPLOAD_SIZE_MB": "100",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
