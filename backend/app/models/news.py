"""Public and worker contracts for source-grounded news releases."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.slides import JobStatus
from app.services.agentic.contracts import AgentPhase


class GenerateNewsRequest(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=20)
    guidance: str = Field(default="", max_length=4000)

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("document_ids must be unique")
        return value


class NewsTaskPayload(GenerateNewsRequest):
    job_id: UUID


class NewsTaskResult(BaseModel):
    job_id: UUID
    article: str = Field(min_length=1, max_length=30000)


class CreateNewsJobResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    phase: AgentPhase


class NewsJobStatusResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    phase: AgentPhase
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    article: str | None = None
