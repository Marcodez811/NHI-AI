from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.documents import delete_document, update_document
from app.models.documents import (
    Document,
    DocumentCategory,
    DocumentDeleteTaskPayload,
    DocumentStatus,
    DocumentUpdate,
)
from app.services.documents.repository import InMemoryDocumentRepository
from app.services.documents.storage import LocalDocumentStorage


def make_document(*, status: DocumentStatus = DocumentStatus.READY, extension: str = ".pdf") -> Document:
    return Document(
        original_filename=f"brief{extension}",
        display_name=f"brief{extension}",
        mime_type="application/pdf",
        extension=extension,
        size_bytes=1,
        checksum=str(uuid4()),
        category=DocumentCategory.LEGISLATIVE_QA.value,
        storage_key="ignored",
        status=status.value,
        stage="ready" if status is DocumentStatus.READY else status.value,
    )


class FakeKicker:
    def __init__(self, task):
        self.task = task

    def with_task_id(self, task_id):
        self.task.task_id = task_id
        return self

    async def kiq(self, payload):
        if self.task.error:
            raise self.task.error
        self.task.payload = payload


class FakeTask:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.task_id = None
        self.payload = None

    def kicker(self):
        return FakeKicker(self)


@pytest.mark.asyncio
async def test_delete_acceptance_transitions_row_and_enqueues_cleanup(tmp_path):
    repo = InMemoryDocumentRepository()
    document = make_document()
    await repo.create_document(document)
    task = FakeTask()

    response = await delete_document(
        document.id,
        repo=repo,
        storage=LocalDocumentStorage(tmp_path),
        task=task,
    )

    assert response.status is DocumentStatus.DELETING
    assert response.stage == "deletion_queued"
    assert response.retrieval_enabled is False
    assert isinstance(task.payload, DocumentDeleteTaskPayload)
    assert task.payload.document_id == document.id
    persisted = await repo.get_document(document.id)
    assert persisted is not None
    assert persisted.status == DocumentStatus.DELETING.value


@pytest.mark.asyncio
async def test_delete_rejects_documents_still_indexing():
    repo = InMemoryDocumentRepository()
    document = make_document(status=DocumentStatus.INDEXING)
    await repo.create_document(document)

    with pytest.raises(HTTPException) as caught:
        await delete_document(document.id, repo=repo, storage=SimpleNamespace(), task=FakeTask())

    assert caught.value.status_code == 409
    assert (await repo.get_document(document.id)).status == DocumentStatus.INDEXING.value


@pytest.mark.asyncio
async def test_delete_queue_failure_preserves_retryable_delete_failed_state():
    repo = InMemoryDocumentRepository()
    document = make_document()
    await repo.create_document(document)

    with pytest.raises(HTTPException) as caught:
        await delete_document(
            document.id,
            repo=repo,
            storage=SimpleNamespace(),
            task=FakeTask(RuntimeError("redis is unavailable")),
        )

    assert caught.value.status_code == 503
    persisted = await repo.get_document(document.id)
    assert persisted is not None
    assert persisted.status == DocumentStatus.DELETE_FAILED.value
    assert persisted.stage == "deletion_failed"
    assert persisted.retrieval_enabled is False
    assert persisted.error == "Document deletion could not be queued."


@pytest.mark.asyncio
async def test_update_rejects_unknown_folder_before_mutating_document():
    repo = InMemoryDocumentRepository()
    document = make_document()
    await repo.create_document(document)
    unknown_folder = uuid4()

    with pytest.raises(HTTPException) as caught:
        await update_document(
            document.id,
            DocumentUpdate(folder_id=unknown_folder),
            repo=repo,
            task=FakeTask(),
        )

    assert caught.value.status_code == 404
    assert (await repo.get_document(document.id)).folder_id is None
