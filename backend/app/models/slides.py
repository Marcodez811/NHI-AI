"""Public and durable contracts for grounded presentation jobs."""

from __future__ import annotations

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
MAX_OUTLINE_NODES = 30
# The worker, artifact preflight, and API boundary must agree on which
# document sources can be turned into a presentation. Keep this contract in
# the slides model so it crosses the HTTP and generic-agent task boundaries
# without each implementation maintaining its own list.
SUPPORTED_SLIDE_SOURCE_EXTENSIONS = frozenset({".pdf", ".docx", ".md", ".markdown", ".txt"})


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    # A job parked here is waiting on a human decision (e.g. outline
    # approval), not a crashed or stalled worker.  Reconciliation and lease
    # sweeps must treat this as a deliberate pause, never a retry target.
    AWAITING_INPUT = "awaiting_input"
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


class SlideJobBrief(BaseModel):
    """The immutable user brief that created a durable slide job."""

    title: str
    document_ids: list[UUID]
    slides_count: int
    guidance: str
    tone: Literal["formal", "casual"]


class OutlineNode(BaseModel):
    """One planned section of the deck; the grounded unit the author must honor.

    ``evidence_refs`` point into the frozen ``EvidenceStore`` rather than
    carrying source text inline, so a node can be re-validated against
    ``work/evidence.json`` without re-parsing prose.
    """

    id: str = Field(min_length=1, max_length=80)
    heading: str = Field(min_length=1, max_length=200)
    intent: str = Field(min_length=1, max_length=500)
    key_points: list[str] = Field(min_length=2, max_length=5)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    # The user's tuning knob: how much author attention this section earns
    # relative to its siblings, without dictating a page-by-page structure.
    emphasis: Literal["light", "normal", "deep"]
    approx_slides: int = Field(gt=0, le=25)


class SlideOutline(BaseModel):
    """The planner's proposed structure, subject to human approval before authoring.

    Granularity is deliberately medium: nodes are sections, not slides, so a
    user can redirect emphasis without negotiating a per-page breakdown.
    """

    title: str = Field(min_length=1, max_length=300)
    narrative: str = Field(min_length=1, max_length=600)
    # The section cap is enforced in ``node_count_within_limit`` rather than as
    # ``max_length`` here. A field constraint becomes ``maxItems`` in the JSON schema
    # sent to the model, and Gemini rejects the whole request (400 INVALID_ARGUMENT)
    # when ``maxItems`` sits on this array of objects -- measured 2026-09-22, where
    # the same limit on the string arrays inside OutlineNode is accepted. A validator
    # keeps the rule without putting it on the wire.
    nodes: list[OutlineNode] = Field(min_length=1)
    total_slides: int = Field(gt=0, le=100)

    @model_validator(mode="after")
    def node_count_within_limit(self) -> "SlideOutline":
        if len(self.nodes) > MAX_OUTLINE_NODES:
            raise ValueError(f"an outline may have at most {MAX_OUTLINE_NODES} sections")
        return self

    @model_validator(mode="after")
    def node_ids_unique(self) -> "SlideOutline":
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("outline node ids must be unique")
        return self


class OutlineRevisionResponse(BaseModel):
    """One immutable planner proposal, as returned to an API client."""

    job_id: UUID
    revision: int = Field(ge=1)
    outline: SlideOutline
    session_id: str
    created_at: datetime
    approved_at: datetime | None = None


class OutlineMessageRequest(BaseModel):
    """A human's turn in the outline planning conversation."""

    message: str = Field(min_length=1, max_length=4000)


class ApproveOutlineRequest(BaseModel):
    """Approve one outline revision and resume authoring from it.

    ``expected_revision`` is both the staleness check and the retry key: a
    dropped response can be retried with the same value and will not enqueue a
    second author run.  See
    ``app/api/routes/slides.py::approve_slide_job_outline``.
    """

    expected_revision: int = Field(ge=1)


class ApproveOutlineResponse(BaseModel):
    """Returned once an outline revision has been approved (or re-approved)."""

    job_id: UUID
    status: JobStatus
    phase: AgentPhase
    approved_revision: int = Field(ge=1)


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
    # Durable jobs return their original input so a refreshed browser can
    # accurately describe and restart a presentation without guessing.
    brief: SlideJobBrief | None = None


class SlideJobSummary(BaseModel):
    """Only the durable fields needed to rediscover a presentation job."""

    job_id: UUID
    title: str
    status: JobStatus
    phase: AgentPhase
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


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
