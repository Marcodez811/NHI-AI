from types import SimpleNamespace
from uuid import uuid4

import pytest
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError
from fastapi import HTTPException

from app.api.routes.slides import (
    create_slides_job,
    download_slides_job,
    get_slides_job,
)
from app.config import settings
from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.models.slides import GenerateSlidesRequest, SlideJob
from app.services.agentic import AgentPhase
from app.services.slides.repository import InMemorySlideJobRepository


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


class FakeDocumentRepository:
    def __init__(self, *documents):
        self.documents = {document.id: document for document in documents}

    async def get_document(self, document_id):
        return self.documents.get(document_id)


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
    assert task.payload.workflow == "slides"
    assert task.payload.input["title"] == "NHI update"
    assert backend.progress[str(response.job_id)].state == "queued"
    assert response.phase is AgentPhase.QUEUED
    assert backend.progress[str(response.job_id)].meta["phase"] == AgentPhase.QUEUED.value

    queued_status = await get_slides_job(response.job_id, backend)
    assert queued_status.phase is AgentPhase.QUEUED


@pytest.mark.asyncio
async def test_create_job_accepts_markdown_source_with_ready_document():
    backend = FakeBackend()
    document = Document(
        original_filename="brief.markdown",
        display_name="brief.markdown",
        mime_type="text/markdown",
        extension=".markdown",
        size_bytes=1,
        checksum="checksum-markdown",
        category=DocumentCategory.BEI_CAN.value,
        storage_key="ignored",
        status=DocumentStatus.READY.value,
    )
    task = FakeTask()

    response = await create_slides_job(
        GenerateSlidesRequest(
            title="Markdown briefing",
            document_ids=[document.id],
            slides_count=8,
            guidance="Summarize the source.",
            tone="formal",
        ),
        backend,
        task,
        FakeDocumentRepository(document),
    )

    assert response.status == "queued"
    assert task.payload.input["document_ids"] == [str(document.id)]


@pytest.mark.asyncio
async def test_create_job_rejects_missing_or_unready_or_unsupported_sources():
    for expected_status, document in [
        (404, None),
        (
            409,
            Document(
                original_filename="queued.txt",
                display_name="queued.txt",
                mime_type="text/plain",
                extension=".txt",
                size_bytes=1,
                checksum="checksum-queued",
                category=DocumentCategory.BEI_CAN.value,
                storage_key="ignored",
                status=DocumentStatus.QUEUED.value,
            ),
        ),
        (
            422,
            Document(
                original_filename="data.csv",
                display_name="data.csv",
                mime_type="text/csv",
                extension=".csv",
                size_bytes=1,
                checksum="checksum-csv",
                category=DocumentCategory.BEI_CAN.value,
                storage_key="ignored",
                status=DocumentStatus.READY.value,
            ),
        ),
    ]:
        backend = FakeBackend()
        task = FakeTask()
        document_id = uuid4() if document is None else document.id
        repository = FakeDocumentRepository(document) if document is not None else FakeDocumentRepository()

        with pytest.raises(HTTPException) as caught:
            await create_slides_job(
                GenerateSlidesRequest(
                    title="Source validation",
                    document_ids=[document_id],
                    slides_count=8,
                    guidance="Summarize the source.",
                    tone="formal",
                ),
                backend,
                task,
                repository,
            )

        assert caught.value.status_code == expected_status
        assert task.payload is None
        assert str(document_id) not in backend.progress


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["NHI update.pptx", "中央癌症防治會報第21次會議.pptx", "健保政策.pptx"])
async def test_completed_status_and_download_are_safe(tmp_path, monkeypatch, filename):
    from urllib.parse import quote

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
            "download_filename": filename,
        },
    )
    status_response = await get_slides_job(job_id, backend)
    download_response = await download_slides_job(job_id, backend)

    assert status_response.phase is AgentPhase.COMPLETED
    assert status_response.download_url == f"/api/v1/slides/jobs/{job_id}/download"
    assert download_response.media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    # Starlette uses RFC 5987 encoding when the attachment name needs it.
    encoded = quote(filename)
    expected = (
        f"attachment; filename*=utf-8''{encoded}"
        if encoded != filename
        else f'attachment; filename="{filename}"'
    )
    assert download_response.headers["content-disposition"] == expected


@pytest.mark.asyncio
async def test_durable_status_exposes_the_original_brief():
    backend = FakeBackend()
    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    document_id = uuid4()
    await repository.create(
        SlideJob(
            id=job_id,
            title="政策重點",
            document_ids=[str(document_id)],
            slides_count=9,
            guidance="聚焦改革影響",
            tone="formal",
        )
    )

    response = await get_slides_job(job_id, backend, repository)

    assert response.brief is not None
    assert response.brief.title == "政策重點"
    assert response.brief.document_ids == [document_id]
    assert response.brief.slides_count == 9


@pytest.mark.asyncio
async def test_unknown_or_unfinished_jobs_do_not_download():
    backend = FakeBackend()
    job_id = uuid4()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as not_found:
        await get_slides_job(job_id, backend)
    assert not_found.value.status_code == 404

    backend.progress[str(job_id)] = TaskProgress(state="running", meta={"stage": "agent"})
    active = await get_slides_job(job_id, backend)
    assert active.status == "running"
    assert active.phase is AgentPhase.PREPARING
    with pytest.raises(HTTPException) as incomplete:
        await download_slides_job(job_id, backend)
    assert incomplete.value.status_code == 409


@pytest.mark.asyncio
async def test_progress_preserves_review_and_revision_phases():
    backend = FakeBackend()
    job_id = uuid4()

    backend.progress[str(job_id)] = TaskProgress(
        state="running",
        meta={
            "status": "running",
            "phase": AgentPhase.REVIEWING.value,
            "stage": "reviewing",
            "message": "Reviewing the generated presentation.",
        },
    )
    reviewing = await get_slides_job(job_id, backend)
    assert reviewing.phase is AgentPhase.REVIEWING
    assert reviewing.stage == "reviewing"

    backend.progress[str(job_id)] = TaskProgress(
        state="running",
        meta={
            "status": "running",
            "phase": AgentPhase.REVISING.value,
            "stage": "revising",
            "message": "Revising the presentation based on review findings.",
        },
    )
    revising = await get_slides_job(job_id, backend)
    assert revising.phase is AgentPhase.REVISING
    assert revising.message == "Revising the presentation based on review findings."


@pytest.mark.asyncio
async def test_failed_terminal_response_includes_failed_phase():
    backend = FakeBackend()
    job_id = uuid4()
    backend.results[str(job_id)] = SimpleNamespace(
        is_err=False,
        return_value={
            "job_id": str(job_id),
            "status": "failed",
            "error": "internal details are not public",
        },
    )

    response = await get_slides_job(job_id, backend)

    assert response.status == "failed"
    assert response.phase is AgentPhase.FAILED
    assert response.error == "Presentation generation failed."
