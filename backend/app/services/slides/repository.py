"""Durable persistence for presentation jobs.

The repository deliberately has a small async surface.  Production uses a
request/worker-owned synchronous SQLModel session, while tests and local
callers can use the in-memory implementation without a database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlmodel import Session, select

from app.models.slides import JobStatus, SlideJob


def _now() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class SlideJobRepository(Protocol):
    async def create(self, job: SlideJob) -> SlideJob: ...
    async def get(self, job_id: UUID) -> SlideJob | None: ...
    async def claim(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300) -> tuple[SlideJob | None, bool]: ...
    async def update(self, job: SlideJob) -> SlideJob: ...
    async def update_progress(self, job_id: UUID, *, phase: str, stage: str | None = None, message: str | None = None, lease_token: str | None = None) -> SlideJob | None: ...
    async def mark_terminal(self, job_id: UUID, *, status: str, phase: str, error: str | None = None, artifact_key: str | None = None, download_filename: str | None = None, finished_at: datetime | None = None, lease_token: str | None = None) -> SlideJob | None: ...


class InMemorySlideJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[UUID, SlideJob] = {}

    async def create(self, job: SlideJob) -> SlideJob:
        self.jobs[job.id] = job
        return job

    async def get(self, job_id: UUID) -> SlideJob | None:
        return self.jobs.get(job_id)

    async def update(self, job: SlideJob) -> SlideJob:
        if job.id not in self.jobs:
            return job
        job.updated_at = _now()
        self.jobs[job.id] = job
        return job

    async def claim(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300) -> tuple[SlideJob | None, bool]:
        job = self.jobs.get(job_id)
        if job is None:
            return None, False
        now = _now()
        if job.status in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return job, False
        if job.lease_expires_at and job.lease_expires_at > now and job.lease_token != lease_token:
            return job, False
        job.lease_token = lease_token
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.status = JobStatus.RUNNING.value
        job.phase = job.phase if job.phase not in {"queued", ""} else "preparing"
        job.stage = job.phase
        job.attempts += 1
        job.started_at = job.started_at or now
        job.updated_at = now
        return job, True

    async def update_progress(self, job_id: UUID, *, phase: str, stage: str | None = None, message: str | None = None, lease_token: str | None = None) -> SlideJob | None:
        job = self.jobs.get(job_id)
        if job is None or (lease_token is not None and job.lease_token != lease_token):
            return None
        job.phase = phase
        job.stage = stage or phase
        job.message = message
        job.status = JobStatus.RUNNING.value
        job.updated_at = _now()
        return job

    async def mark_terminal(self, job_id: UUID, *, status: str, phase: str, error: str | None = None, artifact_key: str | None = None, download_filename: str | None = None, finished_at: datetime | None = None, lease_token: str | None = None) -> SlideJob | None:
        job = self.jobs.get(job_id)
        if job is None or (lease_token is not None and job.lease_token != lease_token):
            return None
        job.status = status
        job.phase = phase
        job.stage = phase
        job.message = "Presentation is ready for download." if status == JobStatus.COMPLETED.value else "Presentation generation failed."
        job.error = error
        if artifact_key is not None:
            job.artifact_key = artifact_key
        if download_filename is not None:
            job.download_filename = download_filename
        job.finished_at = finished_at or _now()
        job.updated_at = _now()
        job.lease_token = None
        job.lease_expires_at = None
        return job


class SQLModelSlideJobRepository(InMemorySlideJobRepository):
    def __init__(self, session: Session) -> None:
        # Keep the in-memory methods as a behavioral reference, but never use
        # the local dictionary for a SQL-backed repository.
        self.session = session

    async def create(self, job: SlideJob) -> SlideJob:
        job.document_ids = [str(value) for value in job.document_ids]
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    async def get(self, job_id: UUID) -> SlideJob | None:
        return self.session.get(SlideJob, job_id)

    async def update(self, job: SlideJob) -> SlideJob:
        job.updated_at = _now()
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    async def claim(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300) -> tuple[SlideJob | None, bool]:
        now = _now()
        job = self.session.exec(select(SlideJob).where(SlideJob.id == job_id).with_for_update()).one_or_none()
        if job is None:
            return None, False
        if job.status in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return job, False
        if job.lease_expires_at and job.lease_expires_at > now and job.lease_token != lease_token:
            self.session.rollback()
            return job, False
        job.lease_token = lease_token
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.status = JobStatus.RUNNING.value
        job.phase = job.phase if job.phase not in {"queued", ""} else "preparing"
        job.stage = job.phase
        job.attempts += 1
        job.started_at = job.started_at or now
        job.updated_at = now
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job, True

    async def update_progress(self, job_id: UUID, *, phase: str, stage: str | None = None, message: str | None = None, lease_token: str | None = None) -> SlideJob | None:
        job = self.session.get(SlideJob, job_id)
        if job is None or (lease_token is not None and job.lease_token != lease_token):
            return None
        job.phase, job.stage, job.message = phase, stage or phase, message
        job.status = JobStatus.RUNNING.value
        job.updated_at = _now()
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    async def mark_terminal(self, job_id: UUID, *, status: str, phase: str, error: str | None = None, artifact_key: str | None = None, download_filename: str | None = None, finished_at: datetime | None = None, lease_token: str | None = None) -> SlideJob | None:
        job = self.session.get(SlideJob, job_id)
        if job is None or (lease_token is not None and job.lease_token != lease_token):
            return None
        job.status, job.phase, job.stage, job.error = status, phase, phase, error
        job.message = "Presentation is ready for download." if status == JobStatus.COMPLETED.value else "Presentation generation failed."
        if artifact_key is not None:
            job.artifact_key = artifact_key
        if download_filename is not None:
            job.download_filename = download_filename
        job.finished_at = finished_at or _now()
        job.updated_at = _now()
        job.lease_token = job.lease_expires_at = None
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job


def job_request(job: SlideJob) -> dict[str, object]:
    """Return the canonical queue payload represented by a durable row."""

    return {
        "job_id": job.id,
        "title": job.title,
        "document_ids": [UUID(value) for value in job.document_ids],
        "slides_count": job.slides_count,
        "guidance": job.guidance,
        "tone": job.tone,
    }


__all__ = ["SlideJobRepository", "InMemorySlideJobRepository", "SQLModelSlideJobRepository", "job_request"]
