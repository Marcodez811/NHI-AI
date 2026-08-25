from uuid import uuid4

import pytest

from app.models.documents import Document, DocumentCategory, DocumentStatus, Folder
from app.services.documents.repository import (
    DuplicateDocumentError,
    FolderNotEmptyError,
    InMemoryDocumentRepository,
)


def make_document(category=DocumentCategory.LEGISLATIVE_QA):
    return Document(
        original_filename="brief.pdf",
        display_name="brief.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=1,
        checksum="same",
        category=category.value,
        storage_key="ignored",
    )


@pytest.mark.asyncio
async def test_repository_rejects_duplicate_content_per_category():
    repo = InMemoryDocumentRepository()
    await repo.create_document(make_document())
    with pytest.raises(DuplicateDocumentError):
        await repo.create_document(make_document())
    public = make_document(DocumentCategory.PUBLIC_OPINION)
    await repo.create_document(public)
    assert len(await repo.list_documents(category=DocumentCategory.PUBLIC_OPINION)) == 1


@pytest.mark.asyncio
async def test_nonempty_folder_cannot_be_deleted():
    repo = InMemoryDocumentRepository()
    folder = await repo.create_folder(Folder(name="Q&A"))
    document = make_document()
    document.folder_id = folder.id
    await repo.create_document(document)
    with pytest.raises(FolderNotEmptyError):
        await repo.delete_folder(folder.id)

