"""Document-domain persistence and API contracts.

The database models deliberately contain no provider-specific retrieval state
apart from opaque IDs.  This lets the API and worker share the same document
identity while keeping OpenAI/vector-store integration in the ingestion
service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field as PydanticField, field_validator
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class DocumentCategory(StrEnum):
    LEGISLATIVE_QA = "legislative_qa"
    PUBLIC_OPINION = "public_opinion"
    BEI_CAN = "bei_can"


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETE_FAILED = "delete_failed"
    SUPERSEDED = "superseded"


SUPPORTED_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".md", ".markdown", ".txt"})
SUPPORTED_DOCUMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/markdown",
        "text/plain",
        "application/octet-stream",  # browsers frequently use this for .md/.docx
    }
)
DEFAULT_MAX_UPLOAD_BYTES = 250 * 1024 * 1024


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Folder(SQLModel, table=True):
    __tablename__ = "document_folders"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True, max_length=255)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    original_filename: str = Field(max_length=512)
    display_name: str = Field(max_length=512)
    mime_type: str = Field(max_length=255)
    extension: str = Field(max_length=32)
    size_bytes: int = Field(ge=0)
    checksum: str = Field(index=True, max_length=64)
    category: str = Field(index=True, max_length=64)
    folder_id: UUID | None = Field(default=None, foreign_key="document_folders.id", index=True)
    storage_key: str = Field(max_length=1024)
    retrieval_enabled: bool = Field(default=True, index=True)
    status: str = Field(default=DocumentStatus.QUEUED.value, index=True, max_length=32)
    stage: str | None = Field(default="queued", max_length=64)
    error: str | None = Field(default=None, max_length=512)
    source_priority: int | None = Field(default=None)
    roles: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    page_count: int | None = Field(default=None)
    table_count: int | None = Field(default=None)
    # Monotonically incremented on every new ingestion attempt.  Workers must
    # compare their job's revision snapshot to this value before promoting results.
    ingestion_revision: int = Field(default=0, ge=0)
    # Set while a category re-index is in progress; cleared on successful promotion.
    pending_category: str | None = Field(default=None, max_length=64)
    # Stable OpenAI Files API file ID for the active attachment.
    remote_file_id: str | None = Field(default=None, max_length=255)
    # Vector-store attachment (file-in-store) ID for the active attachment.
    remote_vector_store_file_id: str | None = Field(default=None, max_length=255)
    # Owning vector store ID for the active attachment.
    remote_vector_store_id: str | None = Field(default=None, max_length=255)
    # Deprecated compatibility fields.  New worker state is persisted only on
    # IngestionJob; these are retained for rolling upgrades/old serializers.
    candidate_remote_file_id: str | None = Field(default=None, max_length=255)
    candidate_remote_vector_store_file_id: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)


class IngestionJob(SQLModel, table=True):
    __tablename__ = "document_ingestion_jobs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(foreign_key="documents.id", index=True)
    status: str = Field(default=DocumentStatus.QUEUED.value, max_length=32)
    attempts: int = Field(default=0, ge=0)
    # Snapshot of the document's ingestion_revision at job creation.
    # Workers verify this matches before writing results.
    revision: int = Field(default=0, ge=0)
    stage: str | None = Field(default="queued", max_length=64)
    # Granular phase within a stage (uploading/attaching/polling/promoting/cleanup_pending).
    phase: str | None = Field(default=None, max_length=64)
    # Category to activate when this revision is promoted.  For an initial
    # ingest this equals the document category; during re-index it is the
    # pending category while Document.category remains the active category.
    target_category: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=512)
    # Candidate remote IDs persisted for idempotent phase resume.
    candidate_remote_file_id: str | None = Field(default=None, max_length=255)
    candidate_remote_vector_store_file_id: str | None = Field(default=None, max_length=255)
    candidate_remote_vector_store_id: str | None = Field(default=None, max_length=255)
    # Resources belonging to the previous active revision.  Persisting these
    # before promotion makes cleanup resumable and prevents orphaned files.
    cleanup_remote_file_id: str | None = Field(default=None, max_length=255)
    cleanup_remote_vector_store_file_id: str | None = Field(default=None, max_length=255)
    cleanup_remote_vector_store_id: str | None = Field(default=None, max_length=255)
    lease_token: str | None = Field(default=None, max_length=128)
    lease_expires_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)


class FolderCreate(BaseModel):
    name: str = PydanticField(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("folder name cannot be blank")
        return value


class FolderUpdate(FolderCreate):
    pass


class FolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    display_name: str
    mime_type: str
    extension: str
    size_bytes: int
    checksum: str
    category: DocumentCategory
    folder_id: UUID | None
    retrieval_enabled: bool
    status: DocumentStatus
    stage: str | None
    error: str | None
    source_priority: int | None
    roles: list[str]
    page_count: int | None
    table_count: int | None
    # Shows an accepted category transition before promotion; None otherwise.
    pending_category: DocumentCategory | None = None
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    display_name: str | None = PydanticField(default=None, min_length=1, max_length=512)
    category: DocumentCategory | None = None
    folder_id: UUID | None = None
    retrieval_enabled: bool | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("display_name cannot be blank")
        return value


class DocumentListResponse(BaseModel):
    items: list[DocumentRead]
    total: int


class DocumentUploadResponse(DocumentRead):
    ingestion_job_id: UUID


class DocumentRetryResponse(DocumentRead):
    """Returned by the retry endpoint; includes the newly queued job ID."""

    ingestion_job_id: UUID


class IngestionJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    status: DocumentStatus
    attempts: int
    revision: int
    stage: str | None
    phase: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class DocumentTaskPayload(BaseModel):
    document_id: UUID
    ingestion_job_id: UUID
    category: DocumentCategory
    attempt: int = PydanticField(default=0, ge=0)


class DocumentDeleteTaskPayload(BaseModel):
    """Opaque, retry-safe input for asynchronous document deletion.

    The remote IDs are captured when the API accepts the deletion.  Keeping
    them on the task boundary lets cleanup continue even if a later catalog
    read changes or removes provider metadata, while the worker still falls
    back to the current row for older/manual payloads.
    """

    document_id: UUID
    remote_file_id: str | None = None
    remote_vector_store_file_id: str | None = None
    remote_vector_store_id: str | None = None


def as_document_read(document: Document) -> DocumentRead:
    """Validate and serialize a DB row without exposing provider IDs/storage keys."""

    data: dict[str, Any] = document.model_dump()
    return DocumentRead.model_validate(data)
