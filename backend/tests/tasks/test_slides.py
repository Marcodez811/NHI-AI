from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.slides import JobStatus, SlidesTaskPayload, SlidesTaskResult
from app.services.agentic import WorkflowStatus


class FakeBackend:
    def __init__(self):
        self.progress = {}

    async def set_progress(self, job_id, progress):
        self.progress[job_id] = progress


@pytest.mark.asyncio
async def test_compatibility_task_routes_through_staged_workflow(tmp_path, monkeypatch):
    from app.tasks import slides as task_module

    document_id = uuid4()
    fake_backend = FakeBackend()
    captured = {}

    async def fake_execute_workflow(payload, **kwargs):
        captured["payload"] = payload
        await kwargs["progress_callback"]({"stage": "agent", "message": "Generating slides."})
        return SimpleNamespace(
            status=WorkflowStatus.COMPLETED,
            output=SlidesTaskResult(
                job_id=payload.job_id,
                status=JobStatus.COMPLETED,
                artifact_key="artifacts/deck.pptx",
                download_filename="deck.pptx",
            ).model_dump(mode="json"),
        )

    monkeypatch.setattr(task_module, "result_backend", fake_backend)
    monkeypatch.setattr(task_module.settings, "slides_documents_root", tmp_path / "documents")
    monkeypatch.setattr(task_module.settings, "slides_jobs_root", tmp_path / "jobs")
    monkeypatch.setattr(task_module.settings, "slides_output_root", tmp_path / "output")
    monkeypatch.setattr("app.services.agentic.service.execute_workflow", fake_execute_workflow)
    payload = SlidesTaskPayload(
        job_id=uuid4(),
        title="NHI update",
        document_ids=[document_id],
        slides_count=8,
        guidance="Summarize.",
        tone="formal",
    )

    result = await task_module.generate_slides_task.original_func(payload)

    assert result.status is JobStatus.COMPLETED
    assert result.artifact_key == "artifacts/deck.pptx"
    assert captured["payload"].workflow == "slides"
    assert captured["payload"].input.document_ids == [document_id]
    assert fake_backend.progress[str(payload.job_id)].state == "completed"
