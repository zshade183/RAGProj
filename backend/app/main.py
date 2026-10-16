from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """健康检查接口的返回数据结构。"""

    status: str
    service: str


app = FastAPI(
    title="RAG 企业知识库",
    description="基于 LangGraph 和 Milvus 的企业文档问答系统",
    version="0.1.0",
)


@app.get("/")
async def root() -> dict[str, str]:
    """项目根接口。"""

    return {
        "message": "RAG 企业知识库后端已经启动",
        "docs": "/docs",
    }


@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """检查后端服务是否正常运行。"""

    return HealthResponse(
        status="ok",
        service="rag-enterprise-knowledge-base",
    )
