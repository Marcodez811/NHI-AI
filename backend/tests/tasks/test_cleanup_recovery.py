from __future__ import annotations

from uuid import uuid4

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models.documents import (
    Document,
    DocumentCategory,
    DocumentCleanupTaskPayload,
    IngestionJob,
)


def _document() -> Document:
    return Document(
        original_filename="brief.pdf",
        display_name="brief.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=1,
        checksum=uuid4().hex,
        category=DocumentCategory.LEGISLATIVE_QA.value,
        storage_key="brief.pdf",
        status="ready",
        stage="ready",
        remote_file_id="new-file",
    )


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_pending_and_self_schedules(monkeypatch):
    from app.tasks import documents as task_module

    payload = DocumentCleanupTaskPayload(
        document_id=uuid4(), ingestion_job_id=uuid4(), remote_file_id="old-file"
    )
    monkeypatch.setattr(
        task_module,
        "_claim_cleanup",
        lambda *_args, **_kwargs: task_module._CleanupClaim(
            lease_token="lease",
            remote_file_id="old-file",
            remote_vector_store_file_id=None,
            remote_vector_store_id=None,
        ),
    )
    monkeypatch.setattr(task_module, "_fail_cleanup", lambda *_args, **_kwargs: 60.0)
    scheduled: list[tuple[object, float]] = []

    async def schedule_retry(value, *, delay):
        scheduled.append((value, delay))

    monkeypatch.setattr(task_module, "_schedule_cleanup_retry", schedule_retry)

    class FailingDeletionService:
        async def delete_remote(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(task_module, "_deletion_service", FailingDeletionService())
    result = await task_module.cleanup_document_task.original_func(payload)

    assert result["status"] == "cleanup_pending"
    assert scheduled == [(payload, 60.0)]


def test_cleanup_claim_lease_fences_duplicate_delivery(tmp_path, monkeypatch):
    from app.tasks import documents as task_module

    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'cleanup.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(db_engine)
    monkeypatch.setattr(task_module, "engine", db_engine)
    document = _document()
    job = IngestionJob(
        document_id=document.id,
        revision=1,
        phase="cleanup_pending",
        cleanup_remote_file_id="old-file",
    )
    payload = DocumentCleanupTaskPayload(
        document_id=document.id, ingestion_job_id=job.id
    )
    with Session(db_engine) as session:
        session.add(document)
        session.add(job)
        session.commit()

    first = task_module._claim_cleanup(
        payload, lease_token="first", lease_seconds=300
    )
    second = task_module._claim_cleanup(
        payload, lease_token="second", lease_seconds=300
    )
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_retry_uses_redis_schedule_source(monkeypatch):
    from app.tasks import documents as task_module

    payload = DocumentCleanupTaskPayload(
        document_id=uuid4(), ingestion_job_id=uuid4(), attempt=2
    )
    calls: list[tuple[str, object, object, object]] = []

    class FakeKicker:
        def with_schedule_id(self, schedule_id):
            calls.append(("id", schedule_id, None, None))
            return self

        async def schedule_by_time(self, source, when, value):
            calls.append(("schedule", source, when, value))

    monkeypatch.setattr(task_module.cleanup_document_task, "kicker", lambda: FakeKicker())
    await task_module._schedule_cleanup_retry(payload, delay=60)
    assert calls[0][1] == f"document-cleanup:{payload.ingestion_job_id}:3"
    assert calls[1][3].attempt == 3
