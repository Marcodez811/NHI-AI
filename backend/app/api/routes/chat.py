"""Source-grounded chat API."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.models.chat import ChatRequest, ChatResponse, QaModeInfo
from app.models.documents import DocumentCategory, DocumentStatus
from app.services.documents.repository import DocumentRepository
from app.services.chat.responder import ChatServiceError, ResponseService

router = APIRouter(tags=["chat"])


def get_chat_service() -> ResponseService:
    """Dependency hook; applications/tests may override this with settings."""

    return ResponseService()


def get_chat_document_repository() -> DocumentRepository:
    """Dependency hook for validating explicit document scopes."""

    from app.api.routes.documents import get_document_repository

    return get_document_repository()


async def _validate_document_scope(request: ChatRequest, repository: DocumentRepository) -> None:
    for document_id in request.document_ids:
        document = await repository.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="A selected document was not found.")
        if document.category != request.mode.value:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selected documents must match the chat category.")
        if document.status != DocumentStatus.READY.value or not document.retrieval_enabled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Selected documents are not ready for retrieval.")


@router.get("/qa-modes", response_model=list[QaModeInfo])
async def list_qa_modes() -> list[QaModeInfo]:
    return [
        QaModeInfo(mode="legislative_qa", label="立院問答", description="Search legislative Q&A sources."),
        QaModeInfo(mode="public_opinion", label="輿情", description="Search policy and public-opinion sources."),
        QaModeInfo(mode="bei_can", label="備參", description="Search briefing reference sources."),
    ]


def _http_error(exc: ChatServiceError) -> HTTPException:
    message = str(exc)
    code = status.HTTP_503_SERVICE_UNAVAILABLE if "unavailable" in message.lower() or "configured" in message.lower() else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)


@router.post("/chat", response_model=ChatResponse)
async def answer_chat(
    request: ChatRequest,
    service: Annotated[ResponseService, Depends(get_chat_service)],
    repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)],
) -> ChatResponse:
    await _validate_document_scope(request, repository)
    try:
        return service.answer(request)
    except ChatServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/chat/stream")
async def stream_chat(
    request: ChatRequest,
    service: Annotated[ResponseService, Depends(get_chat_service)],
    repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)],
) -> StreamingResponse:
    await _validate_document_scope(request, repository)
    events: Generator[str, None, None] = service.answer_stream(request)
    return StreamingResponse(events, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
