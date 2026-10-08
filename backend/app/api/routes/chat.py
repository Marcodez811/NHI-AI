"""Agentic chat API: saved multi-turn sessions that reference the user's files and knowledge-base documents.

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
from pydantic import BaseModel, ConfigDict, Field as PydanticField

from app.config import settings
from app.models.chat import (
    ChatAttachmentRead,
    ChatAttachmentSource,
    ChatAttachmentStatus,
    ChatAttachmentView,
    ChatAttachmentKind,
    ChatMessage,
    ChatMessageCreate,
    ChatMessageRole,
    ChatMessageStatus,
    ChatModelOption,
    ChatModelsResponse,
    ChatQaWorkspace,
    ChatSession,
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionSkillUpdate,
    ChatSessionSummary,
    ChatSessionUpdate,
    LinkDocumentsRequest,
    LinkFilesRequest,
    MAX_REASONING_CHARS,
    UserFile,
    as_chat_attachment_read,
    as_chat_attachment_view,
    as_chat_message_read,
    session_title_from_message,
)
from app.services import app_settings
from app.services.chat import models as chat_models
from app.models.documents import Document, DocumentStatus
from app.services.chat.attachments import AttachmentError, ChatAttachmentStorage, process_upload
from app.services.chat.engine import AgentsSdkChatEngine, ChatEngine
from app.services.chat.skills import legislative_qa as qa
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


_KB_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


def _document_view(session_id: UUID, document: Document) -> ChatAttachmentView:
    usable = document.status == DocumentStatus.READY.value and document.retrieval_enabled
    return ChatAttachmentView(
        id=document.id,
        session_id=session_id,
        display_name=document.display_name,
        mime_type=_KB_MIME_TYPES.get(document.extension.lower(), document.mime_type),
        kind=ChatAttachmentKind.DOCUMENT.value,
        size_bytes=document.size_bytes,
        storage_key=document.storage_key,
        status=ChatAttachmentStatus.READY.value if usable else ChatAttachmentStatus.FAILED.value,
        error=None if usable else "此知識庫文件目前無法使用。",
        text_chars=None,
        created_at=document.created_at,
        source=ChatAttachmentSource.KNOWLEDGE_BASE.value,
    )


async def _session_attachments(
    session_id: UUID,
    repository: ChatRepository,
    document_repository: DocumentRepository | None,
) -> list[ChatAttachmentView]:
    """Linked user files plus attached knowledge-base documents, as one list."""

    views = [as_chat_attachment_view(file, session_id) for file in await repository.list_session_files(session_id)]
    if document_repository is not None:
        for document_id in await repository.list_session_document_ids(session_id):
            document = await document_repository.get_document(document_id)
            if document is not None:
                views.append(_document_view(session_id, document))
    return views


@router.get("/chat/models", response_model=ChatModelsResponse)
async def list_chat_models() -> ChatModelsResponse:
    models = chat_models.list_visible_models()
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
        skill=session.skill,
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
    document_repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)] = None,
) -> ChatSessionDetail:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    messages = await repository.list_messages(session_id)
    attachments = await _session_attachments(session_id, repository, document_repository)
    return ChatSessionDetail(
        id=session.id,
        title=session.title,
        model=session.model,
        skill=session.skill,
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
) -> None:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    # Only the links go: the user's files stay in 「我的檔案」.
    await repository.delete_session(session_id)


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
    file_id = uuid4()
    try:
        processed = await process_upload(storage, file_id, file)
    except AttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    user_file = await repository.create_file(
        UserFile(
            id=file_id,
            display_name=processed.display_name,
            mime_type=processed.mime_type,
            kind=processed.kind,
            size_bytes=processed.size_bytes,
            storage_key=processed.storage_key,
            status=processed.status,
            error=processed.error,
            text_chars=processed.text_chars,
            origin_session_id=session_id,
        )
    )
    await repository.link_file(session_id, user_file.id)
    return as_chat_attachment_read(as_chat_attachment_view(user_file, session_id))


@router.get("/chat/sessions/{session_id}/attachments/{attachment_id}/content")
async def get_chat_attachment_content(
    session_id: UUID,
    attachment_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    storage: Annotated[ChatAttachmentStorage, Depends(get_chat_attachment_storage)],
) -> FileResponse:
    linked = {file.id: file for file in await repository.list_session_files(session_id)}
    attachment = linked.get(attachment_id)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此附件。")
    try:
        path = storage.resolve(attachment.storage_key)
    except AttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FileResponse(path, media_type=attachment.mime_type, filename=attachment.display_name)


@router.delete("/chat/sessions/{session_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_attachment(
    session_id: UUID,
    attachment_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> None:
    """Legacy route: removes an unsent attachment from the conversation (the file stays in 「我的檔案」)."""

    linked = {file.id for file in await repository.list_session_files(session_id)}
    if attachment_id not in linked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此附件。")
    messages = await repository.list_messages(session_id)
    if any(str(attachment_id) in message.attachment_ids for message in messages):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此附件已送出，無法刪除。",
        )
    await repository.unlink_file(session_id, attachment_id)


@router.post("/chat/sessions/{session_id}/files", response_model=list[ChatAttachmentRead])
async def link_chat_files(
    session_id: UUID,
    payload: LinkFilesRequest,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> list[ChatAttachmentRead]:
    if await repository.get_session(session_id) is None:
        raise _session_not_found_error()
    files = []
    for file_id in dict.fromkeys(payload.file_ids):
        user_file = await repository.get_file(file_id)
        if user_file is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此檔案。")
        files.append(user_file)
    for user_file in files:
        await repository.link_file(session_id, user_file.id)
    return [as_chat_attachment_read(as_chat_attachment_view(user_file, session_id)) for user_file in files]


@router.delete("/chat/sessions/{session_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_chat_file(
    session_id: UUID,
    file_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> None:
    if await repository.get_session(session_id) is None:
        raise _session_not_found_error()
    await repository.unlink_file(session_id, file_id)


@router.post("/chat/sessions/{session_id}/documents", response_model=list[ChatAttachmentRead])
async def attach_chat_documents(
    session_id: UUID,
    payload: LinkDocumentsRequest,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    document_repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)],
) -> list[ChatAttachmentRead]:
    if await repository.get_session(session_id) is None:
        raise _session_not_found_error()
    documents = []
    for document_id in dict.fromkeys(payload.document_ids):
        document = await document_repository.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此知識庫文件。")
        if document.status != DocumentStatus.READY.value or not document.retrieval_enabled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "document_not_ready", "message": "此知識庫文件尚未建立索引或目前無法使用。"},
            )
        if document.extension.lower() == ".pdf" and document.size_bytes > app_settings.value("uploads.chat.max_pdf_bytes"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "document_too_large", "message": "此 PDF 檔案過大，無法加入對話。"},
            )
        documents.append(document)
    for document in documents:
        await repository.link_document(session_id, document.id)
    return [as_chat_attachment_read(_document_view(session_id, document)) for document in documents]


@router.delete("/chat/sessions/{session_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_chat_document(
    session_id: UUID,
    document_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> None:
    if await repository.get_session(session_id) is None:
        raise _session_not_found_error()
    await repository.unlink_document(session_id, document_id)


# ── Skill mode and the 立院QA workspace ─────────────────────────────────────


@router.put("/chat/sessions/{session_id}/skill", response_model=ChatSessionSummary)
async def set_chat_session_skill(
    session_id: UUID,
    payload: ChatSessionSkillUpdate,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> ChatSessionSummary:
    """Enter (``{"skill": "legislative_qa"}``) or leave (``{"skill": null}``) a skill mode.

    Leaving keeps the workspace, so entering again resumes where the user left off.
    """

    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    session.skill = payload.skill
    session = await repository.update_session(session)
    return ChatSessionSummary.model_validate(session)


class QaWorkspaceRead(BaseModel):
    stage: str
    questions: dict[str, Any]
    documents: dict[str, Any]
    evidence: dict[str, Any]
    outline: dict[str, Any]
    versions: list[dict[str, Any]]
    base_version_id: str | None
    updated_at: Any


class QaQuestionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[qa.QuestionEdit] = PydanticField(min_length=1)
    confirm: bool = True


class QaDocumentsConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_ids: list[UUID] = PydanticField(min_length=1)


class QaOutlineUpdate(qa.OutlineInput):
    confirm: bool = False


_QA_CONFLICT_CODES = {"qa_steps_unconfirmed", "qa_outline_missing"}


def _qa_http_error(error: qa.QaError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if error.code in _QA_CONFLICT_CODES else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(status_code=code, detail={"code": error.code, "message": error.message})


async def _load_qa(session_id: UUID, repository: ChatRepository) -> ChatQaWorkspace:
    if await repository.get_session(session_id) is None:
        raise _session_not_found_error()
    stored = await repository.get_qa_workspace(session_id)
    return qa.clone(stored) if stored is not None else qa.new_workspace(session_id)


async def _save_qa(workspace: ChatQaWorkspace, repository: ChatRepository) -> QaWorkspaceRead:
    qa.recompute_stage(workspace)
    saved = await repository.save_qa_workspace(workspace)
    return QaWorkspaceRead(**qa.to_read(saved))


@router.get("/chat/sessions/{session_id}/qa", response_model=QaWorkspaceRead)
async def get_qa_workspace(
    session_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> QaWorkspaceRead:
    workspace = await _load_qa(session_id, repository)
    qa.recompute_stage(workspace)
    return QaWorkspaceRead(**qa.to_read(workspace))


@router.put("/chat/sessions/{session_id}/qa/questions", response_model=QaWorkspaceRead)
async def update_qa_questions(
    session_id: UUID,
    payload: QaQuestionsUpdate,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> QaWorkspaceRead:
    """Confirm (``confirm: true``, the default) or just edit the question list."""

    workspace = await _load_qa(session_id, repository)
    try:
        qa.apply_questions(workspace, payload.questions, confirmed=payload.confirm)
    except qa.QaError as error:
        raise _qa_http_error(error) from error
    return await _save_qa(workspace, repository)


@router.post("/chat/sessions/{session_id}/qa/documents/confirm", response_model=QaWorkspaceRead)
async def confirm_qa_documents(
    session_id: UUID,
    payload: QaDocumentsConfirm,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    document_repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)] = None,
) -> QaWorkspaceRead:
    workspace = await _load_qa(session_id, repository)
    attachments = await _session_attachments(session_id, repository, document_repository)
    try:
        qa.confirm_documents(workspace, attachments, payload.attachment_ids)
    except qa.QaError as error:
        raise _qa_http_error(error) from error
    return await _save_qa(workspace, repository)


@router.put("/chat/sessions/{session_id}/qa/outlines/{question_no}", response_model=QaWorkspaceRead)
async def update_qa_outline(
    session_id: UUID,
    question_no: int,
    payload: QaOutlineUpdate,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> QaWorkspaceRead:
    """Edit one question's outline; it needs re-confirming unless ``confirm`` is true."""

    workspace = await _load_qa(session_id, repository)
    try:
        qa.apply_outline(
            workspace, question_no,
            qa.OutlineInput(short=payload.short, detail=payload.detail, dispute_requested=payload.dispute_requested),
            confirmed=payload.confirm,
        )
    except qa.QaError as error:
        raise _qa_http_error(error) from error
    return await _save_qa(workspace, repository)


