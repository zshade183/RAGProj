import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_database_session
from app.models.document import Document
from app.schemas.rag import RagQuestion, RagResponse
from app.services.rag.api_service import (
    RagBusyError,
    ask_with_runtime,
    load_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/documents",
    tags=["RAG 问答"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_database_session),
]


@router.post(
    "/{document_id}/ask",
    response_model=RagResponse,
)
async def ask_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    body: RagQuestion,
    session: DatabaseSession,
) -> dict:
    if body.top_k > body.candidate_k:
        raise HTTPException(
            status_code=422,
            detail="top_k 不能大于 candidate_k。",
        )

    document = await session.get(Document, document_id)

    if document is None or document.knowledge_base_id != knowledge_base_id:
        raise HTTPException(
            status_code=404,
            detail="文档不存在或不属于该知识库。",
        )

    if document.status == "deleting":
        raise HTTPException(
            status_code=409,
            detail="文档正在删除，暂时不能问答。",
        )

    source_sha256 = document.sha256
    active_manifest = document.active_manifest
    expected_snapshot = document.active_snapshot_id

    # 释放读事务，不在模型生成期间占用数据库连接。
    await session.rollback()

    try:
        manifest, snapshot_id = await run_in_threadpool(
            load_snapshot,
            document_id,
            active_manifest,
            expected_snapshot,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "RAG snapshot unavailable: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=409,
            detail="文档尚无可用快照，请检查服务器快照登记和入库结果。",
        ) from exc

    if (
        manifest["knowledge_base_id"] != str(knowledge_base_id)
        or manifest["source_sha256"] != source_sha256
    ):
        raise HTTPException(
            status_code=409,
            detail="向量快照与数据库中的文档不一致。",
        )

    try:
        return await run_in_threadpool(
            ask_with_runtime,
            manifest,
            snapshot_id,
            body.question,
            body.candidate_k,
            body.top_k,
            body.min_score,
            body.mode, body.allow_web, body.session_id,
        )
    except RagBusyError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "5"},
        ) from exc
    except Exception as exc:
        # 不把密钥、完整请求或内部文件路径返回给客户端。
        logger.exception(
            "RAG request failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail=("问答暂时不可用，请检查模型加载、Milvus 和大模型服务配置。"),
        ) from exc
