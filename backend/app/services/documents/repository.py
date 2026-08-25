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


@runtime_checkable
class DocumentRepository(Protocol):
    async def create_folder(self, folder: Folder) -> Folder: ...

    async def list_folders(self) -> list[Folder]: ...

    async def get_folder(self, folder_id: UUID) -> Folder | None: ...

    async def update_folder(self, folder: Folder) -> Folder: ...

    async def delete_folder(self, folder_id: UUID) -> None: ...

    async def create_document(self, document: Document) -> Document: ...

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

    async def delete_document(self, document_id: UUID) -> None: ...

    async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob: ...

    async def get_ingestion_job(self, job_id: UUID) -> IngestionJob | None: ...

    async def list_ingestion_jobs(self, document_id: UUID) -> list[IngestionJob]: ...

    async def update_ingestion_job(self, job: IngestionJob) -> IngestionJob: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_document(document: Document) -> None:
    if document.category not in {item.value for item in DocumentCategory}:
        raise ValueError("Unsupported document category.")
    if document.status == DocumentStatus.QUEUED.value and document.category == "news":
        raise ValueError("News documents are not supported.")


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
            and item.status != DocumentStatus.DELETING.value
            for item in self.documents.values()
        ):
            existing = next(
                item for item in self.documents.values()
                if item.checksum == document.checksum and item.category == document.category
                and item.status != DocumentStatus.DELETING.value
            )
            raise DuplicateDocumentError(existing.id)
        if document.folder_id is not None and document.folder_id not in self.folders:
            raise FolderNotFoundError(str(document.folder_id))
        self.documents[document.id] = document
        return document

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
        document.updated_at = _now()
        self.documents[document.id] = document
        return document

    async def delete_document(self, document_id: UUID) -> None:
        if document_id not in self.documents:
            raise DocumentNotFoundError(str(document_id))
        self.documents[document_id].status = DocumentStatus.DELETING.value
        self.documents[document_id].updated_at = _now()

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
        if existing and existing.status != DocumentStatus.DELETING.value:
            raise DuplicateDocumentError(existing.id)
        if document.folder_id is not None and not self.session.get(Folder, document.folder_id):
            raise FolderNotFoundError(str(document.folder_id))
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        return document

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
        document.updated_at = _now()
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        return document

    async def delete_document(self, document_id: UUID) -> None:
        document = self.session.get(Document, document_id)
        if not document:
            raise DocumentNotFoundError(str(document_id))
        document.status = DocumentStatus.DELETING.value
        document.updated_at = _now()
        self.session.add(document)
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
