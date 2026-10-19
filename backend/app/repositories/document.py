from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document


class DocumentRepository:
    """文档元数据查询。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self,
        *,
        knowledge_base_id: UUID,
        offset: int,
        limit: int,
    ) -> list[Document]:
        statement = (
            select(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await self.session.scalars(statement)
        return list(result)

    async def get(
        self,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
    ) -> Document | None:
        statement = select(Document).where(
            Document.id == document_id,
            Document.knowledge_base_id == knowledge_base_id,
        )

        return await self.session.scalar(statement)
