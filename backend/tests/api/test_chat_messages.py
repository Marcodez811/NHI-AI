from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from app.api.routes.chat import (
    create_chat_session,
    delete_chat_attachment,
    get_chat_session,
    get_chat_attachment_content,
    send_chat_message,
    upload_chat_attachment,
)
from app.config import settings
from app.models.chat import ChatMessage, ChatMessageCreate, ChatMessageRole, ChatSessionCreate
from app.services.chat import models as chat_models
from app.services.chat.attachments import ChatAttachmentStorage
from app.services.chat.engine import ChatEvent
from app.services.chat.repository import InMemoryChatRepository


class FakeUpload:
    def __init__(self, name: str, data: bytes, content_type: str = "text/plain"):
        self.filename = name
        self.content_type = content_type
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data, self._data = self._data, b""
            return data
        data, self._data = self._data[:size], self._data[size:]
        return data


class FakeChatEngine:
    """A fake ``ChatEngine`` that replays a scripted event list and records its inputs."""

    def __init__(self, events):
        self._events = events
        self.calls: list[dict] = []
        self.summaries: list[dict] = []
        self.summary_result = "先前討論的健保規定與附件內容。"
        self.summary_error: Exception | None = None

    async def summarize(self, *, history, previous_summary, attachments, model):
        self.summaries.append({
            "history": list(history),
            "previous_summary": previous_summary,
            "attachments": list(attachments),
            "model": model,
        })
        if self.summary_error:
            raise self.summary_error
        return self.summary_result

    async def run_turn(self, *, history, user_message, attachments, model, summary=None):
        self.calls.append(
            {
                "history": list(history),
                "user_message": user_message,
                "attachments": list(attachments),
                "model": model,
                "summary": summary,
            }
        )
        for event in self._events:
            yield event


async def _collect_sse(response) -> list[str]:
    return [chunk async for chunk in response.body_iterator]


def _events_of(chunks: list[str]) -> list[dict]:
    return [json.loads(chunk[len("data: "):]) for chunk in chunks if chunk.startswith("data: ")]


# ── Attachments: upload / content / delete ──────────────────────────────────


@pytest.mark.asyncio
async def test_upload_attachment_returns_a_ready_record(tmp_path):
    repository = InMemoryChatRepository()
    storage = ChatAttachmentStorage(tmp_path)
    session = await create_chat_session(ChatSessionCreate(), repository)

    attachment = await upload_chat_attachment(
        session.id, FakeUpload("note.txt", "健保給付說明".encode("utf-8")), repository, storage
    )

    assert attachment.status.value == "ready"
    assert attachment.display_name == "note.txt"


@pytest.mark.asyncio
async def test_upload_attachment_rejects_an_unsupported_type(tmp_path):
    repository = InMemoryChatRepository()
    storage = ChatAttachmentStorage(tmp_path)
    session = await create_chat_session(ChatSessionCreate(), repository)

    with pytest.raises(HTTPException) as error:
        await upload_chat_attachment(session.id, FakeUpload("virus.exe", b"x", "application/octet-stream"), repository, storage)
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_attachment_404s_for_an_unknown_session(tmp_path):
    from uuid import uuid4

    with pytest.raises(HTTPException) as error:
        await upload_chat_attachment(
            uuid4(), FakeUpload("note.txt", b"hi"), InMemoryChatRepository(), ChatAttachmentStorage(tmp_path)
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_get_attachment_content_returns_the_stored_file(tmp_path):
    repository = InMemoryChatRepository()
    storage = ChatAttachmentStorage(tmp_path)
    session = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(session.id, FakeUpload("note.txt", b"hello"), repository, storage)

    response = await get_chat_attachment_content(session.id, attachment.id, repository, storage)

    assert response.filename == "note.txt"
    assert response.media_type == "text/plain"


@pytest.mark.asyncio
async def test_delete_unsent_attachment_succeeds(tmp_path):
    repository = InMemoryChatRepository()
    storage = ChatAttachmentStorage(tmp_path)
    session = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(session.id, FakeUpload("note.txt", b"hi"), repository, storage)

    content_path = storage.resolve((await repository.get_file(attachment.id)).storage_key)
    await delete_chat_attachment(session.id, attachment.id, repository)

    # Removing it from the conversation only unlinks; the file stays in 「我的檔案」.
    assert await repository.list_session_files(session.id) == []
    assert await repository.get_file(attachment.id) is not None
    assert content_path.exists()


@pytest.mark.asyncio
async def test_delete_attachment_already_sent_in_a_message_is_rejected(tmp_path):
    repository = InMemoryChatRepository()
    storage = ChatAttachmentStorage(tmp_path)
    session = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(session.id, FakeUpload("note.txt", b"hi"), repository, storage)
    engine = FakeChatEngine([ChatEvent("sources", {"sources": []}), ChatEvent("usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})])
    response = await send_chat_message(
        session.id,
        ChatMessageCreate(content="這是什麼", attachment_ids=[attachment.id], model="gpt-6-astra"),
        repository,
        engine,
    )
    await _collect_sse(response)

    with pytest.raises(HTTPException) as error:
        await delete_chat_attachment(session.id, attachment.id, repository)
    assert error.value.status_code == 409


# ── Messages: SSE order, persistence, statuses ──────────────────────────────


@pytest.mark.asyncio
async def test_message_unavailable_model_422_and_nothing_is_persisted():
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    engine = FakeChatEngine([])

    with pytest.raises(HTTPException) as error:
        await send_chat_message(
            session.id,
            ChatMessageCreate(content="哈囉", model="not-a-real-model"),
            repository,
            engine,
        )

    assert error.value.status_code == 422
    assert await repository.list_messages(session.id) == []
    assert engine.calls == []


@pytest.mark.asyncio
async def test_message_with_an_unready_attachment_422s():
    from uuid import uuid4

    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    engine = FakeChatEngine([])

    with pytest.raises(HTTPException) as error:
        await send_chat_message(
            session.id,
            ChatMessageCreate(content="哈囉", attachment_ids=[uuid4()], model="gpt-6-astra"),
            repository,
            engine,
        )

    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_message_stream_order_and_persistence():
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    engine = FakeChatEngine(
        [
            ChatEvent("text_delta", {"text": "健保"}),
            ChatEvent("tool_started", {"tool": "search_knowledge_base", "label": "搜尋知識庫：健保"}),
            ChatEvent("tool_finished", {"tool": "search_knowledge_base", "label": "找到 1 筆資料"}),
            ChatEvent("text_delta", {"text": "給付規定如下"}),
            ChatEvent("sources", {"sources": [{"name": "白皮書", "snippet": "..."}]}),
            ChatEvent("usage", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}),
        ]
    )

    response = await send_chat_message(
        session.id, ChatMessageCreate(content="健保給付規定？", model="gpt-6-astra"), repository, engine
    )
    chunks = await _collect_sse(response)
    events = _events_of(chunks)

    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "tool_started",
        "tool_finished",
        "text_delta",
        "sources",
        "done",
    ]
    assert events[-1]["title"] == "健保給付規定？"

    messages = await repository.list_messages(session.id)
    assert [m.role for m in messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_assistant_message_content_and_sources_are_persisted():
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    engine = FakeChatEngine(
        [
            ChatEvent("text_delta", {"text": "健保"}),
            ChatEvent("text_delta", {"text": "給付規定如下"}),
            ChatEvent("sources", {"sources": [{"name": "白皮書", "snippet": "..."}]}),
            ChatEvent("usage", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}),
        ]
    )

    response = await send_chat_message(
        session.id, ChatMessageCreate(content="健保給付規定？", model="gpt-6-astra"), repository, engine
    )
    await _collect_sse(response)

    messages = await repository.list_messages(session.id)
    assistant = messages[1]
    assert assistant.content == "健保給付規定如下"
    assert assistant.sources == [{"name": "白皮書", "snippet": "..."}]
    assert assistant.status == "complete"
    assert assistant.model == "gpt-6-astra"
    assert assistant.usage == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}

    session_detail = await repository.get_session(session.id)
    assert session_detail.title == "健保給付規定？"


@pytest.mark.asyncio
async def test_error_event_marks_the_message_error_and_skips_done():
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    engine = FakeChatEngine([ChatEvent("error", {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"})])

    response = await send_chat_message(
        session.id, ChatMessageCreate(content="哈囉", model="gpt-6-astra"), repository, engine
    )
    chunks = await _collect_sse(response)
    events = _events_of(chunks)

    assert events[-1]["type"] == "error"
    assert not any(event["type"] == "done" for event in events)
    messages = await repository.list_messages(session.id)
    assert messages[1].status == "error"


@pytest.mark.asyncio
async def test_history_is_rebuilt_in_order_across_turns():
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    engine = FakeChatEngine([ChatEvent("text_delta", {"text": "回覆"}), ChatEvent("sources", {"sources": []}), ChatEvent("usage", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})])

    first_response = await send_chat_message(
        session.id, ChatMessageCreate(content="第一個問題", model="gpt-6-astra"), repository, engine
    )
    await _collect_sse(first_response)
    second_response = await send_chat_message(
        session.id, ChatMessageCreate(content="第二個問題", model="gpt-6-astra"), repository, engine
    )
    await _collect_sse(second_response)

    assert len(engine.calls) == 2
    assert engine.calls[0]["history"] == []
    assert engine.calls[0]["user_message"].content == "第一個問題"
    second_call_history = engine.calls[1]["history"]
    assert [m.content for m in second_call_history] == ["第一個問題", "回覆"]
    assert engine.calls[1]["user_message"].content == "第二個問題"


@pytest.mark.asyncio
async def test_client_disconnect_persists_an_interrupted_message():
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)

    async def slow_events():
        yield ChatEvent("text_delta", {"text": "先前的內容"})
        await asyncio.sleep(10)
        yield ChatEvent("text_delta", {"text": "不會送達"})  # pragma: no cover

    class SlowEngine:
        async def run_turn(self, *, history, user_message, attachments, model, summary=None):
            async for event in slow_events():
                yield event

    response = await send_chat_message(
        session.id, ChatMessageCreate(content="問題", model="gpt-6-astra"), repository, SlowEngine()
    )
    iterator = response.body_iterator.__aiter__()
    first = await iterator.__anext__()
    assert json.loads(first[len("data: "):])["type"] == "message_start"
    second = await iterator.__anext__()
    assert json.loads(second[len("data: "):])["text"] == "先前的內容"

    await response.body_iterator.aclose()
    await asyncio.sleep(0)  # let the generator's cleanup finish

    messages = await repository.list_messages(session.id)
    assistant = [m for m in messages if m.role == "assistant"][0]
    assert assistant.status == "interrupted"
    assert assistant.content == "先前的內容"


async def _seed_history(repository, session_id, *, input_tokens=100, pairs=5):
    for index in range(pairs):
        await repository.create_message(ChatMessage(
            session_id=session_id,
            role=ChatMessageRole.USER.value,
            content=f"問題 {index}",
        ))
        await repository.create_message(ChatMessage(
            session_id=session_id,
            role=ChatMessageRole.ASSISTANT.value,
            content=f"答覆 {index}",
            usage={"input_tokens": input_tokens, "output_tokens": 1},
        ))


@pytest.mark.asyncio
async def test_compaction_folds_only_newly_old_history_and_exposes_marker(monkeypatch):
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    await _seed_history(repository, session.id)
    monkeypatch.setattr(settings, "chat_compaction_threshold", 0.6)
    monkeypatch.setattr(chat_models, "find_available_model", lambda _: type("Model", (), {"context_window": 100})())
    engine = FakeChatEngine([ChatEvent("text_delta", {"text": "回覆"}), ChatEvent("usage", {"input_tokens": 100})])

    response = await send_chat_message(
        session.id, ChatMessageCreate(content="新的問題", model="gpt-6-astra"), repository, engine,
    )
    events = _events_of(await _collect_sse(response))
    assert [event["type"] for event in events] == [
        "message_start", "compacting", "compacted", "text_delta", "done",
    ]
    assert events[2]["ok"] is True
    assert [message.content for message in engine.summaries[0]["history"]] == [
        "問題 0", "答覆 0", "問題 1", "答覆 1",
    ]
    assert [message.content for message in engine.calls[0]["history"]] == [
        "問題 2", "答覆 2", "問題 3", "答覆 3", "問題 4", "答覆 4",
    ]
    assert engine.calls[0]["summary"] == engine.summary_result
    detail = await get_chat_session(session.id, repository)
    assert detail.compacted_through_message_id == engine.summaries[0]["history"][-1].id
    assert "summary" not in detail.model_dump()

    # Later growth folds only messages after the previous marker into the running summary.
    await _seed_history(repository, session.id, pairs=2)
    engine.summary_result = "更新後的摘要。"
    all_messages = await repository.list_messages(session.id)
    expected_newly_old = all_messages[4:-6]
    response = await send_chat_message(
        session.id, ChatMessageCreate(content="後續問題", model="gpt-6-astra"), repository, engine,
    )
    await _collect_sse(response)
    assert engine.summaries[1]["previous_summary"] == "先前討論的健保規定與附件內容。"
    assert [message.id for message in engine.summaries[1]["history"]] == [message.id for message in expected_newly_old]
    assert [message.id for message in engine.calls[1]["history"]] == [message.id for message in all_messages[-6:]]
    assert engine.calls[1]["summary"] == "更新後的摘要。"


@pytest.mark.asyncio
async def test_compaction_below_threshold_does_not_call_summary(monkeypatch):
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    await _seed_history(repository, session.id, input_tokens=60)
    monkeypatch.setattr(settings, "chat_compaction_threshold", 0.6)
    monkeypatch.setattr(chat_models, "find_available_model", lambda _: type("Model", (), {"context_window": 100})())
    engine = FakeChatEngine([])
    response = await send_chat_message(
        session.id, ChatMessageCreate(content="後續問題", model="gpt-6-astra"), repository, engine,
    )
    assert [event["type"] for event in _events_of(await _collect_sse(response))] == ["message_start", "done"]
    assert engine.summaries == []
    assert len(engine.calls[0]["history"]) == 10


@pytest.mark.asyncio
async def test_failed_compaction_replays_full_history_and_completes(monkeypatch):
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    await _seed_history(repository, session.id)
    prior_marker = (await repository.list_messages(session.id))[0].id
    stored_session = await repository.get_session(session.id)
    stored_session.summary = "先前摘要。"
    stored_session.summary_through_message_id = prior_marker
    await repository.update_session(stored_session)
    monkeypatch.setattr(settings, "chat_compaction_threshold", 0.6)
    monkeypatch.setattr(chat_models, "find_available_model", lambda _: type("Model", (), {"context_window": 100})())
    engine = FakeChatEngine([ChatEvent("text_delta", {"text": "完成"})])
    engine.summary_error = RuntimeError("model failed")
    response = await send_chat_message(
        session.id, ChatMessageCreate(content="後續問題", model="gpt-6-astra"), repository, engine,
    )
    events = _events_of(await _collect_sse(response))
    assert [item["type"] for item in events] == ["message_start", "compacting", "compacted", "text_delta", "done"]
    assert events[2]["ok"] is False
    assert len(engine.calls[0]["history"]) == 10
    assert engine.calls[0]["summary"] is None
    assert engine.summaries[0]["previous_summary"] == "先前摘要。"
    assert (await repository.get_session(session.id)).summary_through_message_id == prior_marker
    assert (await repository.get_session(session.id)).summary == "先前摘要。"


@pytest.mark.asyncio
async def test_failed_summary_persistence_does_not_save_a_new_marker(monkeypatch):
    class FailSummaryOnceRepository(InMemoryChatRepository):
        def __init__(self):
            super().__init__()
            self.fail_summary_once = False

        async def update_session(self, session):
            if self.fail_summary_once:
                self.fail_summary_once = False
                raise RuntimeError("summary persistence failed")
            return await super().update_session(session)

    repository = FailSummaryOnceRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    await _seed_history(repository, session.id)
    initial_history = await repository.list_messages(session.id)
    stored_session = await repository.get_session(session.id)
    stored_session.summary = "舊摘要。"
    stored_session.summary_through_message_id = initial_history[0].id
    await repository.update_session(stored_session)
    repository.fail_summary_once = True
    monkeypatch.setattr(settings, "chat_compaction_threshold", 0.6)
    monkeypatch.setattr(chat_models, "find_available_model", lambda _: type("Model", (), {"context_window": 100})())
    engine = FakeChatEngine([ChatEvent("text_delta", {"text": "繼續回答"})])

    response = await send_chat_message(
        session.id, ChatMessageCreate(content="新問題", model="gpt-6-astra"), repository, engine,
    )
    events = _events_of(await _collect_sse(response))
    assert [event["type"] for event in events] == ["message_start", "compacting", "compacted", "text_delta", "done"]
    assert events[2]["ok"] is False
    assert engine.calls[0]["summary"] is None
    assert [message.id for message in engine.calls[0]["history"]] == [message.id for message in initial_history]
    stored = await repository.get_session(session.id)
    assert stored.summary == "舊摘要。"
    assert stored.summary_through_message_id == initial_history[0].id
    assert (await repository.list_messages(session.id))[-1].status == "complete"


@pytest.mark.asyncio
async def test_reasoning_is_forwarded_and_persisted_with_cap():
    repository = InMemoryChatRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    engine = FakeChatEngine([
        ChatEvent("reasoning_delta", {"text": "思考" * 11_000}),
        ChatEvent("text_delta", {"text": "答案"}),
    ])
    response = await send_chat_message(
        session.id, ChatMessageCreate(content="問題", model="gpt-6-astra"), repository, engine,
    )
    events = _events_of(await _collect_sse(response))
    assert [event["type"] for event in events] == ["message_start", "reasoning_delta", "text_delta", "done"]
    detail = await get_chat_session(session.id, repository)
    assert detail.messages[-1].reasoning == ("思考" * 10_000)
