from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_database_session
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
)

router = APIRouter(
    prefix="/knowledge-bases",
    tags=["知识库"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_database_session),
]


@router.post(
    "",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: DatabaseSession,
) -> KnowledgeBaseRead:
    """创建一个知识库。"""

    repository = KnowledgeBaseRepository(session)

    try:
        knowledge_base = await repository.create(
            name=payload.name,
            description=payload.description,
        )
        response = KnowledgeBaseRead.model_validate(knowledge_base)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return response


@router.get("", response_model=list[KnowledgeBaseRead])
async def list_knowledge_bases(
    session: DatabaseSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[KnowledgeBaseRead]:
    """分页查询知识库。"""

    repository = KnowledgeBaseRepository(session)
    knowledge_bases = await repository.list(
        offset=offset,
        limit=limit,
    )

    return [KnowledgeBaseRead.model_validate(item) for item in knowledge_bases]


@router.get(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseRead,
)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    session: DatabaseSession,
) -> KnowledgeBaseRead:
    """根据编号查询一个知识库。"""

    repository = KnowledgeBaseRepository(session)
    knowledge_base = await repository.get(knowledge_base_id)

    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在",
        )

    return KnowledgeBaseRead.model_validate(knowledge_base)


# 更新知识库
@router.patch(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseRead,
)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeBaseUpdate,
    session: DatabaseSession,
) -> KnowledgeBaseRead:
    """修改知识库名称或描述。"""

    repository = KnowledgeBaseRepository(session)
    knowledge_base = await repository.get(knowledge_base_id)

    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在",
        )

    changes = payload.model_dump(exclude_unset=True)

    try:
        knowledge_base = await repository.update(
            knowledge_base,
            changes=changes,
        )
        response = KnowledgeBaseRead.model_validate(knowledge_base)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return response


@router.delete(
    "/{knowledge_base_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    session: DatabaseSession,
) -> Response:
    """只允许删除没有文档的知识库。"""

    try:
        statement = (
            select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id).with_for_update()
        )
        knowledge_base = await session.scalar(statement)

        if knowledge_base is None:
            raise HTTPException(
                status_code=404,
                detail="知识库不存在",
            )

        document_id = await session.scalar(
            select(Document.id).where(Document.knowledge_base_id == knowledge_base_id).limit(1)
        )

        if document_id is not None:
            raise HTTPException(
                status_code=409,
                detail="知识库中仍有文档，请先删除文档",
            )

        await session.delete(knowledge_base)
        await session.commit()

    except Exception:
        await session.rollback()
        raise

    return Response(status_code=status.HTTP_204_NO_CONTENT)

#eb02143a-6b9c-428f-a93d-389ad9ee7799