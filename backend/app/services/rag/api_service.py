import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from uuid import UUID

from dotenv import dotenv_values

from app.services.embeddings.constants import MODEL_NAME, QUERY_INSTRUCTION
from app.services.rag.answering import AnsweringService
from app.services.retrieval.milvus_store import MilvusStore

PROJECT_ROOT = Path(__file__).resolve().parents[4]

_runtime = None
_runtime_lock = Lock()


class RagBusyError(Exception):
    pass


def load_snapshot(
    document_id: UUID, active_manifest: str | None = None, expected_snapshot: str | None = None
) -> tuple[dict, str]:
    if active_manifest:
        relative_path = active_manifest
    else:
        registry_path = PROJECT_ROOT / "rag_sources.json"
        if not registry_path.is_file():
            raise ValueError("文档尚未完成自动处理")
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        relative_path = registry.get(str(document_id))
        if not relative_path:
            raise ValueError("文档尚未登记可用快照")

    manifest_path = Path(relative_path)
    if not manifest_path.is_absolute():
        manifest_path = PROJECT_ROOT / manifest_path

    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))

    if (
        manifest.get("stage") != "embedded"
        or manifest.get("model_name") != MODEL_NAME
        or manifest.get("dimension") != 1024
        or manifest.get("query_instruction") != QUERY_INSTRUCTION
        or manifest.get("normalized") is not True
        or manifest.get("document_id") != str(document_id)
    ):
        raise ValueError("登记的向量快照与当前文档或模型不兼容。")

    chunks = manifest.get("chunks", [])
    if not chunks or len(chunks) != manifest.get("chunk_count"):
        raise ValueError("向量快照的文本块数量异常。")

    snapshot_id = hashlib.sha256(raw).hexdigest()
    if expected_snapshot and snapshot_id != expected_snapshot:
        raise ValueError("生效快照校验失败")
    return manifest, snapshot_id


