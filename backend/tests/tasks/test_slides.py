from uuid import uuid4

import pytest

from app.models.slides import JobStatus, SlidesTaskPayload, SlidesTaskResult


class FakeBackend:
    def __init__(self):
        self.progress = {}

    async def set_progress(self, job_id, progress):
        self.progress[job_id] = progress


@pytest.mark.asyncio
async def test_task_resolves_documents_and_returns_relative_artifact(tmp_path, monkeypatch):
    from app.services.slides import agent as slides_main
    from app.tasks import slides as task_module

    document_id = uuid4()
    document_dir = tmp_path / "documents" / str(document_id)
    document_dir.mkdir(parents=True)
    source = document_dir / "source.txt"
    source.write_text("source", encoding="utf-8")
    fake_backend = FakeBackend()
    captured = {}

    async def fake_generate_slides(**kwargs):
        captured.update(kwargs)
        await kwargs["progress_callback"]({"stage": "agent", "message": "Generating slides."})
        return SlidesTaskResult(
            job_id=kwargs["job_id"],
            status=JobStatus.COMPLETED,
            artifact_key="artifacts/deck.pptx",
            download_filename="deck.pptx",
        )

    monkeypatch.setattr(task_module, "result_backend", fake_backend)
    monkeypatch.setattr(task_module.settings, "slides_documents_root", tmp_path / "documents")
    monkeypatch.setattr(task_module.settings, "slides_jobs_root", tmp_path / "jobs")
    monkeypatch.setattr(task_module.settings, "slides_output_root", tmp_path / "output")
    monkeypatch.setattr(slides_main, "generate_slides", fake_generate_slides, raising=False)
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
    assert captured["source_paths"] == [source.resolve()]
    assert fake_backend.progress[str(payload.job_id)].state == "completed"
