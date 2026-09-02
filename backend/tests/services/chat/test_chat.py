"""Core chat service tests — filters, citations, and basic answer contract."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.chat import ChatRequest, QaMode
from app.services.chat.citations import normalize_citations
from app.services.chat.responder import INSUFFICIENT_EVIDENCE, ResponseService
from app.services.chat.retrieval import build_file_search_filter


def _response(*, answer="有來源的回答", citations=True):
    annotations = []
    if citations:
        annotations = [
            SimpleNamespace(
                type="file_citation",
                text="《政策.pdf》",
                file_citation=SimpleNamespace(file_id="file_123", filename="政策.pdf"),
                start_index=0,
                end_index=7,
            )
        ]
    return SimpleNamespace(
        output_text=answer,
        output=[SimpleNamespace(content=[SimpleNamespace(annotations=annotations)])],
    )


class AsyncFakeResponses:
    """Async-compatible fake for the Responses API."""

    def __init__(self, response):
        self.response = response
        self.calls: list = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class AsyncFakeClient:
    def __init__(self, response):
        self.responses = AsyncFakeResponses(response)


def request(mode=QaMode.LEGISLATIVE_QA, **kwargs):
    return ChatRequest(question="政策問題", mode=mode, **kwargs)


def test_mode_is_required_and_blank_question_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(question="政策問題")
    with pytest.raises(ValidationError):
        ChatRequest(question="  ", mode=QaMode.BEI_CAN)


def test_vector_store_id_on_request_is_forbidden():
    """ChatRequest must reject vector_store_id via extra='forbid'."""

    with pytest.raises(ValidationError) as exc_info:
        ChatRequest(question="q", mode=QaMode.LEGISLATIVE_QA, vector_store_id="vs_x")
    assert any(e["type"] == "extra_forbidden" for e in exc_info.value.errors())


def test_filter_always_scopes_mode_and_excludes_news():
    document_id = uuid4()
    result = build_file_search_filter(QaMode.PUBLIC_OPINION, [document_id])

    assert result["type"] == "and"
    assert {item["key"] for item in result["filters"][:2]} == {"is_news_source", "qa_set"}
    assert result["filters"][0]["value"] == "false"
    assert result["filters"][-1]["key"] == "document_id"
    assert result["filters"][-1]["value"] == str(document_id)


def test_bei_can_uses_markdown_scope():
    result = build_file_search_filter(QaMode.BEI_CAN, [uuid4()])
    assert result["type"] == "and"
    assert {item["key"] for item in result["filters"][:3]} == {
        "is_news_source",
        "qa_set",
        "content_type",
    }
    assert result["filters"][-1]["key"] == "document_id"


@pytest.mark.asyncio
async def test_async_response_uses_injected_client_and_normalizes_citations():
    """ResponseService.answer() is async and must use the injected client."""

    client = AsyncFakeClient(_response())
    document_id = uuid4()
    service = ResponseService(
        client=client,
        model="test-model",
        vector_store_id="vs_test",
        document_id_for_file=lambda _file_id: document_id,
    )
    result = await service.answer(request(), document_id_allowlist=[document_id])
    assert result.answer == "有來源的回答"
    assert result.citations[0].document_id == document_id
    tool = client.responses.calls[-1]["tools"][0]
    assert client.responses.calls[0]["model"] == "test-model"
    assert tool["type"] == "file_search"
    assert tool["filters"]["type"] == "and"
    assert tool["filters"]["filters"][-1] == {
        "type": "eq",
        "key": "document_id",
        "value": str(document_id),
    }


@pytest.mark.asyncio
async def test_missing_citations_returns_insufficient_evidence():
    service = ResponseService(
        client=AsyncFakeClient(_response(citations=False)),
        model="test-model",
        vector_store_id="vs_test",
    )
    result = await service.answer(request(), document_id_allowlist=[uuid4()])
    assert result.answer == INSUFFICIENT_EVIDENCE
    assert result.citations == []


def test_citation_normalizer_deduplicates_annotations():
    response = _response()
    response.output[0].content[0].annotations.append(response.output[0].content[0].annotations[0])
    assert len(normalize_citations(response)) == 1
