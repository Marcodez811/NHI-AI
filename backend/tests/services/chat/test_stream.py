from types import SimpleNamespace

from app.models.chat import ChatRequest, QaMode
from app.services.chat.responder import ResponseService
from app.services.chat.responder import INSUFFICIENT_EVIDENCE


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(
            [
                SimpleNamespace(type="response.output_text.delta", delta="第一段"),
                SimpleNamespace(type="response.output_text.delta", delta="第二段"),
            ]
        )

    def get_final_response(self):
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


def test_stream_emits_deltas_then_done():
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
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA, vector_store_id="vs")
    events = list(ResponseService(client=FakeClient(response), model="test").answer_stream(request))
    assert '"type": "text_delta"' in events[0]
    assert '第一段' in events[0]
    assert '"type": "done"' in events[-1]
    assert '"grounded": true' in events[-1]


def test_stream_replaces_unreferenced_output_with_insufficient_evidence():
    response = SimpleNamespace(
        output_text="沒有來源的草稿",
        output=[SimpleNamespace(content=[SimpleNamespace(annotations=[])])],
    )
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA, vector_store_id="vs")
    events = list(ResponseService(client=FakeClient(response), model="test").answer_stream(request))
    assert INSUFFICIENT_EVIDENCE in events[0]
    assert '"grounded": false' in events[-1]
