from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase


class KnowledgeBaseRepository:
    """负责知识库表的数据库操作。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        name: str,
        description: str,
    ) -> KnowledgeBase:
        knowledge_base = KnowledgeBase(
            name=name,
            description=description,
        )

        self.session.add(knowledge_base)
        await self.session.flush()
        await self.session.refresh(knowledge_base)

        return knowledge_base

    async def list(
        self,
        *,
        offset: int,
        limit: int,
    ) -> list[KnowledgeBase]:
        statement = (
            select(KnowledgeBase)
            .order_by(
                KnowledgeBase.created_at.desc(),
                KnowledgeBase.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await self.session.scalars(statement)
        return list(result)

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return await self.session.get(KnowledgeBase, knowledge_base_id)

    # 更新知识库
    async def update(
        self,
        knowledge_base: KnowledgeBase,
        *,
        changes: dict[str, str],
    ) -> KnowledgeBase:
        for field_name, value in changes.items():
            # 更新字段值
            setattr(knowledge_base, field_name, value)

        await self.session.flush()
        await self.session.refresh(knowledge_base)

        return knowledge_base

    # 删除知识库
    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        await self.session.delete(knowledge_base)
        await self.session.flush()
