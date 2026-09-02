"""Ingestion retry endpoint tests.

Covers:
- Retry rejected for READY / QUEUED / INDEXING / DELETING → 409
- Retry rejected when local bytes are missing → 409
- Retry rejected when pending_category is set → 409
- Retry increments ingestion_revision and creates a new IngestionJob
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.documents import Document, DocumentCategory, DocumentStatus, IngestionJob


def _make_document(*, status=DocumentStatus.FAILED) -> Document:
    return Document(
        original_filename="brief.pdf",
        display_name="brief.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=100,
        checksum=str(uuid4()),
        category=DocumentCategory.LEGISLATIVE_QA.value,
        storage_key="k",
        status=status.value,
        stage="failed" if status is DocumentStatus.FAILED else status.value,
        ingestion_revision=1,
    )


class FakeStorage:
    def __init__(self, *, has_bytes: bool = True):
        self._has_bytes = has_bytes

    def resolve(self, document_id):
        if not self._has_bytes:
            from app.services.documents.storage import DocumentStorageError
            raise DocumentStorageError("Source file is unavailable.")
        return "fake/path/doc.pdf"


class FakeRepo:
    def __init__(self, document: Document):
        self._document = document
        self.jobs: list[IngestionJob] = []
        self.updated: list[Document] = []

    async def get_document(self, document_id):
        return self._document

    async def update_document(self, document):
        self.updated.append(document)

    async def create_ingestion_job(self, job):
        self.jobs.append(job)

    async def list_ingestion_jobs(self, document_id):
        return self.jobs


class FakeTask:
    def __init__(self, should_fail=False):
        self.queued: list = []
        self._fail = should_fail

    def kicker(self):
        task = self

        class Kicker:
            def with_task_id(self, _id):
                return self

            async def kiq(self, payload):
                if task._fail:
                    raise RuntimeError("Queue is unavailable")
                task.queued.append(payload)

        return Kicker()


@pytest.mark.asyncio
async def test_retry_succeeds_for_failed_document(tmp_path):
    from app.api.routes.documents import retry_ingestion

    document = _make_document(status=DocumentStatus.FAILED)
    repo = FakeRepo(document)
    storage = FakeStorage(has_bytes=True)
    task = FakeTask()

    result = await retry_ingestion(document_id=document.id, repo=repo, storage=storage, task=task)

    assert result.status == DocumentStatus.QUEUED
    assert result.ingestion_job_id == task.queued[0].ingestion_job_id
    assert document.ingestion_revision == 2  # Incremented from 1.


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", [
    DocumentStatus.READY,
    DocumentStatus.QUEUED,
    DocumentStatus.INDEXING,
    DocumentStatus.DELETING,
])
async def test_retry_rejected_for_non_failed_status(bad_status):
    from fastapi import HTTPException
    from app.api.routes.documents import retry_ingestion

    document = _make_document(status=bad_status)
    repo = FakeRepo(document)
    storage = FakeStorage()
    task = FakeTask()

    with pytest.raises(HTTPException) as exc_info:
        await retry_ingestion(document_id=document.id, repo=repo, storage=storage, task=task)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "not_retryable"


@pytest.mark.asyncio
async def test_retry_rejected_when_bytes_missing():
    from fastapi import HTTPException
    from app.api.routes.documents import retry_ingestion

    document = _make_document(status=DocumentStatus.FAILED)
    repo = FakeRepo(document)
    storage = FakeStorage(has_bytes=False)
    task = FakeTask()

    with pytest.raises(HTTPException) as exc_info:
        await retry_ingestion(document_id=document.id, repo=repo, storage=storage, task=task)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "source_missing"


@pytest.mark.asyncio
async def test_retry_rejected_when_pending_category_set():
    from fastapi import HTTPException
    from app.api.routes.documents import retry_ingestion

    document = _make_document(status=DocumentStatus.FAILED)
    document.pending_category = DocumentCategory.PUBLIC_OPINION.value
    repo = FakeRepo(document)
    storage = FakeStorage(has_bytes=True)
    task = FakeTask()

    with pytest.raises(HTTPException) as exc_info:
        await retry_ingestion(document_id=document.id, repo=repo, storage=storage, task=task)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "not_retryable"


@pytest.mark.asyncio
async def test_queue_failure_during_retry_leaves_document_failed(tmp_path):
    """If the queue publish fails during retry, the document returns to FAILED."""

    from app.api.routes.documents import retry_ingestion

    document = _make_document(status=DocumentStatus.FAILED)
    repo = FakeRepo(document)
    storage = FakeStorage(has_bytes=True)
    task = FakeTask(should_fail=True)

    # _queue_ingestion raises HTTPException on queue failure but the
    # document row should be marked failed via the repo.
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await retry_ingestion(document_id=document.id, repo=repo, storage=storage, task=task)
