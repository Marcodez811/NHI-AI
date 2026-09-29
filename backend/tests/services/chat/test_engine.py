from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import Usage
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from agents.tool_context import ToolContext

from app.models.chat import (
    ChatAttachment,
    ChatAttachmentKind,
    ChatAttachmentStatus,
    ChatMessage,
    ChatMessageRole,
)
from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.services.chat.attachments import ChatAttachmentStorage
from app.services.chat.engine import (
    READ_ATTACHMENT_TOOL_NAME,
    SEARCH_TOOL_NAME,
    AgentsSdkChatEngine,
    _message_to_input_item,
)
from app.services.documents.repository import InMemoryDocumentRepository


def _document(*, display_name: str, status=DocumentStatus.READY, retrieval_enabled=True) -> Document:
    return Document(
        original_filename=f"{display_name}.pdf",
        display_name=display_name,
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=1,
        checksum=str(uuid4()),
        category=DocumentCategory.LEGISLATIVE_QA.value,
        storage_key="ignored",
        status=status.value,
        retrieval_enabled=retrieval_enabled,
    )


class FakeContentPart:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeSearchResult:
    def __init__(self, *, document_id, text: str, extra_attributes=None) -> None:
        self.attributes = {"document_id": str(document_id), **(extra_attributes or {})}
        self.content = [FakeContentPart(text)]


class FakePage:
    def __init__(self, data) -> None:
        self.data = data


class FakeVectorStores:
    def __init__(self, results) -> None:
        self._results = results
        self.last_call: dict | None = None

    async def search(self, vector_store_id, *, query, filters, max_num_results):
        self.last_call = {
            "vector_store_id": vector_store_id,
            "query": query,
            "filters": filters,
            "max_num_results": max_num_results,
        }
        return FakePage(self._results)


class FakeOpenAIClient:
    def __init__(self, results) -> None:
        self.vector_stores = FakeVectorStores(results)


def _tool_context(name: str, arguments: dict) -> ToolContext:
    return ToolContext(
        context=None,
        tool_name=name,
        tool_call_id=f"call-{name}",
        tool_arguments=json.dumps(arguments),
    )


def _engine(*, document_repository, storage, results=(), vector_store_id="vs_1") -> AgentsSdkChatEngine:
    return AgentsSdkChatEngine(
        document_repository=document_repository,
        vector_store_id_provider=lambda: vector_store_id,
        attachment_storage=storage,
        client=FakeOpenAIClient(list(results)),
    )


# ── search_knowledge_base: post-filtering and no leaked ids ────────────────


@pytest.mark.asyncio
async def test_search_tool_keeps_only_ready_and_enabled_documents_and_never_leaks_ids(tmp_path):
    repository = InMemoryDocumentRepository()
    ready = await repository.create_document(_document(display_name="健保白皮書"))
    disabled = await repository.create_document(_document(display_name="停用文件", retrieval_enabled=False))
    not_ready = await repository.create_document(_document(display_name="處理中文件", status=DocumentStatus.INDEXING))
    unknown_id = uuid4()

    results = [
        FakeSearchResult(document_id=ready.id, text="健保給付規定第一條"),
        FakeSearchResult(document_id=disabled.id, text="不應出現"),
        FakeSearchResult(document_id=not_ready.id, text="不應出現"),
        FakeSearchResult(document_id=unknown_id, text="不應出現"),
    ]
    engine = _engine(document_repository=repository, storage=ChatAttachmentStorage(tmp_path), results=results)
    sources: list[dict[str, str]] = []
    labels: dict[str, str] = {}
    tool = engine._search_tool(sources, labels)

    ctx = _tool_context(SEARCH_TOOL_NAME, {"query": "健保給付"})
    output = await tool.on_invoke_tool(ctx, json.dumps({"query": "健保給付"}))

    assert "健保白皮書" in output
    assert "不應出現" not in output
    assert str(ready.id) not in output
    assert str(disabled.id) not in output
    assert sources == [{"name": "健保白皮書", "snippet": "健保給付規定第一條"}]
    assert labels[ctx.tool_call_id] == "找到 1 筆資料"


@pytest.mark.asyncio
async def test_search_tool_keeps_several_passages_from_one_document(tmp_path):
    repository = InMemoryDocumentRepository()
    document = await repository.create_document(_document(display_name="健保白皮書"))
    results = [
        FakeSearchResult(document_id=document.id, text="第一段：給付範圍"),
        FakeSearchResult(document_id=document.id, text="第二段：申報流程"),
        FakeSearchResult(document_id=document.id, text="第一段：給付範圍"),
    ]
    engine = _engine(document_repository=repository, storage=ChatAttachmentStorage(tmp_path), results=results)
    labels: dict[str, str] = {}
    tool = engine._search_tool([], labels)

    ctx = _tool_context(SEARCH_TOOL_NAME, {"query": "給付"})
    output = await tool.on_invoke_tool(ctx, json.dumps({"query": "給付"}))

    assert "給付範圍" in output and "申報流程" in output
    assert labels[ctx.tool_call_id] == "找到 2 筆資料"


