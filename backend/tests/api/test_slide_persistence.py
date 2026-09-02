"""Durable slide-job API behavior independent of expiring Redis state."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError

from app.api.routes.slides import create_slides_job, download_slides_job, get_slides_job
from app.config import settings
from app.models.slides import GenerateSlidesRequest, JobStatus
from app.services.agentic import AgentPhase
from app.services.slides.repository import InMemorySlideJobRepository


class _Backend:
    def __init__(self, *, fail_writes: bool = False, fail_reads: bool = False) -> None:
        self.fail_writes = fail_writes
        self.fail_reads = fail_reads
        self.progress: dict[str, TaskProgress] = {}

    async def set_progress(self, job_id, progress):
        if self.fail_writes:
            raise RuntimeError("redis unavailable")
        self.progress[job_id] = progress

    async def get_progress(self, job_id):
        if self.fail_reads:
            raise RuntimeError("redis unavailable")
        return self.progress.get(job_id)

    async def get_result(self, _job_id):
        raise ResultIsMissingError


class _Kicker:
    def __init__(self, task) -> None:
        self.task = task

    def with_task_id(self, task_id):
        self.task.task_id = task_id
        return self

    async def kiq(self, payload):
        self.task.payload = payload


class _Task:
    def __init__(self) -> None:
        self.task_id = None
        self.payload = None

    def kicker(self):
        return _Kicker(self)


def _request() -> GenerateSlidesRequest:
    return GenerateSlidesRequest(
        title="Persistent job",
        document_ids=[uuid4()],
        slides_count=5,
        guidance="Summarize the supplied material.",
        tone="formal",
    )


@pytest.mark.asyncio
async def test_durable_job_survives_missing_or_unavailable_redis():
    repository = InMemorySlideJobRepository()
    task = _Task()
    response = await create_slides_job(
        _request(),
        _Backend(fail_writes=True),
        task,
        slide_repository=repository,
    )

    status = await get_slides_job(
        response.job_id,
        _Backend(fail_reads=True),
        repository,
    )

    assert task.payload is not None
    assert status.status is JobStatus.QUEUED
    assert status.phase is AgentPhase.QUEUED


@pytest.mark.asyncio
async def test_terminal_database_state_wins_over_stale_redis_and_downloads(tmp_path, monkeypatch):
    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    created = await create_slides_job(
        _request(),
        _Backend(),
        _Task(),
        slide_repository=repository,
    )
    job_id = created.job_id
    await repository.mark_terminal(
        job_id,
        status=JobStatus.COMPLETED.value,
        phase=AgentPhase.COMPLETED.value,
        artifact_key=f"{job_id}.pptx",
        download_filename="persistent.pptx",
        finished_at=datetime.now(timezone.utc),
    )
    (tmp_path / f"{job_id}.pptx").write_bytes(b"pptx")
    monkeypatch.setattr(settings, "agent_output_root", tmp_path)
    redis = _Backend()
    redis.progress[str(job_id)] = TaskProgress(
        state="running",
        meta={"status": "running", "phase": "drafting", "stage": "drafting"},
    )

    status = await get_slides_job(job_id, redis, repository)
    download = await download_slides_job(job_id, redis, repository)

    assert status.status is JobStatus.COMPLETED
    assert status.phase is AgentPhase.COMPLETED
    assert status.download_url is not None
    assert download.media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.asyncio
async def test_durable_unknown_job_remains_not_found_when_redis_has_progress():
    job_id = uuid4()
    redis = _Backend()
    redis.progress[str(job_id)] = TaskProgress(state="running", meta={"status": "running"})

    with pytest.raises(HTTPException) as caught:
        await get_slides_job(job_id, redis, InMemorySlideJobRepository())

    assert caught.value.status_code == 404
