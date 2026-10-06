"""Chat-domain persistence and API contracts.

Replaces the old single-question chat (``ChatRequest``/``ChatResponse``/``QaMode``,
see docs/9_29_chat_core_and_attachments_plan.md decision 1): a session holds a
durable, multi-turn conversation with an agent loop that can call tools and
read attachments the user dropped into that one chat. There are no ownership
columns yet -- this is a single-user prototype.
# TODO(auth): scope sessions (and therefore messages/attachments) to an owning user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field as PydanticField, field_validator
from sqlalchemy import Column, JSON, Text
from sqlmodel import Field, SQLModel

DEFAULT_SESSION_TITLE = "新對話"
SESSION_TITLE_MAX_CHARS = 30
MAX_MESSAGE_CHARS = 20_000
MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_REASONING_CHARS = 20_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessageStatus(StrEnum):
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class ChatAttachmentKind(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"


class ChatAttachmentStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


# ── Tables ──────────────────────────────────────────────────────────────────


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    title: str = Field(default=DEFAULT_SESSION_TITLE, max_length=255)
    # The last model used in this session; also the default for the next turn.
    model: str | None = Field(default=None, max_length=128)
    summary: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    summary_through_message_id: UUID | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    session_id: UUID = Field(foreign_key="chat_sessions.id", index=True)
    role: str = Field(max_length=16)
    content: str = Field(sa_column=Column(Text, nullable=False))
    # User messages only: attachments sent with this turn, as string UUIDs.
    attachment_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    # Assistant messages only: knowledge-base results the model used this turn.
    sources: list[dict[str, str]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    status: str = Field(default=ChatMessageStatus.COMPLETE.value, max_length=16)
    # Assistant messages only.
    model: str | None = Field(default=None, max_length=128)
    usage: dict[str, int] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    reasoning: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class UserFile(SQLModel, table=True):
    """A file the user uploaded; conversations reference it, none own it.

    ``storage_key`` is relative to the documents root: new files live at
    ``user_files/<id>``; files migrated from the old per-chat attachments keep
    their ``chat_attachments/<session_id>/<file>`` path.
    # TODO(auth): add an owner column when login exists.
    """

    __tablename__ = "user_files"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    display_name: str = Field(max_length=512)
    mime_type: str = Field(max_length=255)
    kind: str = Field(max_length=16)
    size_bytes: int = Field(ge=0)
    storage_key: str = Field(max_length=1024)
    status: str = Field(default=ChatAttachmentStatus.READY.value, max_length=16)
    error: str | None = Field(default=None, max_length=512)
    text_chars: int | None = Field(default=None)
    origin_session_id: UUID | None = Field(
        default=None, foreign_key="chat_sessions.id", ondelete="SET NULL", nullable=True, index=True
    )
    promoted_document_id: UUID | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class ChatSessionFile(SQLModel, table=True):
    """Which user files a conversation uses. Deleting the conversation deletes only this link."""

    __tablename__ = "chat_session_files"

    session_id: UUID = Field(foreign_key="chat_sessions.id", primary_key=True, ondelete="CASCADE")
    file_id: UUID = Field(foreign_key="user_files.id", primary_key=True, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class ChatSessionDocument(SQLModel, table=True):
    """Knowledge-base documents attached to a conversation."""

    __tablename__ = "chat_session_documents"

    session_id: UUID = Field(foreign_key="chat_sessions.id", primary_key=True, ondelete="CASCADE")
    document_id: UUID = Field(foreign_key="documents.id", primary_key=True, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class ChatAttachmentSource(StrEnum):
    UPLOAD = "upload"
    KNOWLEDGE_BASE = "knowledge_base"


@dataclass
class ChatAttachmentView:
    """One attachment as the engine and the API see it: a user file or a knowledge-base document.

    ``storage_key`` is relative to the documents root for both sources.
    """

    id: UUID
    session_id: UUID
    display_name: str
    mime_type: str
    kind: str
    size_bytes: int
    storage_key: str
    status: str
    error: str | None
    text_chars: int | None
    created_at: datetime
    source: str = ChatAttachmentSource.UPLOAD.value


# ── API contracts ───────────────────────────────────────────────────────────


class ChatModelOption(BaseModel):
    id: str
    label: str
    provider: str
    available: bool


class ChatModelsResponse(BaseModel):
    models: list[ChatModelOption]
    default: str


class ChatSessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    updated_at: datetime


class ChatSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None


class ChatSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = PydanticField(min_length=1, max_length=255)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title cannot be empty or whitespace")
        return value


class ChatAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    mime_type: str
    kind: ChatAttachmentKind
    size_bytes: int
    status: ChatAttachmentStatus
    error: str | None
    text_chars: int | None
    created_at: datetime
    source: ChatAttachmentSource = ChatAttachmentSource.UPLOAD


class UserFileRead(BaseModel):
    id: UUID
    display_name: str
    mime_type: str
    kind: ChatAttachmentKind
    size_bytes: int
    status: ChatAttachmentStatus
    error: str | None
    created_at: datetime
    origin_session_id: UUID | None
    origin_session_title: str | None
    in_knowledge_base: bool


class LinkFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_ids: list[UUID] = PydanticField(min_length=1, max_length=MAX_ATTACHMENTS_PER_MESSAGE * 5)


class LinkDocumentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[UUID] = PydanticField(min_length=1, max_length=MAX_ATTACHMENTS_PER_MESSAGE * 5)


class ChatSourceRead(BaseModel):
    name: str
    snippet: str


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: ChatMessageRole
    content: str
    attachment_ids: list[UUID]
    sources: list[ChatSourceRead]
    status: ChatMessageStatus
    model: str | None
    usage: dict[str, int] | None
    reasoning: str | None
    created_at: datetime


class ChatSessionDetail(BaseModel):
    id: UUID
    title: str
    model: str | None
    created_at: datetime
    updated_at: datetime
    compacted_through_message_id: UUID | None
    messages: list[ChatMessageRead]
    attachments: list[ChatAttachmentRead]


class ChatMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = PydanticField(min_length=1, max_length=MAX_MESSAGE_CHARS)
    attachment_ids: list[UUID] = PydanticField(default_factory=list, max_length=MAX_ATTACHMENTS_PER_MESSAGE)
    model: str

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content cannot be empty or whitespace")
        return value

    @field_validator("attachment_ids")
    @classmethod
    def attachment_ids_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("attachment_ids must be unique")
        return value


def session_title_from_message(content: str) -> str:
    """Derive a session title from the first user message.

    First ``SESSION_TITLE_MAX_CHARS`` characters, whitespace collapsed --
    no extra model call (docs/9_29_chat_core_and_attachments_plan.md).
    """

    collapsed = " ".join(content.split())
    return collapsed[:SESSION_TITLE_MAX_CHARS] or DEFAULT_SESSION_TITLE


def as_chat_message_read(message: ChatMessage) -> ChatMessageRead:
    data: dict[str, Any] = message.model_dump()
    return ChatMessageRead.model_validate(data)


def as_chat_attachment_read(attachment: ChatAttachmentView | UserFile) -> ChatAttachmentRead:
    if isinstance(attachment, UserFile):
        data: dict[str, Any] = attachment.model_dump()
    else:
        data = {**attachment.__dict__}
    return ChatAttachmentRead.model_validate(data)


def as_chat_attachment_view(file: UserFile, session_id: UUID) -> ChatAttachmentView:
    return ChatAttachmentView(
        id=file.id,
        session_id=session_id,
        display_name=file.display_name,
        mime_type=file.mime_type,
        kind=file.kind,
        size_bytes=file.size_bytes,
        storage_key=file.storage_key,
        status=file.status,
        error=file.error,
        text_chars=file.text_chars,
        created_at=file.created_at,
    )