@router.post("/chat/sessions/{session_id}/qa/outlines/confirm-all", response_model=QaWorkspaceRead)
async def confirm_all_qa_outlines(
    session_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> QaWorkspaceRead:
    workspace = await _load_qa(session_id, repository)
    try:
        qa.confirm_all_outlines(workspace)
    except qa.QaError as error:
        raise _qa_http_error(error) from error
    return await _save_qa(workspace, repository)


@router.post("/chat/sessions/{session_id}/qa/outlines/{question_no}/confirm", response_model=QaWorkspaceRead)
async def confirm_qa_outline(
    session_id: UUID,
    question_no: int,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
) -> QaWorkspaceRead:
    workspace = await _load_qa(session_id, repository)
    try:
        qa.confirm_outline(workspace, question_no)
    except qa.QaError as error:
        raise _qa_http_error(error) from error
    return await _save_qa(workspace, repository)


@router.post("/chat/sessions/{session_id}/messages")
async def send_chat_message(
    session_id: UUID,
    payload: ChatMessageCreate,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    engine: Annotated[ChatEngine, Depends(get_chat_engine)],
    document_repository: Annotated[DocumentRepository, Depends(get_chat_document_repository)] = None,
) -> StreamingResponse:
    session = await repository.get_session(session_id)
    if session is None:
        raise _session_not_found_error()
    selected_model = chat_models.find_available_model(payload.model)
    if selected_model is None:
        raise _model_unavailable_error()

    all_attachments = await _session_attachments(session_id, repository, document_repository)
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
        cards: list[dict[str, Any]] = []
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
                and previous_tokens > app_settings.value("chat.compaction_threshold") * selected_model.context_window
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
            # Only a skill turn passes ``skill``, so plain chat keeps the original engine call.
            skill_kwargs: dict[str, Any] = (
                {"skill": qa.SkillRuntime(skill=session.skill, session_id=session_id, repository=repository)}
                if session.skill == qa.SKILL_ID
                else {}
            )
            async for event in engine.run_turn(
                history=replay_history,
                user_message=user_message,
                attachments=all_attachments,
                model=payload.model,
                summary=turn_summary,
                **skill_kwargs,
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
                elif event.type == "skill_card":
                    cards.append(event.data)
                    yield _sse("skill_card", event.data)
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
                    cards=cards,
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
            timeout_seconds=app_settings.value("chat.timeout_seconds"),
        ),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )
