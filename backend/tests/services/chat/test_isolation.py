"""Chat isolation tests.

Covers:
- ChatRequest rejects vector_store_id via extra="forbid"
- ResponseService works without it
- Server-side scope resolver: allowlist filtering, knowledge_base_empty, category mismatch
- Citation allowlist filtering in the service layer
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.chat import ChatRequest, QaMode
from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.services.chat.responder import ChatServiceError, INSUFFICIENT_EVIDENCE, ResponseService


# ── ChatRequest contract ──────────────────────────────────────────────────────


def test_vector_store_id_is_forbidden():
    """vector_store_id must not be accepted on the public model."""

    with pytest.raises(ValidationError) as exc_info:
        ChatRequest(question="q", mode=QaMode.LEGISLATIVE_QA, vector_store_id="vs_secret")
    error_types = {e["type"] for e in exc_info.value.errors()}
    assert "extra_forbidden" in error_types


def test_unknown_field_is_forbidden():
    with pytest.raises(ValidationError) as exc_info:
        ChatRequest(question="q", mode=QaMode.LEGISLATIVE_QA, unexpected_field="x")
    error_types = {e["type"] for e in exc_info.value.errors()}
    assert "extra_forbidden" in error_types


def test_valid_request_accepted():
    req = ChatRequest(question="政策問題", mode=QaMode.LEGISLATIVE_QA)
    assert req.mode is QaMode.LEGISLATIVE_QA
    assert req.document_ids == []


# ── ResponseService — internal injection still works ─────────────────────────


def _make_fake_async_client(answer="回答", with_citations=True):
    annotations = []
    if with_citations:
        annotations = [
            SimpleNamespace(
                type="file_citation",
                text="《來源.pdf》",
                file_citation=SimpleNamespace(file_id="file_abc", filename="來源.pdf"),
                start_index=0,
                end_index=5,
            )
        ]
    response = SimpleNamespace(
        output_text=answer,
        output=[SimpleNamespace(content=[SimpleNamespace(annotations=annotations)])],
    )

    class FakeResponses:
        calls: list = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return response

    class FakeClient:
        responses = FakeResponses()

    return FakeClient(), response


@pytest.mark.asyncio
async def test_response_service_works_without_vector_store_id_on_request():
    """ResponseService can be called with an injected vector_store_id rather than a public field."""

    client, _ = _make_fake_async_client()
    document_id = uuid4()
    service = ResponseService(
        client=client,
        vector_store_id="vs_injected",
        model="test-model",
        document_id_for_file=lambda _file_id: document_id,
    )
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)

    result = await service.answer(request, document_id_allowlist=[document_id])
    assert result.answer == "回答"
    assert len(result.citations) == 1
    filter_value = client.responses.calls[-1]["tools"][0]["filters"]["filters"][-1]
    assert filter_value["key"] == "document_id"
    assert filter_value["value"] == str(document_id)


@pytest.mark.asyncio
async def test_response_service_requires_server_document_scope():
    client, _ = _make_fake_async_client()
    service = ResponseService(client=client, vector_store_id="vs_injected", model="test-model")

    with pytest.raises(ChatServiceError, match="document scope"):
        await service.answer(ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA))

    assert client.responses.calls == []


@pytest.mark.asyncio
async def test_citations_outside_allowlist_are_discarded():
    """Citations whose document_id is not in the allowlist are filtered out."""

    known_doc_id = uuid4()
    unknown_doc_id = uuid4()

    def doc_id_for_file(file_id: str) -> UUID | None:
        return known_doc_id if file_id == "file_abc" else None

    client, _ = _make_fake_async_client()
    service = ResponseService(
        client=client,
        vector_store_id="vs_injected",
        model="test-model",
        document_id_for_file=doc_id_for_file,
    )
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)

    # Allowlist contains a different ID — citation must be removed.
    result = await service.answer(request, document_id_allowlist=[unknown_doc_id])
    assert result.answer == INSUFFICIENT_EVIDENCE
    assert result.citations == []


@pytest.mark.asyncio
async def test_citations_in_allowlist_are_kept():
    known_doc_id = uuid4()

    def doc_id_for_file(file_id: str) -> UUID | None:
        return known_doc_id

    client, _ = _make_fake_async_client()
    service = ResponseService(
        client=client,
        vector_store_id="vs_injected",
        model="test-model",
        document_id_for_file=doc_id_for_file,
    )
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)

    result = await service.answer(request, document_id_allowlist=[known_doc_id])
    assert result.answer == "回答"
    assert len(result.citations) == 1
    assert result.citations[0].document_id == known_doc_id


@pytest.mark.asyncio
async def test_no_citations_without_allowlist_returns_insufficient():
    client, _ = _make_fake_async_client(with_citations=False)
    service = ResponseService(client=client, vector_store_id="vs_injected", model="test-model")
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)

    result = await service.answer(request, document_id_allowlist=[uuid4()])
    assert result.answer == INSUFFICIENT_EVIDENCE


# ── Document scope resolver helpers ──────────────────────────────────────────


def _ready_doc(category: DocumentCategory, doc_id: UUID | None = None) -> Document:
    return Document(
        id=doc_id or uuid4(),
        original_filename="brief.pdf",
        display_name="brief.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=100,
        checksum="abc",
        category=category.value,
        storage_key="k",
        status=DocumentStatus.READY.value,
        retrieval_enabled=True,
    )


class FakeDocumentRepository:
    def __init__(self, documents: list[Document]):
        self._docs = {str(d.id): d for d in documents}

    async def get_document(self, document_id: UUID) -> Document | None:
        return self._docs.get(str(document_id))

    async def list_documents(self, category=None, status=None, retrieval_enabled=None, **_) -> list[Document]:
        results = list(self._docs.values())
        if category is not None:
            results = [d for d in results if d.category == category.value]
        if status is not None:
            results = [d for d in results if d.status == status.value]
        if retrieval_enabled is not None:
            results = [d for d in results if d.retrieval_enabled == retrieval_enabled]
        return results


@pytest.mark.asyncio
async def test_category_wide_scope_excludes_pending_category_docs():
    from app.api.routes.chat import _resolve_document_scope

    eligible_id = uuid4()
    pending_id = uuid4()

    eligible = _ready_doc(DocumentCategory.LEGISLATIVE_QA, eligible_id)
    pending = _ready_doc(DocumentCategory.LEGISLATIVE_QA, pending_id)
    pending.pending_category = DocumentCategory.PUBLIC_OPINION.value

    repo = FakeDocumentRepository([eligible, pending])
    request = ChatRequest(question="q", mode=QaMode.LEGISLATIVE_QA)

    allowlist = await _resolve_document_scope(request, repo)
    assert eligible_id in allowlist
    assert pending_id not in allowlist


@pytest.mark.asyncio
async def test_knowledge_base_empty_raises_409():
    from fastapi import HTTPException
    from app.api.routes.chat import _resolve_document_scope

    # All documents are FAILED, not READY.
    doc = _ready_doc(DocumentCategory.LEGISLATIVE_QA)
    doc.status = DocumentStatus.FAILED.value
    repo = FakeDocumentRepository([doc])
    request = ChatRequest(question="q", mode=QaMode.LEGISLATIVE_QA)

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_document_scope(request, repo)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "knowledge_base_empty"


@pytest.mark.asyncio
async def test_explicit_doc_ids_wrong_category_raises_422():
    from fastapi import HTTPException
    from app.api.routes.chat import _resolve_document_scope

    doc_id = uuid4()
    doc = _ready_doc(DocumentCategory.PUBLIC_OPINION, doc_id)
    repo = FakeDocumentRepository([doc])

    # Request is LEGISLATIVE_QA but doc is PUBLIC_OPINION.
    request = ChatRequest(question="q", mode=QaMode.LEGISLATIVE_QA, document_ids=[doc_id])

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_document_scope(request, repo)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_explicit_doc_ids_not_ready_raises_409():
    from fastapi import HTTPException
    from app.api.routes.chat import _resolve_document_scope

    doc_id = uuid4()
    doc = _ready_doc(DocumentCategory.LEGISLATIVE_QA, doc_id)
    doc.status = DocumentStatus.FAILED.value
    repo = FakeDocumentRepository([doc])

    request = ChatRequest(question="q", mode=QaMode.LEGISLATIVE_QA, document_ids=[doc_id])
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_document_scope(request, repo)
    assert exc_info.value.status_code == 409
