import pytest
from fastapi import HTTPException

from app.api.routes.chat import _resolve_document_scope as _validate_document_scope
from app.models.chat import ChatRequest, QaMode
from app.api.routes.retrieval import retrieval_status
from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.services.documents.repository import InMemoryDocumentRepository


class FakeRegistry:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def status(self):
        return self.snapshot


def _document(*, category: DocumentCategory, status: DocumentStatus, retrieval_enabled: bool = True):
    return Document(
        original_filename="source.pdf",
        display_name="source.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=1,
        checksum="checksum-" + category.value + status.value + str(retrieval_enabled),
        category=category.value,
        storage_key="source",
        status=status.value,
        retrieval_enabled=retrieval_enabled,
    )


@pytest.mark.asyncio
async def test_retrieval_status_separates_ready_store_from_empty_catalog():
    repository = InMemoryDocumentRepository()
    await repository.create_document(
        _document(category=DocumentCategory.LEGISLATIVE_QA, status=DocumentStatus.QUEUED)
    )
    response = await retrieval_status(
        registry=FakeRegistry(
            {
                "state": "ready",
                "can_retrieve": True,
                "error_code": None,
                "warning_code": None,
            }
        ),
        repository=repository,
    )

    assert response.state == "ready"
    assert response.can_retrieve is True
    assert response.ready_document_count == 0


@pytest.mark.asyncio
async def test_retrieval_status_counts_only_ready_enabled_documents():
    repository = InMemoryDocumentRepository()
    await repository.create_document(
        _document(category=DocumentCategory.PUBLIC_OPINION, status=DocumentStatus.READY)
    )
    await repository.create_document(
        _document(category=DocumentCategory.PUBLIC_OPINION, status=DocumentStatus.READY, retrieval_enabled=False)
    )
    await repository.create_document(
        _document(category=DocumentCategory.PUBLIC_OPINION, status=DocumentStatus.FAILED)
    )

    response = await retrieval_status(
        registry=FakeRegistry(
            {
                "state": "provisioning",
                "can_retrieve": False,
                "error_code": None,
                "warning_code": None,
            }
        ),
        repository=repository,
    )

    assert response.state == "provisioning"
    assert response.can_retrieve is False
    assert response.ready_document_count == 1


@pytest.mark.asyncio
async def test_empty_chat_scope_returns_stable_knowledge_base_error():
    repository = InMemoryDocumentRepository()

    with pytest.raises(HTTPException) as error:
        await _validate_document_scope(
            ChatRequest(question="問題", mode=QaMode.BEI_CAN),
            repository,
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "knowledge_base_empty"
