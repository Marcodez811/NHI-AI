"""Repository interfaces for document metadata.

The API depends on ``DocumentRepository`` rather than a global SQL session.
``SQLModelDocumentRepository`` is suitable for production wiring with a
request-scoped SQLModel ``Session``; ``InMemoryDocumentRepository`` keeps
route and service tests deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.documents import (
    Document,
    DocumentCategory,
    DocumentStatus,
    Folder,
    IngestionJob,
    utcnow,
)


class DocumentRepositoryError(RuntimeError):
    """Base class for expected document catalog failures."""


class DocumentNotFoundError(DocumentRepositoryError):
    pass


class DuplicateDocumentError(DocumentRepositoryError):
    def __init__(self, existing_id: UUID) -> None:
        super().__init__("A document with this content already exists in the category.")
        self.existing_id = existing_id


class FolderNotFoundError(DocumentRepositoryError):
    pass


class FolderNotEmptyError(DocumentRepositoryError):
    pass


class DocumentStateConflictError(DocumentRepositoryError):
    """The requested transition conflicts with a newer/active revision."""

    def __init__(self, message: str, *, code: str = "state_conflict") -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class DocumentRepository(Protocol):
    async def create_folder(self, folder: Folder) -> Folder: ...

    async def list_folders(self) -> list[Folder]: ...

    async def get_folder(self, folder_id: UUID) -> Folder | None: ...

    async def update_folder(self, folder: Folder) -> Folder: ...

    async def delete_folder(self, folder_id: UUID) -> None: ...

    async def create_document(self, document: Document) -> Document: ...

    async def create_document_with_job(self, document: Document, job: IngestionJob) -> IngestionJob: ...

    async def list_documents(
        self,
        *,
        category: DocumentCategory | None = None,
        folder_id: UUID | None = None,
        status: DocumentStatus | None = None,
        retrieval_enabled: bool | None = None,
        query: str | None = None,
    ) -> list[Document]: ...

    async def get_document(self, document_id: UUID) -> Document | None: ...

    async def update_document(self, document: Document) -> Document: ...

    async def begin_category_reindex(self, document_id: UUID, target_category: str) -> tuple[Document, IngestionJob]: ...

    async def begin_ingestion_retry(self, document_id: UUID) -> tuple[Document, IngestionJob]: ...

    async def delete_document(self, document_id: UUID) -> None: ...

    async def hard_delete_document(self, document_id: UUID) -> None: ...

    async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob: ...

    async def get_ingestion_job(self, job_id: UUID) -> IngestionJob | None: ...

    async def list_ingestion_jobs(self, document_id: UUID) -> list[IngestionJob]: ...

    async def update_ingestion_job(self, job: IngestionJob) -> IngestionJob: ...

    async def claim_ingestion(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300) -> tuple[Document, IngestionJob, bool]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_document(document: Document) -> None:
    if document.category not in {item.value for item in DocumentCategory}:
        raise ValueError("Unsupported document category.")
    if document.status == DocumentStatus.QUEUED.value and document.category == "news":
        raise ValueError("News documents are not supported.")


_DUPLICATE_EXCLUDED_STATUSES = frozenset(
    {DocumentStatus.DELETING.value, DocumentStatus.DELETE_FAILED.value}
)


def _is_active_document(document: Document) -> bool:
    """Return whether a document participates in content uniqueness."""

    return document.status not in _DUPLICATE_EXCLUDED_STATUSES


def _integrity_error_is_document_duplicate(exc: IntegrityError) -> bool:
    """Recognize the document identity index across SQLite/PostgreSQL.

    PostgreSQL reports the index/constraint name while SQLite generally reports
    the participating table and columns.  Keep this deliberately narrow so a
    foreign-key or malformed-row error is never presented as a duplicate.
    """

    detail = str(exc).lower()
    return (
        "uq_documents_checksum_category" in detail
        or (
            "documents" in detail
            and "checksum" in detail
            and "category" in detail
            and ("unique" in detail or "duplicate" in detail)
        )
    )


class InMemoryDocumentRepository:
    """Small async repository used by the default API dependency and tests."""

    def __init__(self) -> None:
        self.folders: dict[UUID, Folder] = {}
        self.documents: dict[UUID, Document] = {}
        self.ingestion_jobs: dict[UUID, IngestionJob] = {}

    async def create_folder(self, folder: Folder) -> Folder:
        if any(existing.name.casefold() == folder.name.casefold() for existing in self.folders.values()):
            raise DuplicateDocumentError(folder.id)
        self.folders[folder.id] = folder
        return folder

    async def list_folders(self) -> list[Folder]:
        return sorted(self.folders.values(), key=lambda value: value.name.casefold())

    async def get_folder(self, folder_id: UUID) -> Folder | None:
        return self.folders.get(folder_id)

    async def update_folder(self, folder: Folder) -> Folder:
        if folder.id not in self.folders:
            raise FolderNotFoundError(str(folder.id))
        folder.updated_at = _now()
        self.folders[folder.id] = folder
        return folder

    async def delete_folder(self, folder_id: UUID) -> None:
        if folder_id not in self.folders:
            raise FolderNotFoundError(str(folder_id))
        if any(doc.folder_id == folder_id for doc in self.documents.values()):
            raise FolderNotEmptyError(str(folder_id))
        del self.folders[folder_id]

    async def create_document(self, document: Document) -> Document:
        _validate_document(document)
        if any(
            item.checksum == document.checksum and item.category == document.category
            and _is_active_document(item)
            for item in self.documents.values()
        ):
            existing = next(
                item for item in self.documents.values()
                if item.checksum == document.checksum and item.category == document.category
                and _is_active_document(item)
            )
            raise DuplicateDocumentError(existing.id)
        if document.folder_id is not None and document.folder_id not in self.folders:
            raise FolderNotFoundError(str(document.folder_id))
        self.documents[document.id] = document
        return document

    async def create_document_with_job(self, document: Document, job: IngestionJob) -> IngestionJob:
        await self.create_document(document)
        try:
            return await self.create_ingestion_job(job)
        except Exception:
            # The in-memory repository has no transaction manager; compensate
            # so callers observe the same all-or-nothing transition.
            self.documents.pop(document.id, None)
            raise

    async def list_documents(self, *, category=None, folder_id=None, status=None, retrieval_enabled=None, query=None):
        values = list(self.documents.values())
        if category is not None:
            values = [item for item in values if item.category == category.value]
        if folder_id is not None:
            values = [item for item in values if item.folder_id == folder_id]
        if status is not None:
            values = [item for item in values if item.status == status.value]
        if retrieval_enabled is not None:
            values = [item for item in values if item.retrieval_enabled is retrieval_enabled]
        if query:
            query = query.casefold()
            values = [item for item in values if query in item.display_name.casefold() or query in item.original_filename.casefold()]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def get_document(self, document_id: UUID) -> Document | None:
        return self.documents.get(document_id)

    async def update_document(self, document: Document) -> Document:
        if document.id not in self.documents:
            raise DocumentNotFoundError(str(document.id))
        _validate_document(document)
        duplicate = next(
            (item for item in self.documents.values()
             if item.id != document.id and item.checksum == document.checksum
             and item.category == document.category
             and _is_active_document(item)),
            None,
        )
        if duplicate:
            raise DuplicateDocumentError(duplicate.id)
        if document.folder_id is not None and document.folder_id not in self.folders:
            raise FolderNotFoundError(str(document.folder_id))
        document.updated_at = _now()
        self.documents[document.id] = document
        return document

    async def begin_category_reindex(self, document_id: UUID, target_category: str) -> tuple[Document, IngestionJob]:
        document = self.documents.get(document_id)
        if document is None:
            raise DocumentNotFoundError(str(document_id))
        if target_category == document.category:
            return document, IngestionJob(document_id=document.id, revision=document.ingestion_revision, target_category=target_category)
        if document.status in {DocumentStatus.QUEUED.value, DocumentStatus.INDEXING.value} or document.pending_category:
            raise DocumentStateConflictError("Document indexing is already in progress.", code="indexing_in_progress")
        duplicate = next(
            (item for item in self.documents.values()
             if item.id != document.id and item.checksum == document.checksum
             and item.category == target_category
             and _is_active_document(item)),
            None,
        )
        if duplicate:
            raise DuplicateDocumentError(duplicate.id)
        document.pending_category = target_category
        document.ingestion_revision += 1
        document.status = DocumentStatus.INDEXING.value if document.remote_file_id else DocumentStatus.QUEUED.value
        document.stage = "queued"
        document.error = None
        document.updated_at = _now()
        job = IngestionJob(document_id=document.id, revision=document.ingestion_revision, target_category=target_category)
        self.ingestion_jobs[job.id] = job
        return document, job

    async def begin_ingestion_retry(self, document_id: UUID) -> tuple[Document, IngestionJob]:
        document = self.documents.get(document_id)
        if document is None:
            raise DocumentNotFoundError(str(document_id))
        if document.status not in {DocumentStatus.FAILED.value, DocumentStatus.READY.value}:
            raise DocumentStateConflictError(f"Document cannot be retried in status '{document.status}'.", code="not_retryable")
        jobs = [j for j in self.ingestion_jobs.values() if j.document_id == document_id]
        latest = max(jobs, key=lambda j: j.created_at, default=None)
        if latest and latest.status not in {DocumentStatus.FAILED.value, DocumentStatus.SUPERSEDED.value}:
            raise DocumentStateConflictError("Document has an active ingestion job.", code="not_retryable")
        document.ingestion_revision += 1
        document.status = DocumentStatus.QUEUED.value
        document.stage = "queued"
        document.error = None
        # A failed re-index has already restored the active category.  The
        # new retry targets the previously requested category when available.
        target_category = document.pending_category or document.category
        if latest and latest.target_category and latest.target_category != document.category:
            target_category = latest.target_category
        document.pending_category = target_category if target_category != document.category else None
        job = IngestionJob(document_id=document.id, revision=document.ingestion_revision, target_category=target_category)
        self.ingestion_jobs[job.id] = job
        return document, job

    async def claim_ingestion(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300) -> tuple[Document, IngestionJob, bool]:
        job = self.ingestion_jobs.get(job_id)
        if job is None:
            raise DocumentNotFoundError(str(job_id))
        document = self.documents.get(job.document_id)
        if document is None:
            raise DocumentNotFoundError(str(job.document_id))
        now = _now()
        if job.revision != document.ingestion_revision:
            job.status = DocumentStatus.SUPERSEDED.value
            job.phase = "superseded"
            return document, job, False
        if job.phase == "ready" or job.status == DocumentStatus.READY.value:
            return document, job, False
        if job.lease_expires_at and job.lease_expires_at > now and job.lease_token != lease_token:
            return document, job, False
        job.lease_token = lease_token
        job.lease_expires_at = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=timezone.utc)
        job.status = DocumentStatus.INDEXING.value
        job.phase = job.phase or "uploading"
        job.attempts += 1
        document.status = DocumentStatus.INDEXING.value
        document.stage = job.phase
        document.error = None
        return document, job, True

    async def delete_document(self, document_id: UUID) -> None:
        if document_id not in self.documents:
            raise DocumentNotFoundError(str(document_id))
        self.documents[document_id].status = DocumentStatus.DELETING.value
        self.documents[document_id].updated_at = _now()

    async def hard_delete_document(self, document_id: UUID) -> None:
        """Remove a document and its ingestion history from the catalog."""

        if document_id not in self.documents:
            return
        self.ingestion_jobs = {
            job_id: job
            for job_id, job in self.ingestion_jobs.items()
            if job.document_id != document_id
        }
        del self.documents[document_id]

    async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        self.ingestion_jobs[job.id] = job
        return job

    async def get_ingestion_job(self, job_id: UUID) -> IngestionJob | None:
        return self.ingestion_jobs.get(job_id)

    async def list_ingestion_jobs(self, document_id: UUID) -> list[IngestionJob]:
        return [job for job in self.ingestion_jobs.values() if job.document_id == document_id]

    async def update_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        if job.id not in self.ingestion_jobs:
            raise DocumentNotFoundError(str(job.id))
        job.updated_at = _now()
        self.ingestion_jobs[job.id] = job
        return job


class SQLModelDocumentRepository(InMemoryDocumentRepository):
    """SQLModel repository backed by an injected synchronous ``Session``.

    The methods retain an async interface so the API can later switch to an
    async SQLAlchemy session without changing its contracts.
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    def _translate_document_integrity_error(
        self,
        exc: IntegrityError,
        *,
        checksum: str,
        category: str,
    ) -> None:
        """Translate a unique-index race after resetting the failed session.

        A failed SQLAlchemy transaction must be rolled back before the lookup;
        otherwise even the diagnostic query raises ``PendingRollbackError``.
        If the error is unrelated to document identity, or the competing row
        is already in an excluded deletion state, preserve the original error.
        """

        self.session.rollback()
        if not _integrity_error_is_document_duplicate(exc):
            raise exc
        existing = self.session.exec(
            select(Document).where(
                Document.checksum == checksum,
                Document.category == category,
                Document.status.notin_(_DUPLICATE_EXCLUDED_STATUSES),
            )
        ).first()
        if existing is not None:
            raise DuplicateDocumentError(existing.id) from exc
        raise exc

    async def create_folder(self, folder: Folder) -> Folder:
        existing = self.session.exec(select(Folder).where(Folder.name == folder.name)).first()
        if existing:
            raise DuplicateDocumentError(existing.id)
        self.session.add(folder)
        self.session.commit()
        self.session.refresh(folder)
        return folder

    async def list_folders(self) -> list[Folder]:
        return list(self.session.exec(select(Folder).order_by(Folder.name)).all())

    async def get_folder(self, folder_id: UUID) -> Folder | None:
        return self.session.get(Folder, folder_id)

    async def update_folder(self, folder: Folder) -> Folder:
        if not self.session.get(Folder, folder.id):
            raise FolderNotFoundError(str(folder.id))
        folder.updated_at = _now()
        self.session.add(folder)
        self.session.commit()
        self.session.refresh(folder)
        return folder

    async def delete_folder(self, folder_id: UUID) -> None:
        folder = self.session.get(Folder, folder_id)
        if not folder:
            raise FolderNotFoundError(str(folder_id))
        if self.session.exec(select(Document).where(Document.folder_id == folder_id)).first():
            raise FolderNotEmptyError(str(folder_id))
        self.session.delete(folder)
        self.session.commit()

    async def create_document(self, document: Document) -> Document:
        _validate_document(document)
        existing = self.session.exec(
            select(Document).where(Document.checksum == document.checksum, Document.category == document.category)
        ).first()
        if existing and _is_active_document(existing):
            raise DuplicateDocumentError(existing.id)
        if document.folder_id is not None and not self.session.get(Folder, document.folder_id):
            raise FolderNotFoundError(str(document.folder_id))
        self.session.add(document)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self._translate_document_integrity_error(
                exc, checksum=document.checksum, category=document.category
            )
        self.session.refresh(document)
        return document

    async def create_document_with_job(self, document: Document, job: IngestionJob) -> IngestionJob:
        _validate_document(document)
        existing = self.session.exec(
            select(Document).where(Document.checksum == document.checksum, Document.category == document.category)
        ).first()
        if existing and _is_active_document(existing):
            raise DuplicateDocumentError(existing.id)
        if document.folder_id is not None and not self.session.get(Folder, document.folder_id):
            raise FolderNotFoundError(str(document.folder_id))
        if job.document_id != document.id:
            raise DocumentStateConflictError("Ingestion job does not belong to document.")
        self.session.add(document)
        self.session.add(job)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self._translate_document_integrity_error(
                exc, checksum=document.checksum, category=document.category
            )
        self.session.refresh(document)
        self.session.refresh(job)
        return job

    async def list_documents(self, *, category=None, folder_id=None, status=None, retrieval_enabled=None, query=None):
        statement = select(Document)
        if category is not None:
            statement = statement.where(Document.category == category.value)
        if folder_id is not None:
            statement = statement.where(Document.folder_id == folder_id)
        if status is not None:
            statement = statement.where(Document.status == status.value)
        if retrieval_enabled is not None:
            statement = statement.where(Document.retrieval_enabled == retrieval_enabled)
        if query:
            statement = statement.where(Document.display_name.ilike(f"%{query}%"))
        return list(self.session.exec(statement.order_by(Document.created_at.desc())).all())

    async def get_document(self, document_id: UUID) -> Document | None:
        return self.session.get(Document, document_id)

    async def update_document(self, document: Document) -> Document:
        if not self.session.get(Document, document.id):
            raise DocumentNotFoundError(str(document.id))
        _validate_document(document)
        duplicate = self.session.exec(
            select(Document).where(
                Document.id != document.id,
                Document.checksum == document.checksum,
                Document.category == document.category,
                Document.status.notin_(_DUPLICATE_EXCLUDED_STATUSES),
            )
        ).first()
        if duplicate:
            raise DuplicateDocumentError(duplicate.id)
        if document.folder_id is not None and not self.session.get(Folder, document.folder_id):
            raise FolderNotFoundError(str(document.folder_id))
        document.updated_at = _now()
        self.session.add(document)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self._translate_document_integrity_error(
                exc, checksum=document.checksum, category=document.category
            )
        self.session.refresh(document)
        return document

    async def begin_category_reindex(self, document_id: UUID, target_category: str) -> tuple[Document, IngestionJob]:
        statement = select(Document).where(Document.id == document_id).with_for_update()
        document = self.session.exec(statement).one_or_none()
        if document is None:
            raise DocumentNotFoundError(str(document_id))
        if target_category == document.category:
            raise DocumentStateConflictError("Category is unchanged.", code="category_unchanged")
        if document.status in {DocumentStatus.QUEUED.value, DocumentStatus.INDEXING.value} or document.pending_category:
            raise DocumentStateConflictError("Document indexing is already in progress.", code="indexing_in_progress")
        duplicate = self.session.exec(
            select(Document).where(
                Document.id != document.id,
                Document.checksum == document.checksum,
                Document.category == target_category,
                Document.status.notin_(_DUPLICATE_EXCLUDED_STATUSES),
            )
        ).first()
        if duplicate:
            raise DuplicateDocumentError(duplicate.id)
        document.pending_category = target_category
        document.ingestion_revision += 1
        document.status = DocumentStatus.INDEXING.value if document.remote_file_id else DocumentStatus.QUEUED.value
        document.stage = "queued"
        document.error = None
        document.updated_at = _now()
        job = IngestionJob(
            document_id=document.id,
            revision=document.ingestion_revision,
            target_category=target_category,
        )
        self.session.add(document)
        self.session.add(job)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self._translate_document_integrity_error(
                exc, checksum=document.checksum, category=target_category
            )
        self.session.refresh(document)
        self.session.refresh(job)
        return document, job

    async def begin_ingestion_retry(self, document_id: UUID) -> tuple[Document, IngestionJob]:
        document = self.session.exec(select(Document).where(Document.id == document_id).with_for_update()).one_or_none()
        if document is None:
            raise DocumentNotFoundError(str(document_id))
        if document.status not in {DocumentStatus.FAILED.value, DocumentStatus.READY.value}:
            raise DocumentStateConflictError(f"Document cannot be retried in status '{document.status}'.", code="not_retryable")
        latest = self.session.exec(
            select(IngestionJob).where(IngestionJob.document_id == document_id).order_by(IngestionJob.created_at.desc())
        ).first()
        if latest and latest.status not in {DocumentStatus.FAILED.value, DocumentStatus.SUPERSEDED.value}:
            raise DocumentStateConflictError("Document has an active ingestion job.", code="not_retryable")
        document.ingestion_revision += 1
        document.status = DocumentStatus.QUEUED.value
        document.stage = "queued"
        document.error = None
        # A failed re-index is restored to the active category before retry.
        target_category = document.pending_category or document.category
        if latest and latest.target_category and latest.target_category != document.category:
            target_category = latest.target_category
        document.pending_category = target_category if target_category != document.category else None
        job = IngestionJob(document_id=document.id, revision=document.ingestion_revision, target_category=target_category)
        self.session.add(document)
        self.session.add(job)
        self.session.commit()
        self.session.refresh(document)
        self.session.refresh(job)
        return document, job

    async def claim_ingestion(self, job_id: UUID, *, lease_token: str, lease_seconds: int = 300) -> tuple[Document, IngestionJob, bool]:
        now = _now()
        job = self.session.exec(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()).one_or_none()
        if job is None:
            raise DocumentNotFoundError(str(job_id))
        document = self.session.exec(select(Document).where(Document.id == job.document_id).with_for_update()).one_or_none()
        if document is None:
            raise DocumentNotFoundError(str(job.document_id))
        if job.revision != document.ingestion_revision:
            job.status = DocumentStatus.SUPERSEDED.value
            job.phase = "superseded"
            self.session.add(job)
            self.session.commit()
            return document, job, False
        if job.phase == "ready" or job.status == DocumentStatus.READY.value:
            return document, job, False
        if job.lease_expires_at and job.lease_expires_at > now and job.lease_token != lease_token:
            self.session.rollback()
            return document, job, False
        job.lease_token = lease_token
        job.lease_expires_at = datetime.fromtimestamp(now.timestamp() + lease_seconds, tz=timezone.utc)
        job.status = DocumentStatus.INDEXING.value
        job.phase = job.phase or "uploading"
        job.attempts += 1
        document.status = DocumentStatus.INDEXING.value
        document.stage = job.phase
        document.error = None
        self.session.add(document)
        self.session.add(job)
        self.session.commit()
        self.session.refresh(document)
        self.session.refresh(job)
        return document, job, True

    async def delete_document(self, document_id: UUID) -> None:
        document = self.session.get(Document, document_id)
        if not document:
            raise DocumentNotFoundError(str(document_id))
        document.status = DocumentStatus.DELETING.value
        document.updated_at = _now()
        self.session.add(document)
        self.session.commit()

    async def hard_delete_document(self, document_id: UUID) -> None:
        """Delete ingestion rows before the catalog row.

        This is deliberately separate from ``delete_document``: the latter
        remains the lightweight status transition used by older callers,
        while workers call this only after remote and local cleanup succeeds.
        """

        document = self.session.get(Document, document_id)
        if not document:
            return
        jobs = self.session.exec(
            select(IngestionJob).where(IngestionJob.document_id == document_id)
        ).all()
        for job in jobs:
            self.session.delete(job)
        self.session.delete(document)
        self.session.commit()

    async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    async def get_ingestion_job(self, job_id: UUID) -> IngestionJob | None:
        return self.session.get(IngestionJob, job_id)

    async def list_ingestion_jobs(self, document_id: UUID) -> list[IngestionJob]:
        return list(
            self.session.exec(
                select(IngestionJob)
                .where(IngestionJob.document_id == document_id)
                .order_by(IngestionJob.created_at.desc())
            ).all()
        )

    async def update_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        if not self.session.get(IngestionJob, job.id):
            raise DocumentNotFoundError(str(job.id))
        job.updated_at = _now()
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job
