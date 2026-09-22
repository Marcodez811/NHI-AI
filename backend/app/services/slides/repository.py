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
from app.services.agentic.contracts import AgentPhase

MAX_RECENT_SLIDE_JOBS = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """Treat a naive timestamp as UTC; SQLite round-trips a column and drops tzinfo.

    Every write in this repository stamps ``_now()``, which is always
    UTC-aware, so a naive value read back is UTC without its offset, never
    local time. Comparing it against another UTC-aware value without this
    would raise instead of comparing.
    """

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


@runtime_checkable
class SlideJobRepository(Protocol):
    async def create(self, job: SlideJob) -> SlideJob: ...
    async def get(self, job_id: UUID) -> SlideJob | None: ...
    async def list_recent(self, *, limit: int = 20) -> list[SlideJob]: ...
    async def claim(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300, allow_resume_from_awaiting_input: bool = False) -> tuple[SlideJob | None, bool]: ...
    async def update(self, job: SlideJob) -> SlideJob: ...
    async def update_progress(self, job_id: UUID, *, phase: str, stage: str | None = None, message: str | None = None, lease_token: str | None = None) -> SlideJob | None: ...
    async def mark_terminal(self, job_id: UUID, *, status: str, phase: str, error: str | None = None, artifact_key: str | None = None, download_filename: str | None = None, finished_at: datetime | None = None, lease_token: str | None = None) -> SlideJob | None: ...
    async def pause_for_outline_approval(self, job_id: UUID, *, message: str | None = None, lease_token: str | None = None) -> SlideJob | None: ...
    async def expire_awaiting_input(self, job_id: UUID, *, cutoff: datetime, error: str) -> SlideJob | None: ...


class InMemorySlideJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[UUID, SlideJob] = {}

    async def create(self, job: SlideJob) -> SlideJob:
        self.jobs[job.id] = job
        return job

    async def get(self, job_id: UUID) -> SlideJob | None:
        return self.jobs.get(job_id)

    async def list_recent(self, *, limit: int = 20) -> list[SlideJob]:
        """Use the durable creation order so paused jobs remain discoverable."""

        bounded_limit = max(1, min(limit, MAX_RECENT_SLIDE_JOBS))
        return sorted(
            self.jobs.values(),
            key=lambda job: (_as_utc(job.created_at), str(job.id)),
            reverse=True,
        )[:bounded_limit]

    async def update(self, job: SlideJob) -> SlideJob:
        if job.id not in self.jobs:
            return job
        job.updated_at = _now()
        self.jobs[job.id] = job
        return job

    async def claim(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300, allow_resume_from_awaiting_input: bool = False) -> tuple[SlideJob | None, bool]:
        job = self.jobs.get(job_id)
        if job is None:
            return None, False
        now = _now()
        if job.status in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return job, False
        if job.status == JobStatus.AWAITING_INPUT.value and not allow_resume_from_awaiting_input:
            # Parked awaiting a human decision, not a crashed worker: an
            # ordinary (non-resume) delivery must never silently pull it back
            # into RUNNING. Only an ``agents.run`` carrying
            # ``resume_from="author"`` -- enqueued once by the approve
            # endpoint -- may reclaim it.
            return job, False
        if job.lease_expires_at and _as_utc(job.lease_expires_at) > now and job.lease_token != lease_token:
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

    async def pause_for_outline_approval(self, job_id: UUID, *, message: str | None = None, lease_token: str | None = None) -> SlideJob | None:
        """Park a job awaiting a human outline decision, releasing its lease.

        Unlike ``mark_terminal`` this never sets ``finished_at``: the workflow
        is paused, not done, and its workspace (frozen evidence, and the
        pending outline revision) must survive for the eventual
        ``resume_from="author"`` re-entry. The worker must never hold a lease
        while waiting on a human, so the lease is released here exactly as
        ``mark_terminal`` releases it on completion or failure.
        """

        job = self.jobs.get(job_id)
        if job is None or (lease_token is not None and job.lease_token != lease_token):
            return None
        job.status = JobStatus.AWAITING_INPUT.value
        job.phase = AgentPhase.AWAITING_OUTLINE.value
        job.stage = AgentPhase.AWAITING_OUTLINE.value
        job.message = message or "Waiting for outline approval."
        job.updated_at = _now()
        job.lease_token = None
        job.lease_expires_at = None
        return job

    async def expire_awaiting_input(self, job_id: UUID, *, cutoff: datetime, error: str) -> SlideJob | None:
        """Terminally fail one job still parked in AWAITING_INPUT past its TTL.

        Re-checks status and staleness against the job as it stands right now,
        not as the sweep first read it: if a concurrent
        ``claim(allow_resume_from_awaiting_input=True)`` already moved the job
        to RUNNING, or refreshed ``updated_at`` past ``cutoff``, this is a
        no-op rather than a lost-update race against that resume.
        """

        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.status != JobStatus.AWAITING_INPUT.value:
            return None
        if job.updated_at and _as_utc(job.updated_at) > cutoff:
            return None
        job.status = JobStatus.FAILED.value
        job.phase = AgentPhase.FAILED.value
        job.stage = AgentPhase.FAILED.value
        job.message = "Presentation generation failed."
        job.error = error
        job.finished_at = _now()
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

    async def list_recent(self, *, limit: int = 20) -> list[SlideJob]:
        bounded_limit = max(1, min(limit, MAX_RECENT_SLIDE_JOBS))
        statement = select(SlideJob).order_by(SlideJob.created_at.desc(), SlideJob.id.desc()).limit(bounded_limit)
        return list(self.session.exec(statement).all())

    async def update(self, job: SlideJob) -> SlideJob:
        job.updated_at = _now()
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    async def claim(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300, allow_resume_from_awaiting_input: bool = False) -> tuple[SlideJob | None, bool]:
        now = _now()
        job = self.session.exec(select(SlideJob).where(SlideJob.id == job_id).with_for_update()).one_or_none()
        if job is None:
            return None, False
        if job.status in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            return job, False
        if job.status == JobStatus.AWAITING_INPUT.value and not allow_resume_from_awaiting_input:
            self.session.rollback()
            return job, False
        # ``_as_utc`` mirrors ``expire_awaiting_input``: SQLite round-trips a datetime
        # column and drops tzinfo, so a persisted, still-unexpired lease comes back
        # naive while ``now`` is UTC-aware. This is exactly the crash-recovery path --
        # a lease only outlives its holder when a worker dies mid-job -- so comparing
        # them directly would raise ``TypeError`` on the one path meant to recover it.
        if job.lease_expires_at and _as_utc(job.lease_expires_at) > now and job.lease_token != lease_token:
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

    async def pause_for_outline_approval(self, job_id: UUID, *, message: str | None = None, lease_token: str | None = None) -> SlideJob | None:
        job = self.session.get(SlideJob, job_id)
        if job is None or (lease_token is not None and job.lease_token != lease_token):
            return None
        job.status = JobStatus.AWAITING_INPUT.value
        job.phase = AgentPhase.AWAITING_OUTLINE.value
        job.stage = AgentPhase.AWAITING_OUTLINE.value
        job.message = message or "Waiting for outline approval."
        job.updated_at = _now()
        job.lease_token = job.lease_expires_at = None
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    async def expire_awaiting_input(self, job_id: UUID, *, cutoff: datetime, error: str) -> SlideJob | None:
        # ``with_for_update`` mirrors ``claim``: the row lock is what makes
        # the status/staleness recheck below race-free against a concurrent
        # resume rather than just a best-effort read.
        job = self.session.exec(select(SlideJob).where(SlideJob.id == job_id).with_for_update()).one_or_none()
        if job is None:
            return None
        if job.status != JobStatus.AWAITING_INPUT.value or (job.updated_at and _as_utc(job.updated_at) > cutoff):
            self.session.rollback()
            return None
        job.status = JobStatus.FAILED.value
        job.phase = AgentPhase.FAILED.value
        job.stage = AgentPhase.FAILED.value
        job.message = "Presentation generation failed."
        job.error = error
        job.finished_at = _now()
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
