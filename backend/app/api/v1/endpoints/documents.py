from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_database_session
from app.models.knowledge_base import KnowledgeBase
from app.repositories.document import DocumentRepository
from app.schemas.document import DocumentRead
from app.services.documents.download import build_download_response
from app.services.documents.upload import upload_document

router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/documents",
    tags=["文档"],
)

DatabaseSession = Annotated[
    AsyncSession,
    Depends(get_database_session),
]


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    knowledge_base_id: UUID,
    session: DatabaseSession,
    file: Annotated[UploadFile, File(description="需要上传的文档")],
) -> DocumentRead:
    """上传原始文件，并保存文档信息。"""

    try:
        return await upload_document(
            knowledge_base_id=knowledge_base_id,
            upload=file,
            session=session,
        )
    finally:
        await file.close()


@router.get("", response_model=list[DocumentRead])
async def list_documents(
    knowledge_base_id: UUID,
    session: DatabaseSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[DocumentRead]:
    """查询指定知识库中的文档。"""

    knowledge_base = await session.get(
        KnowledgeBase,
        knowledge_base_id,
    )
    if knowledge_base is None:
        raise HTTPException(
            status_code=404,
            detail="知识库不存在",
        )

    repository = DocumentRepository(session)
    documents = await repository.list(
        knowledge_base_id=knowledge_base_id,
        offset=offset,
        limit=limit,
    )

    return [DocumentRead.model_validate(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
) -> DocumentRead:
    """查询文档详情，同时检查所属知识库。"""

    repository = DocumentRepository(session)
    document = await repository.get(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在或不属于该知识库",
        )

    return DocumentRead.model_validate(document)


# 下载文档
@router.get(
    "/{document_id}/download",
    response_class=FileResponse,
)
async def download_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    session: DatabaseSession,
) -> FileResponse:
    """下载文档原文件。"""

    repository = DocumentRepository(session)
    document = await repository.get(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在或不属于该知识库",
        )

    return await build_download_response(document)
