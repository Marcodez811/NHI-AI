"""Document and folder management API."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.config import settings
from app.models.documents import (
    Document,
    DocumentCategory,
    DocumentDeleteTaskPayload,
    DocumentListResponse,
    DocumentRead,
    DocumentRetryResponse,
    DocumentStatus,
    DocumentTaskPayload,
    DocumentUpdate,
    DocumentUploadResponse,
    Folder,
    FolderCreate,
    FolderRead,
    FolderUpdate,
    IngestionJob,
    IngestionJobRead,
    as_document_read,
)
from app.services.documents.repository import (
    DocumentRepository,
    DocumentNotFoundError,
    DuplicateDocumentError,
    FolderNotEmptyError,
    FolderNotFoundError,
    InMemoryDocumentRepository,
    DocumentStateConflictError,
)
from app.services.documents.storage import (
    DocumentStorage,
    DocumentStorageError,
    DocumentTooLargeError,
    LocalDocumentStorage,
    UnsupportedDocumentError,
)
from app.tasks.documents import delete_document_task, ingest_document_task

router = APIRouter(prefix="/documents", tags=["documents"])

_repository = InMemoryDocumentRepository()


def get_document_repository() -> DocumentRepository:
    """Default dependency; production should override with a SQLModel session repository."""

    return _repository


def get_document_storage() -> DocumentStorage:
    return LocalDocumentStorage(settings.documents_root)


def get_ingestion_task() -> Any:
    return ingest_document_task


def get_document_delete_task() -> Any:
    return delete_document_task


def _not_found(message: str = "Document was not found.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def _map_repository_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (DocumentNotFoundError, FolderNotFoundError)):
        return _not_found()
    if isinstance(exc, DuplicateDocumentError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "existing_document_id": str(exc.existing_id)},
        )
    if isinstance(exc, DocumentStateConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, FolderNotEmptyError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Folder is not empty.")
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Document catalog is unavailable.")


async def _queue_ingestion(
    repo: DocumentRepository,
    task: Any,
    document: Document,
    *,
    job: IngestionJob | None = None,
) -> IngestionJob:
    """Create an ingestion job and publish it atomically.

    The job is created in the database before the queue publish.  If the
    queue call fails, both the document and the job are marked FAILED, but
    local source bytes are intentionally preserved so the retry endpoint can
    re-queue them without re-uploading.
    """

    if job is None:
        job = IngestionJob(
            document_id=document.id,
            revision=document.ingestion_revision,
            target_category=document.pending_category or document.category,
        )
        await repo.create_ingestion_job(job)
    try:
        await task.kicker().with_task_id(str(job.id)).kiq(
            DocumentTaskPayload(
                document_id=document.id,
                ingestion_job_id=job.id,
                category=DocumentCategory(job.target_category or document.pending_category or document.category),
            )
        )
    except Exception as exc:
        # Source bytes are intentionally NOT deleted here — they remain on disk
        # so the retry endpoint can requeue without a new upload.
        # An existing active revision remains authoritative after a failed
        # re-index publication.  Initial ingestion has no active IDs and is
        # therefore marked failed, while its source remains retryable.
        if document.remote_file_id and document.pending_category:
            document.status = DocumentStatus.READY.value
            document.stage = "ready"
            document.pending_category = None
        else:
            document.status = DocumentStatus.FAILED.value
            document.stage = "failed"
        document.error = "Document indexing could not be queued."
        await repo.update_document(document)
        job.status = DocumentStatus.FAILED.value
        job.stage = "failed"
        job.phase = "failed"
        job.error = "Document indexing could not be queued."
        await repo.update_ingestion_job(job)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Document indexing is temporarily unavailable.") from exc
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentUploadResponse)
async def upload_document(
    file: Annotated[UploadFile, File(...)],
    category: Annotated[DocumentCategory, Form(...)],
    folder_id: Annotated[UUID | None, Form()] = None,
    repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None,
    storage: Annotated[DocumentStorage, Depends(get_document_storage)] = None,
    task: Annotated[Any, Depends(get_ingestion_task)] = None,
) -> DocumentUploadResponse:
    document_id = uuid4()
    catalog_created = False
    try:
        storage_key, size_bytes, checksum = await storage.save_upload(
            document_id,
            file,
            max_bytes=settings.max_upload_bytes,
        )
        # Use the safe ASCII storage filename for `original_filename` / `storage_key`,
        # but keep the raw unicode name for `display_name` so it is readable in the UI.
        from app.services.documents.storage import safe_filename
        safe_name = safe_filename(file.filename)
        extension = Path(safe_name).suffix.lower()
        document = Document(
            id=document_id,
            original_filename=safe_name,
            display_name=(file.filename or safe_name)[:512],
            mime_type=file.content_type or "application/octet-stream",
            extension=extension,
            size_bytes=size_bytes,
            checksum=checksum,
            category=category.value,
            folder_id=folder_id,
            storage_key=storage_key,
            status=DocumentStatus.QUEUED.value,
            stage="queued",
            ingestion_revision=1,
        )
        # SQL repositories commit the document and first job together.  The
        # fallback keeps compatibility with small test repositories.
        job = IngestionJob(document_id=document.id, revision=document.ingestion_revision, target_category=document.category)
        create_with_job = getattr(repo, "create_document_with_job", None)
        if callable(create_with_job):
            await create_with_job(document, job)
        else:
            await repo.create_document(document)
            await repo.create_ingestion_job(job)
        catalog_created = True
        job = await _queue_ingestion(repo, task, document, job=job)
        return DocumentUploadResponse(**as_document_read(document).model_dump(), ingestion_job_id=job.id)
    except (UnsupportedDocumentError, DocumentTooLargeError) as exc:
        if not catalog_created:
            try:
                await storage.delete(document_id)
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DocumentStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        # Only delete local bytes if the catalog row was never committed.
        if not catalog_created:
            try:
                await storage.delete(document_id)
            except Exception:
                pass
        if isinstance(exc, HTTPException):
            raise
        raise _map_repository_error(exc) from exc


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    category: DocumentCategory | None = None,
    folder_id: UUID | None = None,
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    retrieval_enabled: bool | None = None,
    q: str | None = Query(default=None, max_length=255),
    repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None,
) -> DocumentListResponse:
    documents = await repo.list_documents(
        category=category,
        folder_id=folder_id,
        status=document_status,
        retrieval_enabled=retrieval_enabled,
        query=q,
    )
    return DocumentListResponse(items=[as_document_read(item) for item in documents], total=len(documents))


# Static folder paths must be registered before the UUID document paths.
@router.post("/folders", status_code=status.HTTP_201_CREATED, response_model=FolderRead)
async def create_folder(request: FolderCreate, repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None):
    try:
        return await repo.create_folder(Folder(name=request.name))
    except Exception as exc:
        raise _map_repository_error(exc) from exc


@router.get("/folders", response_model=list[FolderRead])
async def list_folders(repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None):
    return await repo.list_folders()


@router.patch("/folders/{folder_id}", response_model=FolderRead)
async def update_folder(folder_id: UUID, request: FolderUpdate, repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None):
    folder = await repo.get_folder(folder_id)
    if not folder:
        raise _not_found("Folder was not found.")
    folder.name = request.name
    try:
        return await repo.update_folder(folder)
    except Exception as exc:
        raise _map_repository_error(exc) from exc


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: UUID, repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None):
    try:
        await repo.delete_folder(folder_id)
    except Exception as exc:
        raise _map_repository_error(exc) from exc


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(document_id: UUID, repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None):
    document = await repo.get_document(document_id)
    if not document:
        raise _not_found()
    return as_document_read(document)


@router.patch("/{document_id}", response_model=DocumentRead)
async def update_document(
    document_id: UUID,
    request: DocumentUpdate,
    repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None,
    task: Annotated[Any, Depends(get_ingestion_task)] = None,
) -> DocumentRead:
    document = await repo.get_document(document_id)
    if not document:
        raise _not_found()
    changes = request.model_dump(exclude_unset=True)
    if "folder_id" in changes and changes["folder_id"] is not None:
        folder = await repo.get_folder(changes["folder_id"])
        if folder is None:
            raise _not_found("Folder was not found.")
    category_changed = (
        "category" in changes
        and changes["category"] is not None
        and changes["category"].value != document.category
    )
    if category_changed:
        # Reject a category change while another ingestion is active or pending.
        if document.status in {DocumentStatus.QUEUED.value, DocumentStatus.INDEXING.value}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot change category while document indexing is in progress.",
            )
        if getattr(document, "pending_category", None) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot change category while a category transition is pending.",
            )
    if "category" in changes and changes["category"] is not None:
        changes["category"] = changes["category"].value
    if category_changed and hasattr(repo, "begin_category_reindex"):
        try:
            document, job = await repo.begin_category_reindex(document.id, changes["category"])
            await _queue_ingestion(repo, task, document, job=job)
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            raise _map_repository_error(exc) from exc
        return as_document_read(document)
    for key, value in changes.items():
        setattr(document, key, value)
    if category_changed:
        document.status = DocumentStatus.QUEUED.value
        document.stage = "queued"
        document.error = None
        document.ingestion_revision = getattr(document, "ingestion_revision", 0) + 1
    try:
        await repo.update_document(document)
        if category_changed:
            await _queue_ingestion(repo, task, document)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise _map_repository_error(exc) from exc
    return as_document_read(document)


@router.delete("/{document_id}", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentRead)
async def delete_document(
    document_id: UUID,
    repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None,
    storage: Annotated[DocumentStorage, Depends(get_document_storage)] = None,
    task: Annotated[Any, Depends(get_document_delete_task)] = None,
) -> DocumentRead:
    document = await repo.get_document(document_id)
    if not document:
        raise _not_found()

    # Ingestion owns the source and provider attachment while a document is
    # queued/indexing.  Refuse deletion during that window instead of racing
    # the ingestion worker and potentially leaving an orphaned remote file.
    if document.status in {DocumentStatus.QUEUED.value, DocumentStatus.INDEXING.value}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document indexing is still in progress.",
        )
    if document.status not in {
        DocumentStatus.READY.value,
        DocumentStatus.FAILED.value,
        DocumentStatus.DELETING.value,
        DocumentStatus.DELETE_FAILED.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is not available for deletion.",
        )

    document.retrieval_enabled = False
    document.status = DocumentStatus.DELETING.value
    document.stage = "deletion_queued"
    document.error = None
    try:
        await repo.update_document(document)
        await task.kicker().with_task_id(str(document.id)).kiq(
            DocumentDeleteTaskPayload(
                document_id=document.id,
                remote_file_id=document.remote_file_id,
                remote_vector_store_file_id=getattr(document, "remote_vector_store_file_id", None),
                remote_vector_store_id=document.remote_vector_store_id,
            )
        )
    except Exception as exc:
        # The row remains available for an explicit retry.  Do not expose
        # queue/provider exception details through the public API.
        document.status = DocumentStatus.DELETE_FAILED.value
        document.stage = "deletion_failed"
        document.error = "Document deletion could not be queued."
        document.retrieval_enabled = False
        try:
            await repo.update_document(document)
        except Exception:
            pass
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document deletion is temporarily unavailable.",
        ) from exc
    return as_document_read(document)


@router.get("/{document_id}/download")
async def download_document(document_id: UUID, repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None, storage: Annotated[DocumentStorage, Depends(get_document_storage)] = None):
    document = await repo.get_document(document_id)
    if not document:
        raise _not_found()
    if document.status not in {DocumentStatus.READY.value, DocumentStatus.INDEXING.value, DocumentStatus.QUEUED.value}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document is unavailable for download.")
    try:
        source = storage.resolve(document_id)
    except DocumentStorageError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document source is unavailable.") from exc
    return FileResponse(source, media_type=document.mime_type, filename=document.original_filename)


@router.get("/{document_id}/ingestion", response_model=IngestionJobRead)
async def get_ingestion_status(document_id: UUID, repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None):
    document = await repo.get_document(document_id)
    if not document:
        raise _not_found()
    jobs = await repo.list_ingestion_jobs(document_id)
    job = max(jobs, key=lambda value: value.created_at, default=None)
    if job is None:
        raise _not_found("Ingestion job was not found.")
    return IngestionJobRead.model_validate(job)


@router.post("/{document_id}/ingestion/retry", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentRetryResponse)
async def retry_ingestion(
    document_id: UUID,
    repo: Annotated[DocumentRepository, Depends(get_document_repository)] = None,
    storage: Annotated[DocumentStorage, Depends(get_document_storage)] = None,
    task: Annotated[Any, Depends(get_ingestion_task)] = None,
) -> DocumentRetryResponse:
    """Re-queue a failed ingestion without re-uploading the source.

    Only documents in ``FAILED`` status with local source bytes still present
    can be retried.  Ready, queued, actively indexing, or deleting documents
    are rejected with ``409``.
    """

    document = await repo.get_document(document_id)
    if not document:
        raise _not_found()

    # Reject non-retryable states.
    jobs = await repo.list_ingestion_jobs(document_id)
    latest_job = max(jobs, key=lambda value: value.created_at, default=None)
    failed_reindex = (
        document.status == DocumentStatus.READY.value
        and latest_job is not None
        and latest_job.status == DocumentStatus.FAILED.value
        and latest_job.target_category
        and latest_job.target_category != document.category
    )
    if document.status != DocumentStatus.FAILED.value and not failed_reindex:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "not_retryable",
                "message": f"Document cannot be retried in status '{document.status}'.",
            },
        )
    if getattr(document, "pending_category", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "not_retryable",
                "message": "Document cannot be retried while a category transition is pending.",
            },
        )

    # Verify local source bytes are still available.
    try:
        storage.resolve(document_id)
    except DocumentStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "source_missing",
                "message": "Source file is no longer available; a new upload is required.",
            },
        ) from exc

    try:
        begin_retry = getattr(repo, "begin_ingestion_retry", None)
        if callable(begin_retry):
            document, job = await begin_retry(document.id)
        else:
            document.ingestion_revision = getattr(document, "ingestion_revision", 0) + 1
            document.status = DocumentStatus.QUEUED.value
            document.stage = "queued"
            document.error = None
            await repo.update_document(document)
            job = IngestionJob(document_id=document.id, revision=document.ingestion_revision, target_category=document.category)
        job = await _queue_ingestion(repo, task, document, job=job)
    except DocumentStateConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise _map_repository_error(exc) from exc
    return DocumentRetryResponse(**as_document_read(document).model_dump(), ingestion_job_id=job.id)
