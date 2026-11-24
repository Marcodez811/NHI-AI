"""Contracts for the AI-written 立院QA document workflow."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.slides import JobStatus
from app.services.legislative_qa.document import QaIssue
from app.services.agentic.contracts import AgentPhase


class GenerateQaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_label: str | None = Field(default=None, max_length=64)


class QaTaskPayload(BaseModel):
    job_id: UUID
    session_id: UUID
    session_label: str | None = Field(default=None, max_length=64)


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
