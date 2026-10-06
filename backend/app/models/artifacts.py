"""Generated outputs (decks, later reports) the user can find in 「我的檔案」.

# TODO(auth): scope artifacts to an owning user when login exists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactKind(StrEnum):
    SLIDE_DECK = "slide_deck"
    NEWS_DRAFT = "news_draft"
    REPORT = "report"


class ArtifactWorkflow(StrEnum):
    SLIDES = "slides"
    NEWS = "news"
    CHAT = "chat"


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kind: str = Field(max_length=32)
    title: str = Field(max_length=512)
    mime_type: str = Field(max_length=255)
    size_bytes: int = Field(ge=0)
    # Relative to the documents root, e.g. ``artifacts/<id>.pptx``.
    storage_key: str = Field(max_length=1024)
    source_workflow: str = Field(max_length=32)
    source_job_id: UUID | None = Field(default=None, nullable=True, index=True)
    session_id: UUID | None = Field(
        default=None, foreign_key="chat_sessions.id", ondelete="SET NULL", nullable=True
    )
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: ArtifactKind
    title: str
    mime_type: str
    size_bytes: int
    source_workflow: ArtifactWorkflow
    source_job_id: UUID | None
    created_at: datetime
