from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeBaseCreate(BaseModel):
    """创建知识库时，用户需要提交的数据。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)


class KnowledgeBaseRead(BaseModel):
    """返回给用户的知识库信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseUpdate(BaseModel):
    """只修改请求中明确提供的字段。"""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_update(self) -> "KnowledgeBaseUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")

        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null")

        return self
