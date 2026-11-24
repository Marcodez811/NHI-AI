"""Contracts for the AI-written 立院QA document workflow."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.slides import JobStatus
from app.services.legislative_qa.document import QaDocument, QaIssue
from app.services.agentic.contracts import AgentPhase


class GenerateQaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_label: str | None = Field(default=None, max_length=64)


class QaRevision(BaseModel):
    """A scoped revision of one base version: only ``question_no``'s ``part`` (one section when ``section`` is set)."""

    model_config = ConfigDict(extra="forbid")

    base_artifact_id: UUID
    question_no: int = Field(ge=1)
    part: Literal["short", "detail", "both"]
    section: int | None = Field(default=None, ge=0)
    # The user's wording preference: untrusted text, never an instruction that overrides the evidence rules.
    instruction: str = Field(min_length=1, max_length=500)


class ReviseQaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_no: int = Field(ge=1)
    part: Literal["short", "detail", "both"]
    section: int | None = Field(default=None, ge=0)
    instruction: str = Field(min_length=1, max_length=500)
    # Defaults to the latest AI version.
    base_artifact_id: UUID | None = None


class QaTaskPayload(BaseModel):
    job_id: UUID
    session_id: UUID
    session_label: str | None = Field(default=None, max_length=64)
    revision: QaRevision | None = None


class QaTaskResult(BaseModel):
    job_id: UUID
    artifact_id: UUID
    checked: bool = False
    issues: list[QaIssue] = Field(default_factory=list)
    review: dict = Field(default_factory=dict)


class QaJobStatusResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    phase: AgentPhase
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    artifact_id: UUID | None = None
    download_url: str | None = None
    # Verification of the finished document: issues the writer could not resolve (for 人工確認).
    checked: bool = False
    issues: list[QaIssue] = Field(default_factory=list)
    review: dict = Field(default_factory=dict)


class QaVersionContent(BaseModel):
    """One produced version with its structured content (what the .docx is built from)."""

    artifact_id: UUID
    kind: str
    created_at: datetime | None = None
    download_url: str
    document: QaDocument
