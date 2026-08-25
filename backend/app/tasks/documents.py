"""Taskiq ingestion entrypoint.

The task owns the asynchronous boundary.  Provider-specific parsing/indexing
can be injected through ``DocumentIngestionService`` when the vector-store
adapter is wired; this module still enforces the product's no-news invariant
and gives callers a stable result shape.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol

from app.broker import broker
from app.config import settings
from app.db import engine, init_db
from app.models.documents import Document, DocumentCategory, DocumentStatus, DocumentTaskPayload, IngestionJob
from app.services.documents.storage import LocalDocumentStorage
from sqlmodel import Session


class DocumentIngestionService(Protocol):
    async def ingest(self, payload: DocumentTaskPayload) -> None: ...


class NewsDocumentError(ValueError):
    """Raised whenever a source attempts to enter the retrieval index as news."""


class NoopDocumentIngestionService:
    """Safe local scaffold; production wiring supplies the vector-store service."""

    async def ingest(self, payload: DocumentTaskPayload) -> None:
        if payload.category not in set(DocumentCategory):
            raise NewsDocumentError("News documents are not supported.")


class OpenAIDocumentIngestionService:
    """Upload one source and attach it to the configured, news-free index."""

    async def ingest(self, payload: DocumentTaskPayload) -> None:
        if payload.category not in set(DocumentCategory):
            raise NewsDocumentError("News documents are not supported.")
        if not settings.openai_vector_store_id:
            raise RuntimeError("Document retrieval index is not configured.")
        if not settings.openai_api_key:
            raise RuntimeError("Document retrieval provider is not configured.")

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
            source = LocalDocumentStorage(settings.slides_documents_root).resolve(document.id)
            filename = document.original_filename

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
                vector_store_id=settings.openai_vector_store_id,
                file_id=uploaded.id,
                attributes=attributes,
            )
            return str(uploaded.id), str(getattr(attached, "id", uploaded.id))

        remote_file_id, remote_vector_store_file_id = await asyncio.to_thread(upload_and_attach)
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


_ingestion_service: DocumentIngestionService = OpenAIDocumentIngestionService()


def set_document_ingestion_service(service: DocumentIngestionService) -> None:
    """Replace the provider adapter in application/worker startup or tests."""

    global _ingestion_service
    _ingestion_service = service


@broker.task(task_name="documents.ingest")
async def ingest_document_task(payload: DocumentTaskPayload) -> dict[str, str]:
    if payload.category not in set(DocumentCategory):
        raise NewsDocumentError("News documents are not supported.")
    # The worker may start before the API process.  Creating the small set of
    # catalog tables here keeps ingestion safe during a cold deployment while
    # remaining a no-op for an already migrated PostgreSQL database.
    init_db()
    try:
        await _ingestion_service.ingest(payload)
    except Exception as exc:
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
        raise RuntimeError("Document indexing failed.") from exc
    return {
        "document_id": str(payload.document_id),
        "ingestion_job_id": str(payload.ingestion_job_id),
        "status": "ready",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
