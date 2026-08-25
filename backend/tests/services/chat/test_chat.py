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


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


def request(mode=QaMode.LEGISLATIVE_QA, **kwargs):
    return ChatRequest(question="政策問題", mode=mode, vector_store_id="vs_test", **kwargs)


def test_mode_is_required_and_blank_question_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(question="政策問題")
    with pytest.raises(ValidationError):
        ChatRequest(question="  ", mode=QaMode.BEI_CAN)


def test_filter_always_scopes_mode_and_excludes_news():
    document_id = uuid4()
    result = build_file_search_filter(QaMode.PUBLIC_OPINION, [document_id])

    assert result["type"] == "and"
    assert {item["key"] for item in result["filters"][:2]} == {"is_news_source", "qa_set"}
    assert result["filters"][0]["value"] == "false"
    assert result["filters"][-1]["key"] == "document_id"
    assert result["filters"][-1]["value"] == str(document_id)


def test_bei_can_uses_markdown_scope():
    result = build_file_search_filter(QaMode.BEI_CAN)
    assert result["type"] == "and"
    assert {item["key"] for item in result["filters"]} == {"is_news_source", "content_type"}


def test_sync_response_uses_injected_client_and_normalizes_citations():
    client = FakeClient(_response())
    service = ResponseService(client=client, model="test-model")
    result = service.answer(request())

    assert result.answer == "有來源的回答"
    assert result.citations[0].filename == "政策.pdf"
    assert client.responses.calls[0]["model"] == "test-model"
    tool = client.responses.calls[0]["tools"][0]
    assert tool["type"] == "file_search"
    assert tool["filters"]["type"] == "and"


def test_missing_citations_returns_insufficient_evidence():
    service = ResponseService(client=FakeClient(_response(citations=False)), model="test-model")
    result = service.answer(request())
    assert result.answer == INSUFFICIENT_EVIDENCE
    assert result.citations == []


def test_citation_normalizer_deduplicates_annotations():
    response = _response()
    response.output[0].content[0].annotations.append(response.output[0].content[0].annotations[0])
    assert len(normalize_citations(response)) == 1
