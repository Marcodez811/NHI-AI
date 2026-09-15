"""Source-grounded chat API."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.models.chat import ChatRequest, ChatResponse, QaModeInfo
from app.config import settings
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
                    detail="找不到所選文件。",
                )
            if document.category != request.mode.value:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="所選文件必須符合目前的搜尋範圍。",
                )
            if document.status != DocumentStatus.READY.value or not document.retrieval_enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="所選文件尚未完成檢索準備。",
                )
            # A document mid-category-transition cannot be cited in either category.
            if getattr(document, "pending_category", None) is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="所選文件尚未完成檢索準備。",
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
                "message": "目前搜尋範圍尚無可用文件。",
            },
        )
    return [doc.id for doc in eligible]


@router.get("/qa-modes", response_model=list[QaModeInfo])
async def list_qa_modes() -> list[QaModeInfo]:
    return [
        QaModeInfo(mode="legislative_qa", label="立院問答", description="立法院問答資料"),
        QaModeInfo(mode="public_opinion", label="輿情", description="政策與輿情資料"),
        QaModeInfo(mode="bei_can", label="備參", description="備參資料"),
    ]


async def _stream_with_heartbeat(
    source: AsyncGenerator[str, None],
    *,
    heartbeat_seconds: float,
    timeout_seconds: float,
) -> AsyncGenerator[str, None]:
    """Forward an SSE generator while keeping proxies alive and enforcing a deadline."""

    iterator = source.__aiter__()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                yield 'data: {"type":"error","code":"chat_timeout","message":"對話處理逾時，請稍後再試。"}\n\n'
                return
            if pending is None:
                pending = asyncio.create_task(iterator.__anext__())
            done, _ = await asyncio.wait(
                {pending},
                timeout=min(heartbeat_seconds, remaining),
            )
            if not done:
                yield ": heartbeat\n\n"
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        with suppress(Exception):
            await iterator.aclose()


def _http_error(exc: ChatServiceError) -> HTTPException:
    message = str(exc)
    code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if "unavailable" in message.lower() or "configured" in message.lower()
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(
        status_code=code,
        detail={
            "code": "chat_unavailable" if code == status.HTTP_503_SERVICE_UNAVAILABLE else "chat_invalid",
            "message": (
                "對話服務暫時無法使用，請稍後再試。"
                if code == status.HTTP_503_SERVICE_UNAVAILABLE
                else "對話請求設定無效。"
            ),
        },
    )


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
        source = service.answer_stream(request, document_id_allowlist=allowlist)
        async for chunk in _stream_with_heartbeat(
            source,
            heartbeat_seconds=settings.chat_stream_heartbeat_seconds,
            timeout_seconds=settings.chat_timeout_seconds,
        ):
            yield chunk

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
