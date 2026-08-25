from types import SimpleNamespace
from uuid import uuid4

import pytest
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError

from app.api.routes.slides import (
    create_slides_job,
    download_slides_job,
    get_slides_job,
)
from app.config import settings
from app.models.slides import GenerateSlidesRequest


class FakeBackend:
    def __init__(self):
        self.progress = {}
        self.results = {}

    async def set_progress(self, job_id, progress):
        self.progress[job_id] = progress

    async def get_progress(self, job_id):
        return self.progress.get(job_id)

    async def get_result(self, job_id):
        if job_id not in self.results:
            raise ResultIsMissingError
        return self.results[job_id]


class FakeKicker:
    def __init__(self, task):
        self.task = task

    def with_task_id(self, task_id):
        self.task.task_id = task_id
        return self

    async def kiq(self, payload):
        self.task.payload = payload


class FakeTask:
    def __init__(self):
        self.task_id = None
        self.payload = None

    def kicker(self):
        return FakeKicker(self)


@pytest.mark.asyncio
async def test_create_job_records_queued_progress_before_enqueue():
    backend = FakeBackend()
    task = FakeTask()
    response = await create_slides_job(
        GenerateSlidesRequest(
            title="NHI update",
            document_ids=[uuid4()],
            slides_count=8,
            guidance="Summarize the policy implications.",
            tone="formal",
        ),
        backend,
        task,
    )

    assert task.task_id == str(response.job_id)
    assert task.payload.job_id == response.job_id
    assert backend.progress[str(response.job_id)].state == "queued"


@pytest.mark.asyncio
async def test_completed_status_and_download_are_safe(tmp_path, monkeypatch):
    backend = FakeBackend()
    job_id = uuid4()
    deck = tmp_path / "jobs" / "deck.pptx"
    deck.parent.mkdir()
    deck.write_bytes(b"pptx")
    monkeypatch.setattr(settings, "slides_output_root", tmp_path)
    backend.results[str(job_id)] = SimpleNamespace(
        is_err=False,
        return_value={
            "job_id": str(job_id),
            "status": "completed",
            "artifact_key": "jobs/deck.pptx",
            "download_filename": "NHI update.pptx",
        },
    )
    status_response = await get_slides_job(job_id, backend)
    download_response = await download_slides_job(job_id, backend)

    assert status_response.download_url == f"/api/v1/slides/jobs/{job_id}/download"
    assert download_response.media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.asyncio
async def test_unknown_or_unfinished_jobs_do_not_download():
    backend = FakeBackend()
    job_id = uuid4()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as not_found:
        await get_slides_job(job_id, backend)
    assert not_found.value.status_code == 404

    backend.progress[str(job_id)] = TaskProgress(state="running", meta={"stage": "agent"})
    with pytest.raises(HTTPException) as incomplete:
        await download_slides_job(job_id, backend)
    assert incomplete.value.status_code == 409
