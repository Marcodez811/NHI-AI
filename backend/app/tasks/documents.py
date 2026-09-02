"""Taskiq ingestion entrypoint.

The task owns the asynchronous boundary.  Provider-specific parsing/indexing
can be injected through ``DocumentIngestionService`` when the vector-store
adapter is wired; this module still enforces the product's no-news invariant
and gives callers a stable result shape.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from openai import OpenAI
from app.broker import documents_broker
from app.config import settings
from app.db import engine, init_db  # compatibility export for older worker tests
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

logger = logging.getLogger(__name__)


class DocumentIngestionService(Protocol):
    async def ingest(self, payload: DocumentTaskPayload) -> None: ...


class DocumentDeletionService(Protocol):
    async def delete_remote(
        self,
        *,
        remote_file_id: str | None,
        remote_vector_store_file_id: str | None,
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


async def _cleanup_candidate_resources(
    client: Any,
    *,
    candidate_remote_file_id: str | None,
    candidate_remote_vector_store_file_id: str | None,
    vector_store_id: str | None,
) -> None:
    """Best-effort cleanup of candidate remote resources after a failed ingestion."""

    if candidate_remote_vector_store_file_id and vector_store_id:
        try:
            await asyncio.to_thread(
                client.vector_stores.files.delete,
                file_id=candidate_remote_vector_store_file_id,
                vector_store_id=vector_store_id,
            )
        except Exception:
            logger.debug("candidate vector-store file cleanup failed", exc_info=True)

    if candidate_remote_file_id:
        try:
            await asyncio.to_thread(client.files.delete, file_id=candidate_remote_file_id)
        except Exception:
            logger.debug("candidate file cleanup failed", exc_info=True)


# Type alias used by cleanup helper — avoids a circular import with openai.
from typing import Any


class OpenAIDocumentDeletionService:
    """Detach provider resources before the local catalog row is removed."""

    async def delete_remote(
        self,
        *,
        remote_file_id: str | None,
        remote_vector_store_file_id: str | None,
        remote_vector_store_id: str | None,
    ) -> None:
        if not remote_file_id and not remote_vector_store_file_id:
            return
        if not settings.openai_api_key:
            raise RuntimeError("Document retrieval provider is not configured.")
        vector_store_id = _runtime_vector_store_id()

        client = OpenAI(api_key=settings.openai_api_key)

        # Detach the vector-store attachment using the persisted owning store.
        # Fall back to the registry store only for legacy rows that predate
        # the explicit remote_vector_store_id column.
        owning_store = remote_vector_store_id or vector_store_id
        if remote_vector_store_file_id and owning_store:
            try:
                await asyncio.to_thread(
                    client.vector_stores.files.delete,
                    file_id=remote_vector_store_file_id,
                    vector_store_id=owning_store,
                )
            except Exception as exc:
                if not _is_not_found_error(exc):
                    raise
        if remote_file_id:
            try:
                await asyncio.to_thread(client.files.delete, file_id=remote_file_id)
            except Exception as exc:
                if not _is_not_found_error(exc):
                    raise


@dataclass
class _Claim:
    source: Path | None
    filename: str
    target_category: str
    vector_store_id: str | None
    candidate_file_id: str | None
    candidate_vs_file_id: str | None
    candidate_store_id: str | None
    cleanup_only: bool = False
    stale: bool = False


def _claim_job(payload: DocumentTaskPayload, *, lease_token: str, lease_seconds: int = 300) -> _Claim:
    """Atomically claim a revision and snapshot all state needed by a worker."""

    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        job = session.exec(select(IngestionJob).where(IngestionJob.id == payload.ingestion_job_id).with_for_update()).one_or_none()
        document = session.exec(select(Document).where(Document.id == payload.document_id).with_for_update()).one_or_none()
        if job is None or document is None:
            raise RuntimeError("Document ingestion record was not found.")
        if job.revision != document.ingestion_revision:
            job.status = DocumentStatus.SUPERSEDED.value
            job.phase = "superseded"
            session.add(job)
            session.commit()
            return _Claim(None, "", "", None, job.candidate_remote_file_id, job.candidate_remote_vector_store_file_id, job.candidate_remote_vector_store_id, stale=True)
        if job.phase == "ready":
            return _Claim(None, "", job.target_category or document.category, document.remote_vector_store_id, None, None, None)
        if job.lease_expires_at and job.lease_expires_at > now and job.lease_token != lease_token:
            return _Claim(None, "", job.target_category or document.category, document.remote_vector_store_id, None, None, None)
        cleanup_only = job.phase == "cleanup_pending"
        job.lease_token = lease_token
        job.lease_expires_at = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=timezone.utc)
        job.status = DocumentStatus.INDEXING.value
        job.phase = job.phase or "uploading"
        job.attempts += 1
        if not cleanup_only:
            document.status = DocumentStatus.INDEXING.value
            document.stage = job.phase
        document.error = None
        session.add(document)
        session.add(job)
        session.commit()
        source = None if cleanup_only else LocalDocumentStorage(settings.documents_root).resolve(document.id)
        return _Claim(
            source,
            document.original_filename,
            job.target_category or payload.category.value,
            document.remote_vector_store_id,
            job.candidate_remote_file_id,
            job.candidate_remote_vector_store_file_id,
            job.candidate_remote_vector_store_id,
            cleanup_only=cleanup_only,
        )


def _persist_candidate(payload: DocumentTaskPayload, *, lease_token: str, phase: str, file_id: str | None = None, vs_file_id: str | None = None, store_id: str | None = None) -> bool:
    with Session(engine) as session:
        job = session.exec(select(IngestionJob).where(IngestionJob.id == payload.ingestion_job_id).with_for_update()).one_or_none()
        document = session.get(Document, payload.document_id)
        if job is None or document is None or job.revision != document.ingestion_revision or job.lease_token != lease_token:
            return False
        if file_id is not None:
            job.candidate_remote_file_id = file_id
        if vs_file_id is not None:
            job.candidate_remote_vector_store_file_id = vs_file_id
        if store_id is not None:
            job.candidate_remote_vector_store_id = store_id
        job.phase = phase
        job.stage = phase
        job.status = DocumentStatus.INDEXING.value
        session.add(job)
        session.commit()
        return True


def _promote_job(payload: DocumentTaskPayload, *, lease_token: str) -> tuple[bool, dict[str, str | None]]:
    """Swap the candidate into the active row and persist old cleanup IDs."""

    with Session(engine) as session:
        document = session.exec(select(Document).where(Document.id == payload.document_id).with_for_update()).one_or_none()
        job = session.exec(select(IngestionJob).where(IngestionJob.id == payload.ingestion_job_id).with_for_update()).one_or_none()
        if document is None or job is None:
            return False, {}
        if job.revision != document.ingestion_revision or job.lease_token != lease_token:
            job.status = DocumentStatus.SUPERSEDED.value
            job.phase = "superseded"
            session.add(job)
            session.commit()
            return False, {}
        if not job.candidate_remote_file_id or not job.candidate_remote_vector_store_file_id:
            raise RuntimeError("Ingestion candidate is incomplete.")
        old = {
            "file_id": document.remote_file_id,
            "vs_file_id": document.remote_vector_store_file_id,
            "store_id": document.remote_vector_store_id,
        }
        job.cleanup_remote_file_id = old["file_id"]
        job.cleanup_remote_vector_store_file_id = old["vs_file_id"]
        job.cleanup_remote_vector_store_id = old["store_id"]
        document.remote_file_id = job.candidate_remote_file_id
        document.remote_vector_store_file_id = job.candidate_remote_vector_store_file_id
        document.remote_vector_store_id = job.candidate_remote_vector_store_id
        document.category = job.target_category or payload.category.value
        document.pending_category = None
        document.status = DocumentStatus.READY.value
        document.stage = "ready"
        document.error = None
        job.candidate_remote_file_id = None
        job.candidate_remote_vector_store_file_id = None
        job.candidate_remote_vector_store_id = None
        job.status = DocumentStatus.READY.value
        job.phase = "cleanup_pending" if any(old.values()) else "ready"
        session.add(document)
        session.add(job)
        session.commit()
        return True, old


def _mark_ingestion_failed(payload: DocumentTaskPayload, *, lease_token: str | None, message: str) -> None:
    with Session(engine) as session:
        document = session.get(Document, payload.document_id)
        job = session.get(IngestionJob, payload.ingestion_job_id)
        if document is None or job is None or (lease_token is not None and job.lease_token != lease_token) or job.revision != document.ingestion_revision:
            return
        # A failed re-index must leave the previous active revision usable.
        if document.remote_file_id:
            document.status = DocumentStatus.READY.value
            document.stage = "ready"
            document.pending_category = None
        else:
            document.status = DocumentStatus.FAILED.value
            document.stage = "failed"
        document.error = message
        job.status = DocumentStatus.FAILED.value
        job.stage = "failed"
        job.phase = "failed"
        job.error = message
        session.add(document)
        session.add(job)
        session.commit()


class OpenAIDocumentIngestionService:
    """Upload one source and attach it to the configured, news-free index.

    The ingest method is phase-aware and idempotent: each phase persists its
    results before proceeding, and a Taskiq retry resumes from the last
    persisted phase instead of re-uploading.

    A revision token is checked before claiming the task and again before
    promoting results.  If the document's ingestion_revision has advanced
    since this job was created, the job is stale and cleans its own candidate
    resources without overwriting the newer state.
    """

    async def ingest(self, payload: DocumentTaskPayload) -> None:
        if payload.category not in set(DocumentCategory):
            raise NewsDocumentError("News documents are not supported.")
        if not settings.openai_api_key:
            raise RuntimeError("Document retrieval provider is not configured.")
        client = OpenAI(api_key=settings.openai_api_key)
        lease_token = uuid4().hex
        claim = await asyncio.to_thread(_claim_job, payload, lease_token=lease_token)
        if claim.stale:
            logger.info(
                "Ingestion job %s is stale (revision mismatch) — cleaning candidate resources",
                payload.ingestion_job_id,
            )
            await _cleanup_candidate_resources(
                client,
                candidate_remote_file_id=claim.candidate_file_id,
                candidate_remote_vector_store_file_id=claim.candidate_vs_file_id,
                vector_store_id=claim.candidate_store_id or _runtime_vector_store_id(),
            )
            return
        # A cleanup-only redelivery never creates a second candidate.
        if claim.cleanup_only:
            await self._cleanup_promoted_resources(
                payload,
                client,
                claim.candidate_store_id or _runtime_vector_store_id(),
            )
            return
        if claim.source is None:
            # Already ready, or another live worker owns the lease.
            return

        vector_store_id = claim.candidate_store_id or await _ensure_runtime_vector_store_id()
        source, filename, category = claim.source, claim.filename, claim.target_category
        cand_file_id, cand_vs_file_id = claim.candidate_file_id, claim.candidate_vs_file_id
        candidate_store_id = claim.candidate_store_id or vector_store_id

        try:
            # ── Phase 1: Upload ──────────────────────────────────────────────
            if not cand_file_id:
                def upload_file() -> str:
                    with source.open("rb") as handle:
                        uploaded = client.files.create(file=handle, purpose="assistants")
                    return str(uploaded.id)

                cand_file_id = await asyncio.to_thread(upload_file)
                if not await asyncio.to_thread(_persist_candidate, payload, lease_token=lease_token, phase="attaching", file_id=cand_file_id):
                    raise RuntimeError("Ingestion lease expired before upload could be persisted.")

            # ── Phase 2: Attach ──────────────────────────────────────────────
            if not cand_vs_file_id:
                doc_category = DocumentCategory(category)
                attributes = {
                    "filename": filename,
                    "document_id": str(payload.document_id),
                    "qa_set": doc_category.value,
                    "content_type": "markdown_wiki" if doc_category is DocumentCategory.BEI_CAN else "source",
                    "is_news_source": "false",
                }

                def attach_file() -> str:
                    attached = client.vector_stores.files.create(
                        vector_store_id=vector_store_id,
                        file_id=cand_file_id,
                        attributes=attributes,
                    )
                    return str(getattr(attached, "id", cand_file_id))

                cand_vs_file_id = await asyncio.to_thread(attach_file)
                if not await asyncio.to_thread(_persist_candidate, payload, lease_token=lease_token, phase="polling", vs_file_id=cand_vs_file_id, store_id=vector_store_id):
                    raise RuntimeError("Ingestion lease expired before attachment could be persisted.")

            # ── Phase 3: Poll ────────────────────────────────────────────────
            _POLL_INTERVAL = 2.0
            _MAX_POLL_ATTEMPTS = 60
            attachment_status = "in_progress"
            for _ in range(_MAX_POLL_ATTEMPTS):
                def check_status() -> str:
                    vs_file = client.vector_stores.files.retrieve(
                        file_id=cand_vs_file_id,
                        vector_store_id=vector_store_id,
                    )
                    return str(getattr(vs_file, "status", "unknown"))

                attachment_status = await asyncio.to_thread(check_status)
                if attachment_status == "completed":
                    break
                if attachment_status in {"failed", "cancelled", "unknown"}:
                    break
                await asyncio.sleep(_POLL_INTERVAL)
            else:
                attachment_status = "timeout"

            if attachment_status != "completed":
                raise RuntimeError(f"Vector-store attachment did not complete (status={attachment_status}).")

            # ── Phase 4: Promote ─────────────────────────────────────────────
            promoted, _old = await asyncio.to_thread(_promote_job, payload, lease_token=lease_token)
            if not promoted:
                await _cleanup_candidate_resources(client, candidate_remote_file_id=cand_file_id, candidate_remote_vector_store_file_id=cand_vs_file_id, vector_store_id=candidate_store_id)
                return
            await self._cleanup_promoted_resources(payload, client, vector_store_id)
        except Exception as exc:
            await _cleanup_candidate_resources(client, candidate_remote_file_id=cand_file_id, candidate_remote_vector_store_file_id=cand_vs_file_id, vector_store_id=candidate_store_id)
            await asyncio.to_thread(_mark_ingestion_failed, payload, lease_token=lease_token, message="Document indexing failed.")
            raise

    async def _cleanup_promoted_resources(self, payload: DocumentTaskPayload, client: Any, fallback_store_id: str | None) -> None:
        with Session(engine) as session:
            job = session.get(IngestionJob, payload.ingestion_job_id)
            if job is None:
                return
            file_id, vs_file_id, store_id = job.cleanup_remote_file_id, job.cleanup_remote_vector_store_file_id, job.cleanup_remote_vector_store_id or fallback_store_id
        if not file_id and not vs_file_id:
            with Session(engine) as session:
                job = session.get(IngestionJob, payload.ingestion_job_id)
                if job:
                    job.phase = "ready"
                    session.add(job)
                    session.commit()
            return
        try:
            if vs_file_id and store_id:
                try:
                    await asyncio.to_thread(client.vector_stores.files.delete, file_id=vs_file_id, vector_store_id=store_id)
                except Exception as exc:
                    if not _is_not_found_error(exc):
                        raise
            if file_id:
                try:
                    await asyncio.to_thread(client.files.delete, file_id=file_id)
                except Exception as exc:
                    if not _is_not_found_error(exc):
                        raise
            with Session(engine) as session:
                job = session.get(IngestionJob, payload.ingestion_job_id)
                if job:
                    job.cleanup_remote_file_id = None
                    job.cleanup_remote_vector_store_file_id = None
                    job.cleanup_remote_vector_store_id = None
                    job.phase = "ready"
                    session.add(job)
                    session.commit()
        except Exception:
            logger.warning("Old-resource cleanup failed for document %s", payload.document_id, exc_info=True)
            # Keep all IDs and the cleanup_pending phase for a later delivery.
            with Session(engine) as session:
                job = session.get(IngestionJob, payload.ingestion_job_id)
                if job:
                    job.phase = "cleanup_pending"
                    session.add(job)
                    session.commit()


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


def _ingestion_resources_for_deletion(document_id) -> list[tuple[str | None, str | None, str | None]]:
    """Return active, candidate, and pending-cleanup resources, deduplicated."""
    try:
        with Session(engine) as session:
            jobs = session.exec(select(IngestionJob).where(IngestionJob.document_id == document_id)).all()
            values: list[tuple[str | None, str | None, str | None]] = []
            for job in jobs:
                values.extend(
                    [
                        (job.candidate_remote_file_id, job.candidate_remote_vector_store_file_id, job.candidate_remote_vector_store_id),
                        (job.cleanup_remote_file_id, job.cleanup_remote_vector_store_file_id, job.cleanup_remote_vector_store_id),
                    ]
                )
            return list(dict.fromkeys(values))
    except Exception:
        # Rolling deployments may run a deletion task against a pre-migration
        # schema.  The active payload is still cleaned; historical resources
        # become reconciliation work once the migration is complete.
        logger.debug("Could not inspect ingestion history during deletion", exc_info=True)
        return []


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
    # Use the explicit attachment ID from the payload (captured at deletion time);
    # fall back to the current row for legacy/manual payloads.
    remote_vector_store_file_id = (
        payload.remote_vector_store_file_id
        or getattr(document, "remote_vector_store_file_id", None)
    )
    remote_vector_store_id = payload.remote_vector_store_id or document.remote_vector_store_id

    resources = [(remote_file_id, remote_vector_store_file_id, remote_vector_store_id)]
    resources.extend(await asyncio.to_thread(_ingestion_resources_for_deletion, payload.document_id))
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for file_id, vs_file_id, store_id in resources:
        item = (file_id, vs_file_id, store_id)
        if item in seen or not file_id and not vs_file_id:
            continue
        seen.add(item)
        await _deletion_service.delete_remote(
            remote_file_id=file_id,
            remote_vector_store_file_id=vs_file_id,
            remote_vector_store_id=store_id,
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
    try:
        await _ingestion_service.ingest(payload)
    except Exception as exc:
        try:
            await asyncio.to_thread(_mark_ingestion_failed, payload, lease_token=None, message="Document indexing failed.")
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
