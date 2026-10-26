from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    """返回给客户端的文档信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
