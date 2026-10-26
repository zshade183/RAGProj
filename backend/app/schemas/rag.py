from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# RAG 请求参数
class RagQuestion(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    question: str = Field(min_length=1, max_length=1000)
    candidate_k: int = Field(default=20, ge=1, le=50)
    top_k: int = Field(default=5, ge=1, le=10)
    mode: Literal["classic", "agent", "adaptive", "graph1", "graph2"] = "adaptive"
    allow_web: bool = False
    session_id: UUID | None = None
    min_score: float = Field(default=0.3, ge=0, le=1)


# RAG 响应参数
class RagSource(BaseModel):
    source_id: str
    chunk_id: str
    filename: str
    page_number: int | None
    score: float | None
    text: str
    retrieval_kind: str = "retrieved"
    url: str | None = None
    document_id: UUID | None = None


# RAG 响应参数
class RagResponse(BaseModel):
    knowledge_base_id: UUID
    document_id: UUID | None
    snapshot_id: str
    status: Literal["answered", "insufficient_evidence", "direct"]
    answer: str
    citation_ids: list[str]
    citation_check: str
    sources: list[RagSource]
    retrieved_count: int
    accepted_count: int
    trace: list[dict] = Field(default_factory=list)
    retrieval_mode: str = "dense_legacy"
    session_id: UUID | None = None
    query_plan: list[str] = Field(default_factory=list)
