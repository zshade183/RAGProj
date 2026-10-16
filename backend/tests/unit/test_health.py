from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_root() -> None:
    """根接口应该返回启动信息和接口文档地址。"""

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "RAG 企业知识库后端已经启动",
        "docs": "/docs",
    }


def test_health_check() -> None:
    """健康检查接口应该报告服务正常。"""

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "rag-enterprise-knowledge-base",
    }