class RagRuntime:
    def __init__(self) -> None:
        # 系统环境变量优先于 .env。
        config = {
            **dotenv_values(PROJECT_ROOT / ".env"),
            **os.environ,
        }
        self.llm_config = {
            "base_url": config.get("RAG_LLM_BASE_URL") or "https://api.deepseek.com",
            "api_key": config.get("RAG_LLM_API_KEY") or "",
            "model": config.get("RAG_LLM_MODEL") or "deepseek-chat",
        }

        self.answering = AnsweringService(
            base_url=self.llm_config["base_url"],
            api_key=self.llm_config["api_key"],
            model=self.llm_config["model"],
        )

        try:
            from app.services.embeddings.bge import BgeEmbedding
            from app.services.retrieval.reranker import BgeReranker

            self.embedder = BgeEmbedding(
                cache_dir=PROJECT_ROOT / "storage" / "models",
                device="auto",
                local_files_only=config.get("MODEL_LOCAL_FILES_ONLY", "true").lower()
                in {"1", "true", "yes"},
            )
            self.reranker = BgeReranker(
                cache_dir=PROJECT_ROOT / "storage" / "models",
                device="cpu",
                local_files_only=config.get("MODEL_LOCAL_FILES_ONLY", "true").lower()
                in {"1", "true", "yes"},
            )
        except Exception:
            self.answering.close()
            raise

    def close(self) -> None:
        self.answering.close()

    def ask(
        self,
        manifest,
        snapshot_id,
        question,
        candidate_k,
        top_k,
        min_score,
        mode="adaptive",
        allow_web=False,
        session_id=None,
        snapshots=None,
    ):
        from collections import OrderedDict

        from langgraph.checkpoint.memory import InMemorySaver

        from app.core.config import get_settings
        from app.services.rag.context import expand_context
        from app.services.rag.decisions import Judge, WebSearch
        from app.services.rag.query_planning import looks_compound, merge_query_hits
        from app.services.rag.workflow import build_workflow, initial_state
        from app.services.retrieval.hybrid_store import HYBRID_COLLECTION, HybridStore

        settings = get_settings()
        snapshots = snapshots or [(manifest, snapshot_id)]
        is_hybrid = all(m.get("collection_name") == HYBRID_COLLECTION for m, _ in snapshots)
        if len(snapshots) > 1 and not is_hybrid:
            raise ValueError("跨文档问答需要把全部选中文档迁移到混合索引")
        store = (
            HybridStore(uri=settings.milvus_uri)
            if is_hybrid
            else MilvusStore(uri=settings.milvus_uri)
        )
        scopes = [(m["knowledge_base_id"], m["document_id"], sid) for m, sid in snapshots]
        judge = Judge(self.answering)
        try:
            store.ensure_collection()
            for (m, sid), scope in zip(snapshots, scopes, strict=True):
                if store.count_snapshot(*scope) != m["chunk_count"]:
                    raise RuntimeError("当前快照索引不完整")

            def retrieve(query):
                queries = [query]
                if looks_compound(query):
                    try:
                        queries = judge.decompose(query)
                    except RuntimeError:
                        queries = [query]
                groups = []
                for subquery in queries:
                    vector = self.embedder.encode_query(subquery).tolist()
                    if is_hybrid:
                        hits = store.hybrid_search(subquery, vector, scopes, candidate_k)
                    else:
                        hits = store.search(vector, *scopes[0], limit=candidate_k)
                    ranked = self.reranker.rank(question=subquery, hits=hits, batch_size=2)
                    groups.append(
                        [{**hit, "matched_query": subquery} for hit in ranked]
                    )
                selected, count = merge_query_hits(groups, top_k, min_score)
                return selected, count, queries

            def expand(hits):
                return expand_context(hits, snapshots)

            if mode == "classic":
                hits, count, queries = retrieve(question)
                evidence = expand(hits)
                result = self.answering.answer(question, evidence, subquestions=queries)
                result["status"] = "answered" if evidence else "insufficient_evidence"
                state = {
                    "result": result,
                    "retrieved_count": count,
                    "accepted_count": len(evidence),
                    "trace": [],
                    "queries": queries,
                }
            else:
                # Same session ID cannot share history across KBs, snapshots, modes or web permissions.
                permission = bool(allow_web and settings.rag_web_enabled)
                scope_key = hashlib.sha256(
                    json.dumps([scopes, mode, permission]).encode()
                ).hexdigest()
                thread = scope_key + ":" + str(session_id)
                if not hasattr(self, "memories"):
                    self.memories = OrderedDict()
                if mode in {"graph1", "graph2"}:
                    from app.services.rag.reference_graphs import (
                        build_reference_graph1,
                        build_reference_graph2,
                        make_chat_model,
                    )

                    if not hasattr(self, "reference_llm"):
                        self.reference_llm = make_chat_model(**self.llm_config)
                    if mode == "graph1":
                        from langchain_core.messages import AIMessage, HumanMessage

                        checkpoint = None
                        if session_id:
                            checkpoint = self.memories.pop(thread, None) or InMemorySaver()
                            self.memories[thread] = checkpoint
                        graph = build_reference_graph1(
                            llm=self.reference_llm,
                            retrieve=retrieve,
                            expand=expand,
                            answering=self.answering,
                            judge=judge,
                            max_rewrites=min(max(settings.rag_max_rewrites, 0), 3),
                            checkpointer=checkpoint,
                        )
                        state = graph.invoke(
                            {
                                "messages": [HumanMessage(content=question)],
                                "question": question,
                                "evidence": [],
                                "accepted_count": 0,
                                "retrieved_count": 0,
                                "rewrites": 0,
                                "trace": [],
                                "queries": [],
                            },
                            {
                                "configurable": {"thread_id": thread},
                                "recursion_limit": 40,
                            },
                        )
                        if not state.get("result"):
                            last = state.get("messages", [])[-1]
                            content = (
                                last.content
                                if isinstance(last, AIMessage) and isinstance(last.content, str)
                                else "请明确需要查询的知识问题。"
                            )
                            state["result"] = {
                                "status": "direct",
                                "answer": content.strip(),
                                "citation_ids": [],
                                "sources": [],
                                "citation_check": "not_applicable",
                            }
                        if checkpoint:
                            messages = state.get("messages", [])[-20:]
                            checkpoint.delete_thread(thread)
                            graph.update_state(
                                {"configurable": {"thread_id": thread}},
                                {"messages": messages},
                            )
                    else:
                        if not hasattr(self, "graph2_histories"):
                            self.graph2_histories = OrderedDict()
                        history = self.graph2_histories.pop(thread, [])
                        graph = build_reference_graph2(
                            retrieve=retrieve,
                            expand=expand,
                            answering=self.answering,
                            judge=judge,
                            web=WebSearch(settings.tavily_api_key.get_secret_value()),
                            allow_web=permission,
                            max_rewrites=min(max(settings.rag_max_rewrites, 0), 3),
                            max_generations=min(
                                max(settings.rag_max_generations, 1), 4
                            ),
                        )
                        state = graph.invoke(
                            {
                                "question": question,
                                "query": question,
                                "documents": [],
                                "evidence": [],
                                "accepted_count": 0,
                                "retrieved_count": 0,
                                "generation": "",
                                "result": {},
                                "transform_count": 0,
                                "generations": 0,
                                "allow_web": permission,
                                "web_used": False,
                                "route": "",
                                "trace": [],
                                "queries": [],
                                "history": history,
                            },
                            {"recursion_limit": 50},
                        )
                        self.graph2_histories[thread] = state.get("history", [])[-6:]
                        while len(self.graph2_histories) > 32:
                            self.graph2_histories.popitem(last=False)
                else:
                    checkpoint = None
                    if session_id:
                        checkpoint = self.memories.pop(thread, None) or InMemorySaver()
                        self.memories[thread] = checkpoint
                    graph = build_workflow(
                        retrieve,
                        expand,
                        self.answering,
                        judge,
                        WebSearch(settings.tavily_api_key.get_secret_value()),
                        checkpoint,
                        max_rewrites=min(max(settings.rag_max_rewrites, 0), 3),
                        max_generations=min(max(settings.rag_max_generations, 1), 4),
                    )
                    state = graph.invoke(
                        initial_state(question, mode, permission),
                        {"configurable": {"thread_id": thread}, "recursion_limit": 60},
                    )
                    if checkpoint:
                        # Keep only bounded conversation history; discard evidence-heavy node checkpoints.
                        history = state.get("history", [])
                        checkpoint.delete_thread(thread)
                        graph.update_state(
                            {"configurable": {"thread_id": thread}},
                            {"history": history},
                        )
                    while len(self.memories) > 32:
                        self.memories.popitem(last=False)
            return {
                **state["result"],
                "knowledge_base_id": scopes[0][0],
                "document_id": scopes[0][1] if len(scopes) == 1 else None,
                "snapshot_id": snapshot_id
                if len(scopes) == 1
                else hashlib.sha256(json.dumps(scopes, sort_keys=True).encode()).hexdigest(),
                "retrieved_count": state["retrieved_count"],
                "accepted_count": state["accepted_count"],
                "trace": state["trace"],
                "query_plan": state.get("queries", []),
                "retrieval_mode": "hybrid_rrf" if is_hybrid else "dense_legacy",
                "session_id": session_id,
            }
        finally:
            store.close()


def ask_with_runtime(
    manifest: dict,
    snapshot_id: str,
    question: str,
    candidate_k: int,
    top_k: int,
    min_score: float,
    mode: str = "adaptive",
    allow_web: bool = False,
    session_id=None,
    snapshots=None,
) -> dict:
    global _runtime

    # 本机阶段一次只执行一个 RAG 请求。
    # 不让多个请求同时加载模型或争抢显存。
    if not _runtime_lock.acquire(blocking=False):
        raise RagBusyError("已有问答正在处理，请稍后重试。")

    try:
        if _runtime is None:
            _runtime = RagRuntime()

        return _runtime.ask(
            manifest=manifest,
            snapshot_id=snapshot_id,
            question=question,
            candidate_k=candidate_k,
            top_k=top_k,
            min_score=min_score,
            mode=mode,
            allow_web=allow_web,
            session_id=session_id,
            snapshots=snapshots,
        )
    finally:
        _runtime_lock.release()


def close_runtime() -> None:
    global _runtime

    with _runtime_lock:
        if _runtime is not None:
            _runtime.close()
            _runtime = None
