"""Deletion task tests — updated for renamed remote_vector_store_file_id."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.config import settings
from app.models.documents import (
    Document,
    DocumentCategory,
    DocumentDeleteTaskPayload,
    DocumentStatus,
)


def make_document(*, status: DocumentStatus = DocumentStatus.DELETING) -> Document:
    return Document(
        original_filename="brief.pdf",
        display_name="brief.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=1,
        checksum=str(uuid4()),
        category=DocumentCategory.LEGISLATIVE_QA.value,
        storage_key="ignored",
        status=status.value,
        stage="deletion_queued",
        remote_file_id="file-source",
        # New name for the attachment (vector-store file) ID:
        remote_vector_store_file_id="vector-file",
    )


@pytest.mark.asyncio
async def test_delete_worker_orders_remote_local_and_catalog_cleanup(tmp_path, monkeypatch):
    from app.tasks import documents as task_module

    document = make_document()
    payload = DocumentDeleteTaskPayload(
        document_id=document.id,
        remote_file_id=document.remote_file_id,
        remote_vector_store_file_id=document.remote_vector_store_file_id,
    )
    events: list[str] = []

    class FakeDeletionService:
        async def delete_remote(
            self,
            *,
            remote_file_id,
            remote_vector_store_file_id,
            remote_vector_store_id,
        ):
            assert remote_file_id == "file-source"
            assert remote_vector_store_file_id == "vector-file"
            events.append("remote")

    async def fake_local_delete(_storage, document_id):
        assert document_id == document.id
        events.append("local")

    monkeypatch.setattr(task_module, "init_db", lambda: None)
    monkeypatch.setattr(task_module, "_document_for_deletion", lambda _id: document)
    monkeypatch.setattr(task_module, "_deletion_service", FakeDeletionService())
    monkeypatch.setattr(task_module, "_hard_delete_document", lambda _id: events.append("catalog"))
    monkeypatch.setattr(task_module.LocalDocumentStorage, "delete", fake_local_delete)
    monkeypatch.setattr(settings, "documents_root", tmp_path)

    result = await task_module.delete_document_task.original_func(payload)

    assert result == {"document_id": str(document.id), "status": "deleted"}
    assert events == ["remote", "local", "catalog"]


@pytest.mark.asyncio
async def test_delete_worker_marks_failure_safely_then_allows_idempotent_retry(tmp_path, monkeypatch):
    from app.tasks import documents as task_module

    document = make_document()
    payload = DocumentDeleteTaskPayload(document_id=document.id)
    service = SimpleNamespace(fail=True)

    class FakeDeletionService:
        async def delete_remote(self, **_kwargs):
            if service.fail:
                raise RuntimeError("provider credentials leaked here")

    async def fake_local_delete(_storage, _document_id):
        return None

    def mark_failed(_document_id):
        document.status = DocumentStatus.DELETE_FAILED.value
        document.stage = "deletion_failed"
        document.error = "Document deletion failed."

    monkeypatch.setattr(task_module, "init_db", lambda: None)
    monkeypatch.setattr(task_module, "_document_for_deletion", lambda _id: document)
    monkeypatch.setattr(task_module, "_deletion_service", FakeDeletionService())
    monkeypatch.setattr(task_module, "_mark_delete_failed", mark_failed)
    monkeypatch.setattr(task_module, "_hard_delete_document", lambda _id: None)
    monkeypatch.setattr(task_module.LocalDocumentStorage, "delete", fake_local_delete)
    monkeypatch.setattr(settings, "documents_root", tmp_path)

    failed = await task_module.delete_document_task.original_func(payload)
    assert failed == {"document_id": str(document.id), "status": DocumentStatus.DELETE_FAILED.value}
    assert document.error == "Document deletion failed."

    service.fail = False
    retried = await task_module._delete_document(payload)
    assert retried == {"document_id": str(document.id), "status": "deleted"}


@pytest.mark.asyncio
async def test_provider_not_found_is_idempotent(monkeypatch):
    from app.tasks import documents as task_module

    calls: list[tuple[str, dict[str, str]]] = []

    class NotFoundError(Exception):
        status_code = 404

    class FakeVectorFiles:
        def delete(self, **kwargs):
            calls.append(("vector", kwargs))
            raise NotFoundError("already detached")

    class FakeFiles:
        def delete(self, **kwargs):
            calls.append(("file", kwargs))
            raise NotFoundError("already deleted")

    client = SimpleNamespace(
        vector_stores=SimpleNamespace(files=FakeVectorFiles()),
        files=FakeFiles(),
    )
    monkeypatch.setattr(task_module, "OpenAI", lambda **_kwargs: client)
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "openai_vector_store_id", "vector-store")

    await task_module.OpenAIDocumentDeletionService().delete_remote(
        remote_file_id="file-source",
        remote_vector_store_file_id="vector-file",
        remote_vector_store_id="vector-store",
    )

    assert calls == [
        ("vector", {"file_id": "vector-file", "vector_store_id": "vector-store"}),
        ("file", {"file_id": "file-source"}),
    ]


@pytest.mark.asyncio
async def test_deletion_prefers_persisted_vector_store_over_legacy_env(monkeypatch):
    from app.tasks import documents as task_module

    calls: list[dict[str, str]] = []

    class FakeVectorFiles:
        def delete(self, **kwargs):
            calls.append(kwargs)

    class FakeFiles:
        def delete(self, **_kwargs):
            return None

    client = SimpleNamespace(
        vector_stores=SimpleNamespace(files=FakeVectorFiles()),
        files=FakeFiles(),
    )
    monkeypatch.setattr(task_module, "OpenAI", lambda **_kwargs: client)
    monkeypatch.setattr(
        task_module,
        "_retrieval_registry",
        SimpleNamespace(get_ready_id=lambda: "db-vector-store"),
    )
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "openai_vector_store_id", "legacy-env-vector-store")

    await task_module.OpenAIDocumentDeletionService().delete_remote(
        remote_file_id=None,
        remote_vector_store_file_id="vector-file",
        remote_vector_store_id="db-vector-store",
    )

    assert calls == [{"file_id": "vector-file", "vector_store_id": "db-vector-store"}]
