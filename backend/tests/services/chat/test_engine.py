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
    BASE_INSTRUCTIONS,
    EXPIRED_ATTACHMENT_NOTE,
    TURN_CONTEXT_CLOSE,
    TURN_CONTEXT_OPEN,
    INLINE_USER_MESSAGE_WINDOW,
    READ_ATTACHMENT_TOOL_NAME,
    SEARCH_TOOL_NAME,
    AgentsSdkChatEngine,
    _SafeStream,
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


@pytest.mark.asyncio
async def test_read_attachment_pdf_requests_a_new_name_mention_and_failed_pdf_is_unavailable(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    attachment = ChatAttachment(
        session_id=uuid4(), display_name="舊公文.pdf", mime_type="application/pdf",
        kind=ChatAttachmentKind.DOCUMENT.value, size_bytes=1, storage_key="unused",
        status=ChatAttachmentStatus.READY.value,
    )
    engine = _engine(document_repository=InMemoryDocumentRepository(), storage=storage)
    tool = engine._read_attachment_tool({attachment.id: attachment}, {})
    ctx = _tool_context(READ_ATTACHMENT_TOOL_NAME, {"name": attachment.display_name})
    output = await tool.on_invoke_tool(ctx, json.dumps({"name": attachment.display_name}))
    assert "新訊息提及附件名稱" in output
    assert "已直接附" not in output

    attachment.status = ChatAttachmentStatus.FAILED.value
    failed_tool = engine._read_attachment_tool({attachment.id: attachment}, {})
    failed = await failed_tool.on_invoke_tool(ctx, json.dumps({"name": attachment.display_name}))
    assert failed == "這個附件目前無法讀取。"


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
        self.calls = []

    def run_streamed(self, agent, input, **kwargs):
        self.calls.append((agent, input))
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


@pytest.mark.asyncio
async def test_attachment_expires_after_three_user_messages_and_reopens_by_name_this_turn(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    session_id, attachment_id = uuid4(), uuid4()
    _, storage_key = await storage.save(session_id, attachment_id, ".pdf", b"%PDF-1.4 fake")
    attachment = ChatAttachment(
        id=attachment_id, session_id=session_id, display_name="公文.pdf",
        mime_type="application/pdf", kind=ChatAttachmentKind.DOCUMENT.value,
        size_bytes=13, storage_key=storage_key, status=ChatAttachmentStatus.READY.value,
    )
    first = ChatMessage(
        session_id=session_id, role=ChatMessageRole.USER.value, content="請讀附件",
        attachment_ids=[str(attachment_id)],
    )
    runner = FakeRunner([])
    engine = AgentsSdkChatEngine(
        document_repository=InMemoryDocumentRepository(),
        vector_store_id_provider=lambda: None, attachment_storage=storage,
        client=FakeOpenAIClient([]), runner_factory=runner,
    )
    history = [first]
    for turn in range(1, INLINE_USER_MESSAGE_WINDOW + 1):
        current = ChatMessage(
            session_id=session_id, role=ChatMessageRole.USER.value, content=f"追問{turn}",
        )
        await anext(engine.run_turn(history=history, user_message=current, attachments=[attachment], model="gpt-6-luna"))
        old_item = runner.calls[-1][1][0]
        if turn < INLINE_USER_MESSAGE_WINDOW:
            assert old_item["content"][1]["type"] == "input_file"
        else:
            placeholder = old_item["content"]
            assert placeholder == (
                "請讀附件" + EXPIRED_ATTACHMENT_NOTE.format(name="公文.pdf")
            )
        history.append(current)
    mentioned = ChatMessage(
        session_id=session_id, role=ChatMessageRole.USER.value, content="再看公文.pdf",
    )
    await anext(engine.run_turn(history=history, user_message=mentioned, attachments=[attachment], model="gpt-6-luna"))
    assert runner.calls[-1][1][0]["content"] == placeholder
    assert runner.calls[-1][1][-1]["content"][1]["type"] == "input_file"
    assert all(tool.name != "open_attachment" for tool in runner.calls[-1][0].tools)
    # No reopening tool exists, so the placeholder must not point the model at one.
    assert "open_attachment" not in EXPIRED_ATTACHMENT_NOTE
    other_session = ChatMessage(session_id=uuid4(), role=ChatMessageRole.USER.value, content="公文.pdf")
    await anext(engine.run_turn(history=[], user_message=other_session, attachments=[attachment], model="gpt-6-luna"))
    last_content = runner.calls[-1][1][-1]["content"]
    # A plain string means text only: nothing was inlined.
    assert isinstance(last_content, str) or all(part["type"] != "input_file" for part in last_content)


@pytest.mark.asyncio
async def test_static_instructions_context_last_and_reasoning_summary_delta_only(tmp_path):
    runner = FakeRunner([
        RawResponsesStreamEvent(data=SimpleNamespace(
            type="response.reasoning_summary_text.delta", delta="分析摘要",
        )),
        RawResponsesStreamEvent(data=SimpleNamespace(type="response.reasoning_text.delta", delta="私密思路")),
    ])
    engine = AgentsSdkChatEngine(
        document_repository=InMemoryDocumentRepository(),
        vector_store_id_provider=lambda: None, attachment_storage=ChatAttachmentStorage(tmp_path),
        client=FakeOpenAIClient([]), runner_factory=runner,
    )
    old = ChatMessage(session_id=uuid4(), role=ChatMessageRole.ASSISTANT.value, content="前文")
    current = ChatMessage(session_id=old.session_id, role=ChatMessageRole.USER.value, content="續問")
    result = [
        event async for event in engine.run_turn(
            history=[old], user_message=current, attachments=[], model="gpt-6-luna", summary="既有摘要",
        )
    ]
    assert [event.data["text"] for event in result if event.type == "reasoning_delta"] == ["分析摘要"]
    agent, items = runner.calls[0]
    assert agent.instructions == BASE_INSTRUCTIONS
    assert agent.model_settings.reasoning.summary == "auto"
    assert items[0] == {"role": "assistant", "content": "前文"}
    assert items[-1]["content"] == [
        {"type": "input_text", "text": "續問"},
        {"type": "input_text", "text": f"{TURN_CONTEXT_OPEN}\n先前對話摘要：\n既有摘要\n{TURN_CONTEXT_CLOSE}"},
    ]
    other_session = ChatMessage(session_id=uuid4(), role=ChatMessageRole.USER.value, content="請問")
    await anext(engine.run_turn(
        history=[], user_message=other_session, attachments=[], model="gpt-4o",
    ))
    # Nothing to say: no context block, so the model can't mistake it for the user's words.
    assert runner.calls[-1][1][-1]["content"] == "請問"
    assert runner.calls[-1][0].instructions == BASE_INSTRUCTIONS
    assert runner.calls[-1][0].model_settings.reasoning is None


@pytest.mark.asyncio
async def test_turn_context_describes_failed_attachments_without_inlining_them(tmp_path):
    session_id = uuid4()
    attachment = ChatAttachment(
        session_id=session_id, display_name="失敗附件.pdf", mime_type="application/pdf",
        kind=ChatAttachmentKind.DOCUMENT.value, size_bytes=1, storage_key="not-present",
        status=ChatAttachmentStatus.FAILED.value,
    )
    runner = FakeRunner([])
    engine = AgentsSdkChatEngine(
        document_repository=InMemoryDocumentRepository(),
        vector_store_id_provider=lambda: None, attachment_storage=ChatAttachmentStorage(tmp_path),
        client=FakeOpenAIClient([]), runner_factory=runner,
    )
    message = ChatMessage(
        session_id=session_id, role=ChatMessageRole.USER.value,
        content="這份文件如何？", attachment_ids=[str(attachment.id)],
    )
    await anext(engine.run_turn(
        history=[], user_message=message, attachments=[attachment], model="gpt-6-luna",
    ))
    parts = runner.calls[-1][1][-1]["content"]
    assert parts == [
        {"type": "input_text", "text": "這份文件如何？"},
        {"type": "input_text", "text": f"{TURN_CONTEXT_OPEN}\n這個對話目前的附件：\n- 失敗附件.pdf（無法讀取；處理失敗）\n{TURN_CONTEXT_CLOSE}"},
    ]


@pytest.mark.asyncio
async def test_summarize_uses_one_tool_free_call_with_previous_summary_and_visible_names(tmp_path):
    class SummaryRunner:
        def __init__(self):
            self.calls = []

        async def run(self, agent, input, **kwargs):
            self.calls.append((agent, input))
            return SimpleNamespace(final_output="摘要結果")

    runner = SummaryRunner()
    engine = AgentsSdkChatEngine(
        document_repository=InMemoryDocumentRepository(),
        vector_store_id_provider=lambda: None, attachment_storage=ChatAttachmentStorage(tmp_path),
        client=FakeOpenAIClient([]), runner_factory=runner,
    )
    session_id, attachment_id = uuid4(), uuid4()
    attachment = ChatAttachment(
        id=attachment_id, session_id=session_id, display_name="圖表.pdf",
        mime_type="application/pdf", kind=ChatAttachmentKind.DOCUMENT.value,
        size_bytes=13, storage_key="hidden", status=ChatAttachmentStatus.READY.value,
    )
    message = ChatMessage(
        session_id=session_id, role=ChatMessageRole.USER.value, content="規範是多少？",
        attachment_ids=[str(attachment_id)],
    )
    assert await engine.summarize(
        history=[message], previous_summary="先前決定", attachments=[attachment], model="gpt-6-luna",
    ) == "摘要結果"
    agent, prompt = runner.calls[0]
    assert agent.tools == []
    assert "先前決定" in prompt and "圖表.pdf" in prompt and "規範是多少？" in prompt
    assert str(attachment_id) not in prompt and attachment.storage_key not in prompt


@pytest.mark.asyncio
async def test_split_internal_identifiers_never_reach_reasoning_or_answer(tmp_path):
    session_id = uuid4()
    unknown_id = str(uuid4())
    user_message = ChatMessage(session_id=session_id, role=ChatMessageRole.USER.value, content="請回答")
    identifier = str(session_id)
    runner = FakeRunner([
        RawResponsesStreamEvent(data=SimpleNamespace(
            type="response.reasoning_summary_text.delta", delta=f"摘要 {identifier[:11]}",
        )),
        RawResponsesStreamEvent(data=SimpleNamespace(
            type="response.reasoning_summary_text.delta", delta=f"{identifier[11:]} 與資料",
        )),
        RawResponsesStreamEvent(data=SimpleNamespace(
            type="response.output_text.delta", delta="答案 file-6F2ksmvX",
        )),
        RawResponsesStreamEvent(data=SimpleNamespace(
            type="response.output_text.delta", delta="xt4VdoqmHRw6kL 結束",
        )),
        RawResponsesStreamEvent(data=SimpleNamespace(
            type="response.output_text.delta", delta=f" 未知 {unknown_id[:16]}",
        )),
        RawResponsesStreamEvent(data=SimpleNamespace(
            type="response.output_text.delta", delta=unknown_id[16:] + " 收尾",
        )),
    ])
    engine = AgentsSdkChatEngine(
        document_repository=InMemoryDocumentRepository(),
        vector_store_id_provider=lambda: None, attachment_storage=ChatAttachmentStorage(tmp_path),
        client=FakeOpenAIClient([]), runner_factory=runner,
    )
    events = [
        event async for event in engine.run_turn(
            history=[], user_message=user_message, attachments=[], model="gpt-6-luna",
        )
    ]
    reasoning = "".join(event.data["text"] for event in events if event.type == "reasoning_delta")
    answer = "".join(event.data["text"] for event in events if event.type == "text_delta")
    assert reasoning == "摘要 （內部資訊已略） 與資料"
    assert answer == "答案 （內部資訊已略） 結束 未知 （內部資訊已略） 收尾"
    assert identifier not in reasoning + answer and unknown_id not in answer


def test_ordinary_words_that_look_like_id_prefixes_are_not_redacted():
    stream = _SafeStream([], [])
    text = "採用 file-based 儲存，比較 vs_old 方案"

    assert stream.feed(text, final=True) == text
