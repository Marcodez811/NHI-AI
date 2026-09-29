"""Agentic chat API: saved multi-turn sessions with per-session attachments.

Replaces the old single-question chat (docs/9_29_chat_core_and_attachments_plan.md,
decision 1). ``SSE_RESPONSE_HEADERS``/``_stream_with_heartbeat`` moved to
``app.services.streaming`` because the slides outline stream also depends on
them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.config import settings
from app.models.chat import (
    ChatAttachment,
    ChatAttachmentRead,
    ChatAttachmentStatus,
    ChatMessage,
    ChatMessageCreate,
    ChatMessageRole,
    ChatMessageStatus,
    ChatModelOption,
    ChatModelsResponse,
    ChatSession,
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionSummary,
    ChatSessionUpdate,
    MAX_REASONING_CHARS,
    as_chat_attachment_read,
    as_chat_message_read,
    session_title_from_message,
)
from app.services.chat import models as chat_models
from app.services.chat.attachments import AttachmentError, ChatAttachmentStorage, process_upload
from app.services.chat.engine import AgentsSdkChatEngine, ChatEngine
from app.services.chat.repository import ChatRepository, InMemoryChatRepository
from app.services.documents.repository import DocumentRepository
from app.services.streaming import SSE_RESPONSE_HEADERS, _stream_with_heartbeat

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)
COMPACTION_RECENT_MESSAGES = 6

_repository = InMemoryChatRepository()
_attachment_storage = ChatAttachmentStorage()


def get_chat_repository() -> ChatRepository:
    """Default dependency; production overrides this with a SQLModel session repository."""

    return _repository


def get_chat_attachment_storage() -> ChatAttachmentStorage:
    return _attachment_storage


def get_chat_document_repository() -> DocumentRepository:
    """Dependency hook for resolving the knowledge-base catalog the search tool post-filters against."""

    from app.api.routes.documents import get_document_repository

    return get_document_repository()


def _default_vector_store_id() -> str | None:
    # Legacy/test fallback; production wiring in main.py overrides this via
    # the durable RetrievalIndexRegistry, the same as the old chat service did.
    return getattr(settings, "openai_vector_store_id", None) or None


def get_chat_engine(
    document_repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> ChatEngine:
    """Default dependency; production overrides this in ``app/main.py``."""

    return AgentsSdkChatEngine(
        document_repository=document_repository,
        vector_store_id_provider=_default_vector_store_id,
        attachment_storage=storage,
        openai_api_key=settings.openai_api_key,
        litellm_api_keys={
            "gemini": settings.gemini_api_key,
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
        },
    )


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


def _model_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "model_unavailable", "message": "所選模型目前無法使用。"},
    )


def _session_not_found_error() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此對話。")


@router.get("/chat/models", response_model=ChatModelsResponse)
async def list_chat_models() -> ChatModelsResponse:
    models = chat_models.list_models()
    return ChatModelsResponse(
        models=[
            ChatModelOption(id=model.id, label=model.label, provider=model.provider, available=model.available)
            for model in models
        ],
        default=chat_models.default_model_id(),
    )


@router.get("/chat/sessions", response_model=list[ChatSessionSummary])
async def list_chat_sessions(
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> list[ChatSessionSummary]:
    sessions = await repository.list_sessions()
    return [ChatSessionSummary.model_validate(session) for session in sessions]


@router.post("/chat/sessions", response_model=ChatSessionDetail, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    payload: ChatSessionCreate,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> ChatSessionDetail:
    model_id = payload.model or chat_models.default_model_id()
    if chat_models.find_available_model(model_id) is None:
        raise _model_unavailable_error()
    session = await repository.create_session(ChatSession(model=model_id))
    # The same shape as GET, so the client can use the new session (and its
    # model) without a second request.
    return ChatSessionDetail(
        id=session.id,
        title=session.title,
        model=session.model,
        created_at=session.created_at,
        updated_at=session.updated_at,
        compacted_through_message_id=session.summary_through_message_id,
        messages=[],
        attachments=[],
    )


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(
    session_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> ChatSessionDetail:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    messages = await repository.list_messages(session_id)
    attachments = await repository.list_attachments(session_id)
    return ChatSessionDetail(
        id=session.id,
        title=session.title,
        model=session.model,
        created_at=session.created_at,
        updated_at=session.updated_at,
        compacted_through_message_id=session.summary_through_message_id,
        messages=[as_chat_message_read(message) for message in messages],
        attachments=[as_chat_attachment_read(attachment) for attachment in attachments],
    )


@router.patch("/chat/sessions/{session_id}", response_model=ChatSessionSummary)
async def rename_chat_session(
    session_id: UUID,
    payload: ChatSessionUpdate,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> ChatSessionSummary:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    session.title = payload.title
    session = await repository.update_session(session)
    return ChatSessionSummary.model_validate(session)


@router.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> None:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    await repository.delete_session(session_id)
    await storage.delete_session(session_id)


@router.post(
    "/chat/sessions/{session_id}/attachments",
    response_model=ChatAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_chat_attachment(
    session_id: UUID,
    file: Annotated[UploadFile, File(...)],
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> ChatAttachmentRead:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    attachment_id = uuid4()
    try:
        processed = await process_upload(storage, session_id, attachment_id, file)
    except AttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    attachment = await repository.create_attachment(
        ChatAttachment(
            id=attachment_id,
            session_id=session_id,
            display_name=processed.display_name,
            mime_type=processed.mime_type,
            kind=processed.kind,
            size_bytes=processed.size_bytes,
            storage_key=processed.storage_key,
            status=processed.status,
            error=processed.error,
            text_chars=processed.text_chars,
        )
    )
    return as_chat_attachment_read(attachment)


@router.get("/chat/sessions/{session_id}/attachments/{attachment_id}/content")
async def get_chat_attachment_content(
    session_id: UUID,
    attachment_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> FileResponse:
    attachment = await repository.get_attachment(attachment_id)
    if attachment is None or attachment.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此附件。")
    try:
        path = storage.resolve(session_id, attachment.storage_key)
    except AttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FileResponse(path, media_type=attachment.mime_type, filename=attachment.display_name)


@router.delete("/chat/sessions/{session_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_attachment(
    session_id: UUID,
    attachment_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> None:
    attachment = await repository.get_attachment(attachment_id)
    if attachment is None or attachment.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此附件。")
    messages = await repository.list_messages(session_id)
    if any(str(attachment_id) in message.attachment_ids for message in messages):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此附件已送出，無法刪除。",
        )
    await storage.delete_attachment(session_id, attachment.storage_key)
    await repository.delete_attachment(attachment_id)


@router.post("/chat/sessions/{session_id}/messages")
async def send_chat_message(
    session_id: UUID,
    payload: ChatMessageCreate,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    engine: Annotated[ChatEngine, Depends(get_chat_engine)],
) -> StreamingResponse:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    selected_model = chat_models.find_available_model(payload.model)
    if selected_model is None:
        raise _model_unavailable_error()

    all_attachments = await repository.list_attachments(session_id)
    ready_by_id = {
        attachment.id: attachment
        for attachment in all_attachments
        if attachment.status == ChatAttachmentStatus.READY.value
    }
    for attachment_id in payload.attachment_ids:
        if attachment_id not in ready_by_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "attachment_not_ready", "message": "附件尚未準備完成或不存在。"},
            )

    history = await repository.list_messages(session_id)
    is_first_message = not history
    user_message = await repository.create_message(
        ChatMessage(
            session_id=session_id,
            role=ChatMessageRole.USER.value,
            content=payload.content,
            attachment_ids=[str(item) for item in payload.attachment_ids],
        )
    )
    message_id = uuid4()

    async def _generate() -> AsyncGenerator[str, None]:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_length = 0
        sources: list[dict[str, str]] = []
        usage: dict[str, int] | None = None
        had_error = False
        completed = False
        try:
            yield _sse("message_start", {"message_id": str(message_id)})
            replay_history = history
            previous = next((message for message in reversed(history) if message.role == ChatMessageRole.ASSISTANT.value), None)
            previous_tokens = (previous.usage or {}).get("input_tokens") if previous else None
            marker_index = next(
                (index for index, message in enumerate(history) if message.id == session.summary_through_message_id),
                -1,
            )
            if session.summary_through_message_id is not None and marker_index >= 0:
                replay_history = history[marker_index + 1:]
            turn_summary = session.summary if replay_history is not history else None
            if (
                isinstance(previous_tokens, (int, float))
                and previous_tokens > settings.chat_compaction_threshold * selected_model.context_window
                and len(replay_history) > COMPACTION_RECENT_MESSAGES
            ):
                newly_old = replay_history[:-COMPACTION_RECENT_MESSAGES]
                yield _sse("compacting", {})
                saved_summary = session.summary
                saved_marker = session.summary_through_message_id
                try:
                    summary = await engine.summarize(
                        history=newly_old,
                        previous_summary=turn_summary,
                        attachments=all_attachments,
                        model=payload.model,
                    )
                    if not summary.strip():
                        raise ValueError("Empty compaction summary")
                    session.summary = summary
                    session.summary_through_message_id = newly_old[-1].id
                    await repository.update_session(session)
                    replay_history = replay_history[-COMPACTION_RECENT_MESSAGES:]
                    turn_summary = summary
                    yield _sse("compacted", {"ok": True})
                except (asyncio.CancelledError, GeneratorExit):
                    session.summary = saved_summary
                    session.summary_through_message_id = saved_marker
                    raise
                except Exception:
                    session.summary = saved_summary
                    session.summary_through_message_id = saved_marker
                    logger.exception("Chat compaction failed")
                    # A failed summary must not truncate the conversation for this turn.
                    replay_history = history
                    turn_summary = None
                    yield _sse("compacted", {"ok": False})
            async for event in engine.run_turn(
                history=replay_history,
                user_message=user_message,
                attachments=all_attachments,
                model=payload.model,
                summary=turn_summary,
            ):
                if event.type == "text_delta":
                    text_parts.append(str(event.data.get("text", "")))
                    yield _sse("text_delta", event.data)
                elif event.type == "reasoning_delta":
                    delta = str(event.data.get("text", ""))
                    if reasoning_length < MAX_REASONING_CHARS:
                        kept = delta[:MAX_REASONING_CHARS - reasoning_length]
                        reasoning_parts.append(kept)
                        reasoning_length += len(kept)
                    yield _sse("reasoning_delta", {"text": delta})
                elif event.type in ("tool_started", "tool_finished"):
                    yield _sse(event.type, event.data)
                elif event.type == "sources":
                    sources = list(event.data.get("sources", []))
                    yield _sse("sources", event.data)
                elif event.type == "usage":
                    usage = event.data
                elif event.type == "error":
                    had_error = True
                    yield _sse("error", event.data)
            completed = True
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            had_error = True
            completed = True
            yield _sse(
                "error",
                {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"},
            )
        finally:
            status_value = (
                ChatMessageStatus.ERROR.value
                if had_error
                else ChatMessageStatus.COMPLETE.value
                if completed
                else ChatMessageStatus.INTERRUPTED.value
            )
            await repository.create_message(
                ChatMessage(
                    id=message_id,
                    session_id=session_id,
                    role=ChatMessageRole.ASSISTANT.value,
                    content="".join(text_parts),
                    sources=sources,
                    status=status_value,
                    model=payload.model,
                    usage=usage,
                    reasoning="".join(reasoning_parts) or None,
                )
            )
            session.model = payload.model
            if is_first_message:
                session.title = session_title_from_message(payload.content)
            await repository.update_session(session)

        if not had_error:
            yield _sse("done", {"message_id": str(message_id), "title": session.title})

    # Passed directly (not wrapped in another generator): a client disconnect
    # closes exactly this object, so ``_stream_with_heartbeat``'s own
    # ``finally`` synchronously closes ``_generate()`` and runs its interrupted-
    # status persistence, instead of leaving that close to rely on garbage
    # collection of an intermediate wrapper generator.
    return StreamingResponse(
        _stream_with_heartbeat(
            _generate(),
            heartbeat_seconds=settings.chat_stream_heartbeat_seconds,
            timeout_seconds=settings.chat_timeout_seconds,
        ),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )
