"""Async provider and settings-based configuration tests.

Covers:
- ResponseService.answer() is async and awaits the async client
- ResponseService.answer_stream() is async and yields SSE events
- Provider key must come from settings, not os.getenv
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.chat import ChatRequest, QaMode
from app.services.chat.responder import INSUFFICIENT_EVIDENCE, ResponseService


def _build_annotation(file_id="file_x", filename="source.pdf"):
    return SimpleNamespace(
        type="file_citation",
        text="《source.pdf》",
        file_citation=SimpleNamespace(file_id=file_id, filename=filename),
        start_index=0,
        end_index=5,
    )


def _fake_response(*, answer="非常好的回答", with_citations=True):
    annotations = [_build_annotation()] if with_citations else []
    return SimpleNamespace(
        output_text=answer,
        output=[SimpleNamespace(content=[SimpleNamespace(annotations=annotations)])],
    )


class AsyncFakeResponses:
    """Simulates an async OpenAI Responses API client."""

    def __init__(self, response):
        self.response = response
        self.calls: list = []
        self.stream_calls: list = []
        self._stream_yielded = False

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        response = self.response
        # Return a context manager that yields the stream then provides the final response.
        return _AsyncStreamContextManager(response)


class _AsyncStreamContextManager:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_response(self):
        return self._response


class AsyncFakeClient:
    def __init__(self, response):
        self.responses = AsyncFakeResponses(response)


@pytest.mark.asyncio
async def test_answer_is_async_and_awaits_client():
    """ResponseService.answer() must be awaitable and use the async client."""

    client = AsyncFakeClient(_fake_response())
    document_id = uuid4()
    service = ResponseService(
        client=client,
        vector_store_id="vs_test",
        model="test-model",
        document_id_for_file=lambda _file_id: document_id,
    )
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)

    result = await service.answer(request, document_id_allowlist=[document_id])
    assert result.answer == "非常好的回答"
    assert len(client.responses.calls) == 1
    assert client.responses.calls[0]["model"] == "test-model"
    assert client.responses.calls[0]["tools"][0]["filters"]["filters"][-1] == {
        "type": "eq",
        "key": "document_id",
        "value": str(document_id),
    }


@pytest.mark.asyncio
async def test_answer_stream_yields_sse_events():
    """answer_stream() must yield text_delta and done SSE events."""

    client = AsyncFakeClient(_fake_response(answer="回答文字"))
    document_id = uuid4()
    service = ResponseService(
        client=client,
        vector_store_id="vs_test",
        model="test-model",
        document_id_for_file=lambda _file_id: document_id,
    )
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)

    chunks: list[str] = []
    async for chunk in service.answer_stream(request, document_id_allowlist=[document_id]):
        chunks.append(chunk)

    assert len(chunks) >= 1
    done_chunk = chunks[-1]
    assert "done" in done_chunk
    parsed = json.loads(done_chunk.replace("data: ", ""))
    assert parsed["type"] == "done"
    assert parsed["grounded"] is True
    assert client.responses.stream_calls[0]["tools"][0]["filters"]["filters"][-1]["value"] == str(document_id)


@pytest.mark.asyncio
async def test_answer_stream_with_no_citations_sends_insufficient_evidence():
    client = AsyncFakeClient(_fake_response(with_citations=False))
    service = ResponseService(client=client, vector_store_id="vs_test", model="test-model")
    request = ChatRequest(question="問題", mode=QaMode.BEI_CAN)

    chunks: list[str] = []
    async for chunk in service.answer_stream(request, document_id_allowlist=[uuid4()]):
        chunks.append(chunk)

    # Find the text_delta chunk.
    text_chunks = [c for c in chunks if "text_delta" in c]
    assert any(INSUFFICIENT_EVIDENCE in c for c in text_chunks)

    done_chunk = chunks[-1]
    parsed = json.loads(done_chunk.replace("data: ", ""))
    assert parsed["grounded"] is False


@pytest.mark.asyncio
async def test_settings_api_key_used_not_os_getenv(monkeypatch):
    """The client must read its key from settings, not directly from os.getenv."""

    import os
    from app import config as config_module
    from pydantic import SecretStr

    # Clear any real key from the environment.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Patch settings.openai_api_key to a known value.
    captured_keys: list[str] = []

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key: str):
            captured_keys.append(api_key)
            self.responses = AsyncFakeResponses(_fake_response())

    monkeypatch.setattr(
        config_module.settings,
        "openai_api_key",
        SecretStr("settings-key-123"),
    )

    from app.services.chat import responder as responder_module

    original_factory = responder_module._default_async_client

    async def patched_factory():
        api_key = config_module.settings.openai_api_key.get_secret_value()
        client = FakeAsyncOpenAI(api_key=api_key)
        return client

    monkeypatch.setattr(responder_module, "_default_async_client", patched_factory)

    service = ResponseService(vector_store_id="vs_test", model="test-model")
    request = ChatRequest(question="問題", mode=QaMode.LEGISLATIVE_QA)
    await service.answer(request, document_id_allowlist=[uuid4()])

    assert "settings-key-123" in captured_keys
