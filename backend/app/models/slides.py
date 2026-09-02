from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import Column, JSON
from sqlmodel import SQLModel, Field as SQLField

from app.services.agentic.contracts import AgentPhase

DEFAULT_MAX_DOCUMENTS = 20
HARD_UPLOAD_CEILING = 25
DEFAULT_TIMEOUT_MINUTES = 45
# The worker, artifact preflight, and API boundary must agree on which
# document sources can be turned into a presentation. Keep this contract in
# the slides model so it crosses the HTTP and generic-agent task boundaries
# without each implementation maintaining its own list.
SUPPORTED_SLIDE_SOURCE_EXTENSIONS = frozenset({".pdf", ".docx", ".md", ".markdown", ".txt"})


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SlideJob(SQLModel, table=True):
    """Durable catalog row for one presentation workflow.

    Redis remains useful for low-latency progress, but this row is the
    authority for lifecycle state and the published artifact.  The input
    document IDs are stored as strings because PostgreSQL/SQLite JSON values
    cannot encode Python UUID instances portably.
    """

    __tablename__ = "slide_jobs"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    title: str = SQLField(max_length=512)
    document_ids: list[str] = SQLField(default_factory=list, sa_column=Column(JSON, nullable=False))
    slides_count: int = SQLField(ge=5, le=25)
    guidance: str = SQLField(default="")
    tone: str = SQLField(max_length=32)
    status: str = SQLField(default=JobStatus.QUEUED.value, index=True, max_length=32)
    phase: str = SQLField(default="queued", index=True, max_length=32)
    stage: str | None = SQLField(default="queued", max_length=64)
    message: str | None = SQLField(default=None, max_length=512)
    error: str | None = SQLField(default=None, max_length=512)
    artifact_key: str | None = SQLField(default=None, max_length=1024)
    download_filename: str | None = SQLField(default=None, max_length=512)
    attempts: int = SQLField(default=0, ge=0)
    lease_token: str | None = SQLField(default=None, max_length=128)
    lease_expires_at: datetime | None = SQLField(default=None)
    started_at: datetime | None = SQLField(default=None)
    finished_at: datetime | None = SQLField(default=None)
    created_at: datetime = SQLField(default_factory=_utcnow, nullable=False)
    updated_at: datetime = SQLField(default_factory=_utcnow, nullable=False)

    @field_validator("document_ids", mode="before")
    @classmethod
    def normalize_document_ids(cls, value: object) -> list[str]:
        return [str(item) for item in (value or [])]


class GenerateSlidesRequest(BaseModel):
    """What the API accepts to kick off a slides job."""

    title: str = Field(min_length=1)
    document_ids: list[UUID] = Field(min_length=1, max_length=DEFAULT_MAX_DOCUMENTS)
    slides_count: int = Field(ge=5, le=25)
    guidance: str
    tone: Literal["formal", "casual"]

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title cannot be empty or whitespace")
        return v

    @field_validator("document_ids")
    @classmethod
    def document_ids_unique(cls, v: list[UUID]) -> list[UUID]:
        if len(v) != len(set(v)):
            raise ValueError("document_ids must be unique")
        return v


class CreateSlidesJobResponse(BaseModel):
    """Returned immediately after a job is accepted and queued."""

    job_id: UUID
    status: JobStatus
    phase: AgentPhase = AgentPhase.QUEUED


class SlidesJobStatusResponse(BaseModel):
    """Returned when a client polls for job status."""

    job_id: UUID
    status: JobStatus
    phase: AgentPhase
    stage: str | None = None
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    download_url: str | None = None


# --------------------------------------------------------------------------
# Internal contracts (cross the Redis / TaskIQ boundary)
# --------------------------------------------------------------------------

class SlidesTaskPayload(GenerateSlidesRequest):
    """
    What actually gets serialized onto the Redis stream for a worker to
    pick up. Everything a worker needs to run the job, minus anything
    filesystem-specific -- document_ids are resolved to paths worker-side
    via DocumentResolver, never sent as paths over the wire.
    """

    job_id: UUID


class SlidesTaskResult(BaseModel):
    """
    What a worker reports back once a job finishes or fails.
    `artifact_key` is a path relative to the job's output directory
    (never absolute) so it's safe to store and pass back through Redis.
    """

    job_id: UUID
    status: JobStatus
    phase: AgentPhase | None = None
    artifact_key: str | None = None
    download_filename: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    @model_validator(mode="after")
    def default_terminal_phase(self) -> "SlidesTaskResult":
        """Make legacy worker results explicit at the public boundary."""

        if self.status is JobStatus.COMPLETED:
            self.phase = AgentPhase.COMPLETED
        elif self.status is JobStatus.FAILED:
            self.phase = AgentPhase.FAILED
        elif self.phase is None:
            self.phase = {
                JobStatus.QUEUED: AgentPhase.QUEUED,
                JobStatus.RUNNING: AgentPhase.PREPARING,
            }[self.status]
        return self


@runtime_checkable
class DocumentResolver(Protocol):
    """Resolve opaque document identifiers inside the worker environment."""

    async def resolve_many(self, document_ids: list[UUID]) -> list[Path]:
        """Return local source paths for the supplied document IDs."""
