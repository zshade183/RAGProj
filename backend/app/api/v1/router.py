from fastapi import APIRouter

from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.knowledge_bases import router as knowledge_bases_router
from app.api.v1.endpoints.knowledge_rag import router as knowledge_rag_router
from app.api.v1.endpoints.processing import router as processing_router
from app.api.v1.endpoints.rag import router as rag_router

api_router = APIRouter()
api_router.include_router(knowledge_rag_router)
api_router.include_router(processing_router)

api_router.include_router(health_router)
api_router.include_router(knowledge_bases_router)
api_router.include_router(documents_router)
api_router.include_router(rag_router)
