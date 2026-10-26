"""Knowledge-base search over active snapshots only (no history or deleted documents)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.api.v1.endpoints.rag import DatabaseSession, logger
from app.models.document import Document
from app.schemas.rag import RagQuestion, RagResponse
from app.services.rag.api_service import RagBusyError, ask_with_runtime, load_snapshot
from app.services.retrieval.hybrid_store import HYBRID_COLLECTION

router = APIRouter(tags=["RAG 问答"])


@router.post("/knowledge-bases/{knowledge_base_id}/ask", response_model=RagResponse)
async def ask_knowledge_base(knowledge_base_id: UUID, body: RagQuestion, session: DatabaseSession):
    if body.top_k > body.candidate_k:
        raise HTTPException(422, "top_k 不能大于 candidate_k")
    rows = (
        (
            await session.execute(
                select(Document)
                .where(
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.status != "deleting",
                    Document.active_manifest.is_not(None),
                )
                .order_by(Document.id)
            )
        )
        .scalars()
        .all()
    )
    entries = [(d.id, d.active_manifest, d.active_snapshot_id, d.sha256) for d in rows]
    await session.rollback()
    if not entries:
        raise HTTPException(409, "知识库尚无已发布快照")
    try:
        snapshots = []
        for doc_id, path, sid, sha in entries:
            m, actual = await run_in_threadpool(load_snapshot, doc_id, path, sid)
            if (
                m["knowledge_base_id"] != str(knowledge_base_id)
                or m["source_sha256"] != sha
                or m.get("collection_name") != HYBRID_COLLECTION
            ):
                raise ValueError("需要迁移混合索引")
            snapshots.append((m, actual))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, "存在不可用或旧版快照，请先运行混合索引迁移") from exc
    try:
        return await run_in_threadpool(
            ask_with_runtime,
            snapshots[0][0],
            snapshots[0][1],
            body.question,
            body.candidate_k,
            body.top_k,
            body.min_score,
            body.mode,
            body.allow_web,
            body.session_id,
            snapshots,
        )
    except RagBusyError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc
    except Exception as exc:
        logger.exception("Knowledge RAG failed: %s", type(exc).__name__)
        raise HTTPException(503, "知识库问答暂时不可用，请检查服务日志") from exc
