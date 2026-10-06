"""The user's uploaded files (「我的檔案」): list, download, delete, and promote to the knowledge base."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers

from app.api.routes.chat import get_chat_attachment_storage, get_chat_repository
from app.api.routes.documents import (
    get_document_repository,
    get_document_storage,
    get_ingestion_task,
    ingest_upload,
)
from app.models.chat import ChatAttachmentKind, ChatAttachmentStatus, UserFile, UserFileRead
from app.models.documents import DocumentCategory
from app.services.chat.attachments import AttachmentError, ChatAttachmentStorage
from app.services.chat.repository import ChatRepository
from app.services.documents.repository import DocumentRepository
from app.services.documents.storage import DocumentStorage

router = APIRouter(prefix="/files", tags=["files"])

PROMOTABLE_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})


class PromoteFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: DocumentCategory
    folder_id: UUID | None = None


def _file_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此檔案。")


async def _as_file_read(file: UserFile, repository: ChatRepository) -> UserFileRead:
    title = None
    if file.origin_session_id is not None:
        session = await repository.get_session(file.origin_session_id)
        title = session.title if session is not None else None
    return UserFileRead(
        id=file.id,
        display_name=file.display_name,
        mime_type=file.mime_type,
        kind=file.kind,
        size_bytes=file.size_bytes,
        status=file.status,
        error=file.error,
        created_at=file.created_at,
        origin_session_id=file.origin_session_id,
        origin_session_title=title,
        in_knowledge_base=file.promoted_document_id is not None,
    )


@router.get("", response_model=list[UserFileRead])
async def list_files(
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> list[UserFileRead]:
    return [await _as_file_read(file, repository) for file in await repository.list_files()]


@router.get("/{file_id}/content")
async def get_file_content(
    file_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> FileResponse:
    file = await repository.get_file(file_id)
    if file is None:
        raise _file_not_found()
    try:
        path = storage.resolve(file.storage_key)
    except AttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FileResponse(path, media_type=file.mime_type, filename=file.display_name)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> None:
    file = await repository.get_file(file_id)
    if file is None:
        raise _file_not_found()
    try:
        await storage.delete_file(file.storage_key)
    except AttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    # Links go with the file; earlier messages keep their text.
    await repository.delete_file(file_id)


@router.post("/{file_id}/promote", status_code=status.HTTP_202_ACCEPTED, response_model=UserFileRead)
async def promote_file(
    file_id: UUID,
    payload: PromoteFileRequest,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
    document_repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    document_storage: Annotated[DocumentStorage, Depends(get_document_storage)],
    task: Annotated[Any, Depends(get_ingestion_task)],
) -> UserFileRead:
    """Copy a user file into the knowledge base through the normal ingestion path."""

    file = await repository.get_file(file_id)
    if file is None:
        raise _file_not_found()
    if file.promoted_document_id is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已加入知識庫")
    extension = Path(file.display_name).suffix.lower()
    if (
        file.kind == ChatAttachmentKind.IMAGE.value
        or extension not in PROMOTABLE_EXTENSIONS
        or file.status != ChatAttachmentStatus.READY.value
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="此檔案無法加入知識庫，僅支援 PDF、DOCX、TXT、MD。",
        )
    try:
        data = storage.resolve(file.storage_key).read_bytes()
    except (AttachmentError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件已無法讀取。") from exc
    upload = UploadFile(
        file=BytesIO(data),
        size=len(data),
        filename=file.display_name,
        headers=Headers({"content-type": file.mime_type}),
    )
    document = await ingest_upload(
        upload, payload.category, payload.folder_id, document_repository, document_storage, task
    )
    file.promoted_document_id = document.id
    file = await repository.update_file(file)
    return await _as_file_read(file, repository)
