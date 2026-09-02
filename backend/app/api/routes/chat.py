"""Source-grounded chat API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

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
    """Dependency hook for resolving document scopes."""

    from app.api.routes.documents import get_document_repository

    return get_document_repository()


async def _resolve_document_scope(
    request: ChatRequest,
    repository: DocumentRepository,
) -> list[UUID]:
    """Resolve the server-side document allowlist for a chat request.

    Explicit selections are validated for readiness, retrieval eligibility, and
    category membership.  Category-wide requests resolve every currently eligible
    document from PostgreSQL.  An empty resolved scope raises a stable conflict
    so the UI can direct the user to upload a source instead of sending a
    guaranteed-unproductive Responses API request.
    """

    if request.document_ids:
        # Explicit scope: validate each selection individually.
        for document_id in request.document_ids:
            document = await repository.get_document(document_id)
            if document is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="A selected document was not found.",
                )
            if document.category != request.mode.value:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Selected documents must match the chat category.",
                )
            if document.status != DocumentStatus.READY.value or not document.retrieval_enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Selected documents are not ready for retrieval.",
                )
            # A document mid-category-transition cannot be cited in either category.
            if getattr(document, "pending_category", None) is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Selected documents are not ready for retrieval.",
                )
        return list(request.document_ids)

    # Category-wide scope: resolve from PostgreSQL.
    ready_documents = await repository.list_documents(
        category=DocumentCategory(request.mode.value),
        status=DocumentStatus.READY,
        retrieval_enabled=True,
    )
    # Exclude any document currently transitioning to a new category.
    eligible = [
        doc for doc in ready_documents
        if getattr(doc, "pending_category", None) is None
    ]
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "knowledge_base_empty",
                "message": "No retrieval-ready documents are available for this category.",
            },
        )
    return [doc.id for doc in eligible]


@router.get("/qa-modes", response_model=list[QaModeInfo])
async def list_qa_modes() -> list[QaModeInfo]:
    return [
        QaModeInfo(mode="legislative_qa", label="立院問答", description="Search legislative Q&A sources."),
        QaModeInfo(mode="public_opinion", label="輿情", description="Search policy and public-opinion sources."),
        QaModeInfo(mode="bei_can", label="備參", description="Search briefing reference sources."),
    ]


def _http_error(exc: ChatServiceError) -> HTTPException:
    message = str(exc)
    code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if "unavailable" in message.lower() or "configured" in message.lower()
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=message)


@router.post("/chat", response_model=ChatResponse)
async def answer_chat(
    request: ChatRequest,
    service: Annotated[ResponseService, Depends(get_chat_service)],
    repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)],
) -> ChatResponse:
    allowlist = await _resolve_document_scope(request, repository)
    try:
        return await service.answer(request, document_id_allowlist=allowlist)
    except ChatServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/chat/stream")
async def stream_chat(
    request: ChatRequest,
    service: Annotated[ResponseService, Depends(get_chat_service)],
    repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)],
) -> StreamingResponse:
    allowlist = await _resolve_document_scope(request, repository)

    async def _generate() -> AsyncGenerator[str, None]:
        async for chunk in service.answer_stream(request, document_id_allowlist=allowlist):
            yield chunk

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
