"""Application service for durable slide-job lifecycle transitions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from app.models.slides import GenerateSlidesRequest, JobStatus, SlideJob, SlidesTaskResult
from app.services.agentic import AgentPhase
from app.services.slides.repository import SlideJobRepository, job_request


class SlideJobService:
    """Keep lifecycle mutations in one small, repository-backed boundary."""

    def __init__(self, repository: SlideJobRepository) -> None:
        self.repository = repository

    async def create(self, request: GenerateSlidesRequest, *, job_id: UUID | None = None, queued_at: datetime | None = None) -> SlideJob:
        from datetime import datetime, timezone

        now = queued_at or datetime.now(timezone.utc)
        job = SlideJob(
            id=job_id or uuid4(),
            title=request.title,
            document_ids=[str(value) for value in request.document_ids],
            slides_count=request.slides_count,
            guidance=request.guidance,
            tone=request.tone,
            status=JobStatus.QUEUED.value,
            phase=AgentPhase.QUEUED.value,
            stage="queued",
            message="Presentation job is queued.",
            started_at=now,
        )
        return await self.repository.create(job)

    async def claim(self, job_id: UUID, *, lease_token: str | None = None, lease_seconds: int = 300) -> tuple[SlideJob | None, bool, str]:
        token = lease_token or uuid4().hex
        job, claimed = await self.repository.claim(job_id, lease_token=token, lease_seconds=lease_seconds)
        return job, claimed, token

    async def record_progress(self, job_id: UUID, *, phase: str, stage: str | None = None, message: str | None = None, lease_token: str | None = None) -> SlideJob | None:
        return await self.repository.update_progress(job_id, phase=phase, stage=stage, message=message, lease_token=lease_token)

    async def record_result(self, result: SlidesTaskResult, *, lease_token: str | None = None) -> SlideJob | None:
        return await self.repository.mark_terminal(
            result.job_id,
            status=result.status.value,
            phase=(result.phase or (AgentPhase.COMPLETED if result.status is JobStatus.COMPLETED else AgentPhase.FAILED)).value,
            error=result.error,
            artifact_key=result.artifact_key,
            download_filename=result.download_filename,
            finished_at=result.finished_at,
            lease_token=lease_token,
        )

    @staticmethod
    def task_input(job: SlideJob) -> dict[str, object]:
        return job_request(job)


__all__ = ["SlideJobService"]
