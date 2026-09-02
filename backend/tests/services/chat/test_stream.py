from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.chat import ChatRequest, QaMode
from app.services.chat.responder import ResponseService
from app.services.chat.responder import INSUFFICIENT_EVIDENCE


class FakeStream:
    def __init__(self, response):
        self.response = response
        self.events = iter(
            [
                SimpleNamespace(type="response.output_text.delta", delta="第一段"),
                SimpleNamespace(type="response.output_text.delta", delta="第二段"),
            ]
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def get_final_response(self):
        return self.response


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.stream_calls = []

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return FakeStream(self.response)


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


@pytest.mark.asyncio
async def test_stream_emits_deltas_then_done():
    response = SimpleNamespace(
        output_text="第一段第二段",
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(
                        annotations=[
                            SimpleNamespace(
                                text="source",
                                file_citation=SimpleNamespace(file_id="f1", filename="source.pdf"),
                            )
                        ]
                    )
                ]
            )
        ],
    )
    document_id = uuid4()
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)
    client = FakeClient(response)
    service = ResponseService(
        client=client,
        model="test",
        vector_store_id="vs",
        document_id_for_file=lambda _file_id: document_id,
    )
    events = [
        event
        async for event in service.answer_stream(request, document_id_allowlist=[document_id])
    ]
    assert '"type": "text_delta"' in events[0]
    assert '第一段' in events[0]
    assert '"type": "done"' in events[-1]
    assert '"grounded": true' in events[-1]
    payload = client.responses.stream_calls[0]
    assert payload["tools"][0]["filters"]["filters"][-1]["value"] == str(document_id)


@pytest.mark.asyncio
async def test_stream_replaces_unreferenced_output_with_insufficient_evidence():
    response = SimpleNamespace(
        output_text="沒有來源的草稿",
        output=[SimpleNamespace(content=[SimpleNamespace(annotations=[])])],
    )
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)
    service = ResponseService(client=FakeClient(response), model="test", vector_store_id="vs")
    events = [
        event
        async for event in service.answer_stream(request, document_id_allowlist=[uuid4()])
    ]
    assert INSUFFICIENT_EVIDENCE in events[0]
    assert '"grounded": false' in events[-1]