@pytest.mark.asyncio
async def test_search_tool_sends_the_is_news_source_and_category_filters(tmp_path):
    repository = InMemoryDocumentRepository()
    engine = _engine(document_repository=repository, storage=ChatAttachmentStorage(tmp_path), results=[])
    tool = engine._search_tool([], {})
    ctx = _tool_context(SEARCH_TOOL_NAME, {"query": "q", "category": "bei_can"})

    await tool.on_invoke_tool(ctx, json.dumps({"query": "q", "category": "bei_can"}))

    call = engine._client.vector_stores.last_call
    assert call["filters"] == {
        "type": "and",
        "filters": [
            {"type": "eq", "key": "is_news_source", "value": "false"},
            {"type": "eq", "key": "qa_set", "value": "bei_can"},
        ],
    }


@pytest.mark.asyncio
async def test_search_tool_reports_when_the_knowledge_base_is_unavailable(tmp_path):
    repository = InMemoryDocumentRepository()
    engine = AgentsSdkChatEngine(
        document_repository=repository,
        vector_store_id_provider=lambda: None,
        attachment_storage=ChatAttachmentStorage(tmp_path),
        client=FakeOpenAIClient([]),
    )
    labels: dict[str, str] = {}
    tool = engine._search_tool([], labels)
    ctx = _tool_context(SEARCH_TOOL_NAME, {"query": "q"})

    output = await tool.on_invoke_tool(ctx, json.dumps({"query": "q"}))

    assert "無法使用" in output
    assert labels[ctx.tool_call_id] == "知識庫目前無法使用"


# ── read_attachment: scoped to the attachments handed to the engine ────────


@pytest.mark.asyncio
async def test_read_attachment_pages_through_saved_text(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    session_id, attachment_id = uuid4(), uuid4()
    path, storage_key = await storage.save(session_id, attachment_id, ".txt", b"placeholder")
    await storage.save_text(path, "0123456789")
    attachment = ChatAttachment(
        id=attachment_id,
        session_id=session_id,
        display_name="報告.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        kind=ChatAttachmentKind.DOCUMENT.value,
        size_bytes=11,
        storage_key=storage_key,
        status=ChatAttachmentStatus.READY.value,
        text_chars=10,
    )
    engine = _engine(document_repository=InMemoryDocumentRepository(), storage=storage)
    tool = engine._read_attachment_tool({attachment.id: attachment}, {})
    ctx = _tool_context(READ_ATTACHMENT_TOOL_NAME, {"name": "報告.docx", "start": 2, "max_chars": 4})

    output = await tool.on_invoke_tool(ctx, json.dumps({"name": "報告.docx", "start": 2, "max_chars": 4}))

    assert "2345" in output
    assert "共 10 字元" in output


@pytest.mark.asyncio
async def test_read_attachment_cannot_see_attachments_outside_the_session(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    engine = _engine(document_repository=InMemoryDocumentRepository(), storage=storage)
    # No attachments handed to the engine for this session -- a name from
    # another session's attachment must not be readable.
    tool = engine._read_attachment_tool({}, {})
    ctx = _tool_context(READ_ATTACHMENT_TOOL_NAME, {"name": "其他對話的檔案.pdf"})

    output = await tool.on_invoke_tool(ctx, json.dumps({"name": "其他對話的檔案.pdf"}))

    assert "找不到" in output


# ── History rebuild: only final text replayed, images re-attached ──────────


@pytest.mark.asyncio
async def test_message_to_input_item_replays_text_only_for_plain_turns():
    message = ChatMessage(session_id=uuid4(), role=ChatMessageRole.ASSISTANT.value, content="這是先前的回答")
    item = await _message_to_input_item(message, {}, ChatAttachmentStorage.__new__(ChatAttachmentStorage))

    assert item == {"role": "assistant", "content": "這是先前的回答"}


@pytest.mark.asyncio
async def test_message_to_input_item_reattaches_image_parts(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    session_id, attachment_id = uuid4(), uuid4()
    path, storage_key = await storage.save(session_id, attachment_id, ".png", b"fake-png-bytes")
    attachment = ChatAttachment(
        id=attachment_id,
        session_id=session_id,
        display_name="photo.png",
        mime_type="image/png",
        kind=ChatAttachmentKind.IMAGE.value,
        size_bytes=14,
        storage_key=storage_key,
        status=ChatAttachmentStatus.READY.value,
    )
    message = ChatMessage(
        session_id=session_id,
        role=ChatMessageRole.USER.value,
        content="這張圖是什麼？",
        attachment_ids=[str(attachment_id)],
    )

    item = await _message_to_input_item(message, {attachment.id: attachment}, storage)

    assert item["role"] == "user"
    assert item["content"][0] == {"type": "input_text", "text": "這張圖是什麼？"}
    image_part = item["content"][1]
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_message_to_input_item_attaches_pdfs_as_files(tmp_path):
    # The provider reads the PDF itself, scanned pages included.
    storage = ChatAttachmentStorage(tmp_path)
    session_id, attachment_id = uuid4(), uuid4()
    _, storage_key = await storage.save(session_id, attachment_id, ".pdf", b"%PDF-1.4 fake")
    attachment = ChatAttachment(
        id=attachment_id,
        session_id=session_id,
        display_name="掃描公文.pdf",
        mime_type="application/pdf",
        kind=ChatAttachmentKind.DOCUMENT.value,
        size_bytes=13,
        storage_key=storage_key,
        status=ChatAttachmentStatus.READY.value,
    )
    message = ChatMessage(
        session_id=session_id,
        role=ChatMessageRole.USER.value,
        content="請摘要這份公文",
        attachment_ids=[str(attachment_id)],
    )

    item = await _message_to_input_item(message, {attachment.id: attachment}, storage)

    file_part = item["content"][1]
    assert file_part["type"] == "input_file"
    assert file_part["filename"] == "掃描公文.pdf"
    assert file_part["file_data"].startswith("data:application/pdf;base64,")


# ── run_turn: streamed text/tool events, sources, usage ────────────────────


class FakeStreamResult:
    def __init__(self, events) -> None:
        self._events = events
        self.context_wrapper = SimpleNamespace(usage=Usage(requests=1, input_tokens=5, output_tokens=7, total_tokens=12))

    async def stream_events(self):
        for event in self._events:
            yield event


class FakeRunner:
    def __init__(self, events) -> None:
        self._events = events

    def run_streamed(self, agent, input, **kwargs):
        return FakeStreamResult(self._events)


@pytest.mark.asyncio
async def test_run_turn_streams_text_deltas_and_a_final_sources_and_usage_event(tmp_path):
    events = [
        RawResponsesStreamEvent(data=SimpleNamespace(type="response.output_text.delta", delta="你好")),
        RawResponsesStreamEvent(data=SimpleNamespace(type="response.output_text.delta", delta="！")),
        RawResponsesStreamEvent(data=SimpleNamespace(type="some.other.event")),
    ]
    repository = InMemoryDocumentRepository()
    engine = AgentsSdkChatEngine(
        document_repository=repository,
        vector_store_id_provider=lambda: None,
        attachment_storage=ChatAttachmentStorage(tmp_path),
        client=FakeOpenAIClient([]),
        runner_factory=FakeRunner(events),
    )
    user_message = ChatMessage(session_id=uuid4(), role=ChatMessageRole.USER.value, content="哈囉")

    collected = [
        event
        async for event in engine.run_turn(history=[], user_message=user_message, attachments=[], model="gpt-6-luna")
    ]

    types = [event.type for event in collected]
    assert types == ["text_delta", "text_delta", "sources", "usage"]
    assert "".join(event.data["text"] for event in collected if event.type == "text_delta") == "你好！"
    assert collected[-2].data == {"sources": []}
    assert collected[-1].data == {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}


@pytest.mark.asyncio
async def test_run_turn_yields_an_error_event_when_the_model_run_fails(tmp_path):
    class FailingRunner:
        def run_streamed(self, agent, input, **kwargs):
            raise RuntimeError("boom")

    repository = InMemoryDocumentRepository()
    engine = AgentsSdkChatEngine(
        document_repository=repository,
        vector_store_id_provider=lambda: None,
        attachment_storage=ChatAttachmentStorage(tmp_path),
        client=FakeOpenAIClient([]),
        runner_factory=FailingRunner(),
    )
    user_message = ChatMessage(session_id=uuid4(), role=ChatMessageRole.USER.value, content="哈囉")

    collected = [
        event
        async for event in engine.run_turn(history=[], user_message=user_message, attachments=[], model="gpt-6-luna")
    ]

    assert len(collected) == 1
    assert collected[0].type == "error"
