"""Taskiq ingestion entrypoint.

The task owns the asynchronous boundary.  Provider-specific parsing/indexing
can be injected through ``DocumentIngestionService`` when the vector-store
adapter is wired; this module still enforces the product's no-news invariant
and gives callers a stable result shape.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from app.broker import documents_broker
from app.config import settings
from app.db import engine, init_db
from app.models.documents import (
    Document,
    DocumentCategory,
    DocumentDeleteTaskPayload,
    DocumentStatus,
    DocumentTaskPayload,
    IngestionJob,
)
from app.services.documents.storage import DocumentStorageError, LocalDocumentStorage
from app.services.retrieval.registry import RetrievalIndexRegistry
from app.models.retrieval import RetrievalIndexState
from sqlmodel import Session, select


class DocumentIngestionService(Protocol):
    async def ingest(self, payload: DocumentTaskPayload) -> None: ...


class DocumentDeletionService(Protocol):
    async def delete_remote(
        self,
        *,
        remote_file_id: str | None,
        remote_vector_store_id: str | None,
    ) -> None: ...


class NewsDocumentError(ValueError):
    """Raised whenever a source attempts to enter the retrieval index as news."""


_retrieval_registry = RetrievalIndexRegistry()


def _runtime_vector_store_id() -> str | None:
    """Read the durable store ID, retaining a compatibility env fallback."""

    try:
        get_record = getattr(_retrieval_registry, "get_record", None)
        if callable(get_record) and get_record() is not None:
            return _retrieval_registry.get_ready_id()
        return _retrieval_registry.get_ready_id() or settings.openai_vector_store_id
    except Exception:
        # A worker can receive a task during the short window before the new
        # table is available.  Existing deployments with an explicit store ID
        # can still clean up already-indexed files in that window.
        return settings.openai_vector_store_id


async def _ensure_runtime_vector_store_id() -> str:
    """Adopt/create the primary store and return its persisted ID."""

    # A worker can begin consuming an upload while the API is still creating
    # the shared store.  Wait briefly for that other process to finish its
    # lease instead of failing the first document immediately.
    for attempt in range(20):
        record = await asyncio.to_thread(_retrieval_registry.ensure_ready)
        if record.state == RetrievalIndexState.READY.value and record.vector_store_id:
            return record.vector_store_id
        if record.state != RetrievalIndexState.PROVISIONING.value or attempt == 19:
            raise RuntimeError("Document retrieval index is not available.")
        await asyncio.sleep(0.5)
    raise RuntimeError("Document retrieval index is not available.")


def set_retrieval_index_registry(registry: RetrievalIndexRegistry) -> None:
    """Inject the application registry, primarily for worker startup/tests."""

    global _retrieval_registry
    _retrieval_registry = registry


class NoopDocumentIngestionService:
    """Safe local scaffold; production wiring supplies the vector-store service."""

    async def ingest(self, payload: DocumentTaskPayload) -> None:
        if payload.category not in set(DocumentCategory):
            raise NewsDocumentError("News documents are not supported.")


def _is_not_found_error(exc: Exception) -> bool:
    """Recognize provider 404s without depending on an SDK exception class."""

    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    return status_code == 404


class OpenAIDocumentDeletionService:
    """Detach provider resources before the local catalog row is removed."""

    async def delete_remote(
        self,
        *,
        remote_file_id: str | None,
        remote_vector_store_id: str | None,
    ) -> None:
        if not remote_file_id and not remote_vector_store_id:
            return
        if not settings.openai_api_key:
            raise RuntimeError("Document retrieval provider is not configured.")
        vector_store_id = _runtime_vector_store_id()
        if remote_vector_store_id and not vector_store_id:
            raise RuntimeError("Document retrieval index is not configured.")

        client = OpenAI(api_key=settings.openai_api_key)
        if remote_vector_store_id:
            try:
                await asyncio.to_thread(
                    client.vector_stores.files.delete,
                    file_id=remote_vector_store_id,
                    vector_store_id=vector_store_id,
                )
            except Exception as exc:
                # A previously detached vector-store file is already in the
                # desired state and should not make retries fail forever.
                if not _is_not_found_error(exc):
                    raise
        if remote_file_id:
            try:
                await asyncio.to_thread(client.files.delete, file_id=remote_file_id)
            except Exception as exc:
                if not _is_not_found_error(exc):
                    raise


class OpenAIDocumentIngestionService:
    """Upload one source and attach it to the configured, news-free index."""

    async def ingest(self, payload: DocumentTaskPayload) -> None:
        if payload.category not in set(DocumentCategory):
            raise NewsDocumentError("News documents are not supported.")
        if not settings.openai_api_key:
            raise RuntimeError("Document retrieval provider is not configured.")
        vector_store_id = await _ensure_runtime_vector_store_id()

        def load_source_and_mark_indexing() -> tuple[Path, str]:
            with Session(engine) as session:
                document = session.get(Document, payload.document_id)
                job = session.get(IngestionJob, payload.ingestion_job_id)
                if document is None or job is None:
                    raise RuntimeError("Document ingestion record was not found.")
                document.status = DocumentStatus.INDEXING.value
                document.stage = "uploading"
                document.error = None
                job.status = DocumentStatus.INDEXING.value
                job.stage = "uploading"
                job.attempts += 1
                session.add(document)
                session.add(job)
                session.commit()
                source = LocalDocumentStorage(settings.documents_root).resolve(document.id)
                return source, document.original_filename

        source, filename = await asyncio.to_thread(load_source_and_mark_indexing)

        def upload_and_attach() -> tuple[str, str]:
            from openai import OpenAI

            client = OpenAI(api_key=settings.openai_api_key)
            with source.open("rb") as handle:
                uploaded = client.files.create(file=handle, purpose="assistants")
            attributes = {
                "filename": filename,
                "document_id": str(payload.document_id),
                "qa_set": payload.category.value,
                "content_type": "markdown_wiki" if payload.category is DocumentCategory.BEI_CAN else "source",
                "is_news_source": "false",
            }
            attached = client.vector_stores.files.create(
                vector_store_id=vector_store_id,
                file_id=uploaded.id,
                attributes=attributes,
            )
            return str(uploaded.id), str(getattr(attached, "id", uploaded.id))

        remote_file_id, remote_vector_store_file_id = await asyncio.to_thread(upload_and_attach)
        def mark_ready() -> None:
            with Session(engine) as session:
                document = session.get(Document, payload.document_id)
                job = session.get(IngestionJob, payload.ingestion_job_id)
                if document is None or job is None:
                    return
                document.remote_file_id = remote_file_id
                document.remote_vector_store_id = remote_vector_store_file_id
                document.status = DocumentStatus.READY.value
                document.stage = "ready"
                document.error = None
                job.status = DocumentStatus.READY.value
                job.stage = "ready"
                job.error = None
                session.add(document)
                session.add(job)
                session.commit()

        await asyncio.to_thread(mark_ready)


_ingestion_service: DocumentIngestionService = OpenAIDocumentIngestionService()
_deletion_service: DocumentDeletionService = OpenAIDocumentDeletionService()


def set_document_ingestion_service(service: DocumentIngestionService) -> None:
    """Replace the provider adapter in application/worker startup or tests."""

    global _ingestion_service
    _ingestion_service = service


def set_document_deletion_service(service: DocumentDeletionService) -> None:
    """Replace the provider deletion adapter in application startup/tests."""

    global _deletion_service
    _deletion_service = service


def _document_for_deletion(document_id) -> Document | None:
    with Session(engine) as session:
        return session.get(Document, document_id)


def _mark_delete_failed(document_id) -> None:
    with Session(engine) as session:
        document = session.get(Document, document_id)
        if document is None:
            return
        document.retrieval_enabled = False
        document.status = DocumentStatus.DELETE_FAILED.value
        document.stage = "deletion_failed"
        document.error = "Document deletion failed."
        document.updated_at = datetime.now(timezone.utc)
        session.add(document)
        session.commit()


def _hard_delete_document(document_id) -> None:
    """Remove dependent ingestion rows before deleting the catalog row."""

    with Session(engine) as session:
        document = session.get(Document, document_id)
        if document is None:
            return
        jobs = session.exec(
            select(IngestionJob).where(IngestionJob.document_id == document_id)
        ).all()
        for job in jobs:
            session.delete(job)
        session.delete(document)
        session.commit()


async def _delete_document(payload: DocumentDeleteTaskPayload) -> dict[str, str]:
    """Perform one idempotent deletion attempt and return a safe result."""

    document = await asyncio.to_thread(_document_for_deletion, payload.document_id)
    if document is None:
        # A replay after successful cleanup is intentionally harmless.
        return {"document_id": str(payload.document_id), "status": "deleted"}
    if document.status not in {
        DocumentStatus.DELETING.value,
        DocumentStatus.DELETE_FAILED.value,
    }:
        # A stale task must never delete a document that has since returned
        # to an active ingestion state.
        return {"document_id": str(payload.document_id), "status": document.status}

    remote_file_id = payload.remote_file_id or document.remote_file_id
    remote_vector_store_id = payload.remote_vector_store_id or document.remote_vector_store_id
    await _deletion_service.delete_remote(
        remote_file_id=remote_file_id,
        remote_vector_store_id=remote_vector_store_id,
    )
    try:
        await LocalDocumentStorage(settings.documents_root).delete(payload.document_id)
    except DocumentStorageError:
        raise
    await asyncio.to_thread(_hard_delete_document, payload.document_id)
    return {"document_id": str(payload.document_id), "status": "deleted"}


@documents_broker.task(task_name="documents.ingest")
async def ingest_document_task(payload: DocumentTaskPayload) -> dict[str, str]:
    if payload.category not in set(DocumentCategory):
        raise NewsDocumentError("News documents are not supported.")
    # The worker may start before the API process.  Creating the small set of
    # catalog tables here keeps ingestion safe during a cold deployment while
    # remaining a no-op for an already migrated PostgreSQL database.
    await asyncio.to_thread(init_db)
    try:
        await _ingestion_service.ingest(payload)
    except Exception as exc:
        def mark_failed() -> None:
            with Session(engine) as session:
                document = session.get(Document, payload.document_id)
                job = session.get(IngestionJob, payload.ingestion_job_id)
                if document is not None:
                    document.status = DocumentStatus.FAILED.value
                    document.stage = "failed"
                    document.error = "Document indexing failed."
                    session.add(document)
                if job is not None:
                    job.status = DocumentStatus.FAILED.value
                    job.stage = "failed"
                    job.error = "Document indexing failed."
                    session.add(job)
                session.commit()

        try:
            await asyncio.to_thread(mark_failed)
        except Exception:
            pass
        raise RuntimeError("Document indexing failed.") from exc
    return {
        "document_id": str(payload.document_id),
        "ingestion_job_id": str(payload.ingestion_job_id),
        "status": "ready",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


@documents_broker.task(task_name="documents.delete")
async def delete_document_task(payload: DocumentDeleteTaskPayload) -> dict[str, str]:
    """Detach remote resources, remove local bytes, then delete catalog data.

    Deletion failures are converted into a stable terminal result after the
    document row is marked ``delete_failed``.  That keeps provider/SDK details
    out of the Taskiq result and lets a later DELETE enqueue an idempotent
    retry.  A failure to persist the failure state is re-raised because the
    catalog's truth must not be silently lost.
    """

    if not isinstance(payload, DocumentDeleteTaskPayload):
        payload = DocumentDeleteTaskPayload.model_validate(payload)
    await asyncio.to_thread(init_db)
    try:
        return await _delete_document(payload)
    except Exception as exc:
        try:
            await asyncio.to_thread(_mark_delete_failed, payload.document_id)
        except Exception as mark_exc:
            raise RuntimeError("Document deletion failed.") from mark_exc
        _ = exc
        return {
            "document_id": str(payload.document_id),
            "status": DocumentStatus.DELETE_FAILED.value,
        }
