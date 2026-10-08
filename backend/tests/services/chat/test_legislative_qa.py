from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import Usage
from agents.tool_context import ToolContext
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.models.chat import (
    ChatAttachmentKind,
    ChatAttachmentStatus,
    ChatAttachmentView,
    ChatMessage,
    ChatMessageRole,
    ChatQaWorkspace,
    ChatSession,
    utcnow,
)
from app.services.chat.attachments import ChatAttachmentStorage
from app.services.chat.engine import (
    BASE_INSTRUCTIONS,
    TURN_CONTEXT_OPEN,
    AgentsSdkChatEngine,
)
from app.services.chat.repository import InMemoryChatRepository, SQLModelChatRepository
from app.services.chat.skills import legislative_qa as qa
from app.services.documents.repository import InMemoryDocumentRepository


def _attachment(name: str, *, source: str = "knowledge_base", status: str = "ready") -> ChatAttachmentView:
    return ChatAttachmentView(
        id=uuid4(), session_id=uuid4(), display_name=name, mime_type="text/plain",
        kind=ChatAttachmentKind.DOCUMENT.value, size_bytes=1, storage_key="x", status=status,
        error=None, text_chars=10, created_at=utcnow(), source=source,
    )


def _ctx(name: str, arguments: dict) -> ToolContext:
    return ToolContext(
        context=None, tool_name=name, tool_call_id=f"call-{name}", tool_arguments=json.dumps(arguments),
    )


class Harness:
    def __init__(self, attachments=(), hits=()):
        self.repository = InMemoryChatRepository()
        self.session_id = uuid4()
        self.attachments = list(attachments)
        self.cards: list[tuple[str, dict]] = []
        self.labels: dict[str, str] = {}
        self.queries: list[str] = []
        self.hits = list(hits)

        async def search(query, max_results):
            self.queries.append(query)
            return self.hits

        runtime = qa.SkillRuntime(skill=qa.SKILL_ID, session_id=self.session_id, repository=self.repository)
        tools = qa.build_tools(
            runtime, self.attachments, search, lambda kind, data: self.cards.append((kind, data)), self.labels,
        )
        self.tools = {tool.name: tool for tool in tools}

    async def call(self, name: str, arguments: dict) -> str:
        return await self.tools[name].on_invoke_tool(_ctx(name, arguments), json.dumps(arguments))

    async def workspace(self) -> ChatQaWorkspace:
        return await self.repository.get_qa_workspace(self.session_id)


QUESTIONS = {"questions": [{"text": "113年醫療給付改善方案成效？", "note": ""}, {"text": "藥價調整進度？", "note": "含爭議"}]}


async def _confirmed(harness: Harness, *, docs=True) -> None:
    await harness.call("set_questions", QUESTIONS)
    workspace = await harness.workspace()
    workspace.questions_confirmed = True
    if docs:
        qa.confirm_documents(workspace, harness.attachments, [a.id for a in harness.attachments])
    await harness.repository.save_qa_workspace(workspace)


@pytest.mark.asyncio
async def test_set_questions_records_numbers_emits_a_card_and_awaits_confirmation():
    harness = Harness()
    result = await harness.call("set_questions", QUESTIONS)

    workspace = await harness.workspace()
    assert [(q["no"], q["note"]) for q in workspace.questions] == [(1, ""), (2, "含爭議")]
    assert workspace.questions_confirmed is False and workspace.stage == "questions"
    assert harness.cards == [("questions", {"items": workspace.questions, "confirmed": False})]
    assert "2 題" in result and str(harness.session_id) not in result


@pytest.mark.asyncio
async def test_set_questions_rejects_blank_text():
    harness = Harness()
    result = await harness.call("set_questions", {"questions": [{"text": "  ", "note": ""}]})
    assert "不可空白" in result and harness.cards == [] and await harness.workspace() is None


@pytest.mark.asyncio
async def test_suggest_documents_needs_confirmed_questions_and_never_attaches():
    kb = _attachment("已附加文件")
    new_id = uuid4()
    hits = [
        {"document_id": new_id, "name": "新文件", "snippet": "內容" * 200},
        {"document_id": kb.id, "name": "已附加文件", "snippet": "x"},
        {"document_id": new_id, "name": "新文件", "snippet": "重複"},
    ]
    harness = Harness([kb], hits)
    await harness.call("set_questions", QUESTIONS)
    assert "還沒有被使用者確認" in await harness.call("suggest_documents", {"question_nos": []})
    assert harness.queries == []

    workspace = await harness.workspace()
    workspace.questions_confirmed = True
    await harness.repository.save_qa_workspace(workspace)
    harness.cards.clear()
    result = await harness.call("suggest_documents", {"question_nos": [2]})

    assert harness.queries == ["藥價調整進度？"]
    kind, data = harness.cards[0]
    assert kind == "documents"
    candidates = data["items"][0]["candidates"]
    assert [(c["name"], c["attached"]) for c in candidates] == [("新文件", False), ("已附加文件", True)]
    assert candidates[0]["id"] == str(new_id) and len(candidates[0]["snippet"]) == 200
    assert str(new_id) not in result and "【新文件】" in result
    assert harness.repository.document_links == {}


@pytest.mark.asyncio
async def test_record_evidence_requires_attached_sources_and_is_all_or_nothing():
    attached = _attachment("衛福部年報")
    harness = Harness([attached])
    await _confirmed(harness)
    good = {"field": "figures", "text": "113年預算 100 億元", "source_name": "衛福部年報", "quote": "預算為100億元"}
    bad = {"field": "aim", "text": "目的", "source_name": "網路新聞", "quote": "原文"}

    rejected = await harness.call("record_evidence", {"question_no": 1, "items": [good, bad]})
    assert "網路新聞" in rejected and "整批未記錄" in rejected
    assert (await harness.workspace()).evidence == {} and harness.cards[-1][0] == "questions"

    accepted = await harness.call("record_evidence", {"question_no": 1, "items": [good]})
    workspace = await harness.workspace()
    entry = workspace.evidence["1"]
    assert entry["figures"] == [{
        "text": "113年預算 100 億元", "source": {"id": str(attached.id), "name": "衛福部年報"}, "quote": "預算為100億元",
    }]
    assert entry["aim"] == [] and str(attached.id) not in accepted
    kind, data = harness.cards[-1]
    assert kind == "evidence" and data["question_no"] == 1 and data["evidence"] == entry

    # Re-recording a field replaces it and keeps the other fields.
    status = {"field": "status", "text": "已完成", "source_name": "衛福部年報", "quote": "已完成"}
    await harness.call("record_evidence", {"question_no": 1, "items": [status]})
    entry = (await harness.workspace()).evidence["1"]
    assert entry["figures"] and entry["status"][0]["text"] == "已完成"

    assert "沒有第 9 題" in await harness.call("record_evidence", {"question_no": 9, "items": [good]})


@pytest.mark.asyncio
async def test_record_evidence_only_cites_confirmed_documents_and_rejects_empty_quotes():
    one, two = _attachment("文件甲"), _attachment("文件乙")
    harness = Harness([one, two])
    await _confirmed(harness, docs=False)
    workspace = await harness.workspace()
    qa.confirm_documents(workspace, harness.attachments, [one.id])
    await harness.repository.save_qa_workspace(workspace)

    item = {"field": "aim", "text": "目的", "source_name": "文件乙", "quote": "原文"}
    assert "不是這個對話中可用的資料" in await harness.call("record_evidence", {"question_no": 1, "items": [item]})
    blank = {**item, "source_name": "文件甲", "quote": " "}
    assert "出處原文" in await harness.call("record_evidence", {"question_no": 1, "items": [blank]})


@pytest.mark.asyncio
async def test_propose_outline_defaults_dispute_rule_and_moves_the_stage():
    harness = Harness([_attachment("文件甲")])
    await _confirmed(harness)
    await harness.call("propose_outline", {"question_no": 1, "short": ["重點一"], "detail": [], "dispute_requested": False})

    workspace = await harness.workspace()
    assert [s["title"] for s in workspace.outline["1"]["detail"]] == ["背景說明", "目前辦理情形", "未來工作重點"]
    assert workspace.outline["1"]["confirmed"] is False and workspace.stage == "outline"
    kind, data = harness.cards[-1]
    assert kind == "outline" and data["outline"] == workspace.outline["1"]

    sneaky = {"question_no": 2, "short": ["a"], "detail": [{"title": "爭議點", "points": []}], "dispute_requested": False}
    assert "爭議點" in await harness.call("propose_outline", sneaky)
    await harness.call("propose_outline", {"question_no": 2, "short": ["a"], "detail": [], "dispute_requested": True})
    assert (await harness.workspace()).outline["2"]["detail"][-1]["title"] == "爭議點"
    assert "簡答至少" in await harness.call(
        "propose_outline", {"question_no": 1, "short": [" "], "detail": [], "dispute_requested": False},
    )


def test_stage_is_derived_and_editing_questions_invalidates_dependents():
    attached = _attachment("文件甲")
    workspace = qa.new_workspace(uuid4())
    assert qa.recompute_stage(workspace) == "questions"
    qa.apply_questions(
        workspace, [qa.QuestionEdit(text="甲"), qa.QuestionEdit(text="乙")], confirmed=True,
    )
    assert qa.recompute_stage(workspace) == "documents"
    qa.confirm_documents(workspace, [attached], [attached.id])
    assert qa.recompute_stage(workspace) == "evidence"
    for no in (1, 2):
        qa.apply_outline(workspace, no, qa.OutlineInput(short=["x"]))
    qa.confirm_outline(workspace, 1)
    assert qa.recompute_stage(workspace) == "outline"
    qa.confirm_all_outlines(workspace)
    assert qa.recompute_stage(workspace) == "ready"

    # Reword question 2, keep 1 and add 3: only 2's outline goes; the question list needs re-confirming.
    qa.apply_questions(
        workspace,
        [qa.QuestionEdit(no=1, text="甲"), qa.QuestionEdit(no=2, text="乙改"), qa.QuestionEdit(text="丙")],
        confirmed=False,
    )
    assert sorted(workspace.outline) == ["1"] and workspace.questions[2]["no"] == 3
    assert qa.recompute_stage(workspace) == "questions"


def test_confirm_documents_rejects_unattached_or_failed_documents():
    ok, failed = _attachment("好"), _attachment("壞", status=ChatAttachmentStatus.FAILED.value)
    workspace = qa.new_workspace(uuid4())
    for ids in ([uuid4()], [failed.id], []):
        with pytest.raises(qa.QaError):
            qa.confirm_documents(workspace, [ok, failed], ids)
    qa.confirm_documents(workspace, [ok, failed], [ok.id])
    assert workspace.documents["used"] == [{"id": str(ok.id), "name": "好", "source": "knowledge_base"}]


def test_render_context_is_compact_and_has_no_ids():
    workspace = qa.new_workspace(uuid4())
    attached = _attachment("文件甲")
    qa.apply_questions(workspace, [qa.QuestionEdit(text="甲題", note="備")], confirmed=True)
    qa.confirm_documents(workspace, [attached], [attached.id])
    qa.apply_evidence(
        workspace, 1, [qa.EvidenceItemInput(field="figures", text="100億", source_name="文件甲", quote="一百億")], [attached],
    )
    qa.apply_outline(workspace, 1, qa.OutlineInput(short=["要點"]))
    text = qa.render_context(workspace)
    assert "資料輸入" not in text and "答題架構" in text
    assert "1. 甲題（備註：備）" in text and "【文件甲】" in text and "數據：100億" in text and "簡答：要點" in text
    assert str(attached.id) not in text and str(workspace.session_id) not in text
    assert "尚未設定" in qa.render_context(None)


# ── Engine wiring ───────────────────────────────────────────────────────────


class _StreamResult:
    def __init__(self, on_stream):
        self._on_stream = on_stream
        self.context_wrapper = SimpleNamespace(usage=Usage(requests=1, input_tokens=1, output_tokens=1, total_tokens=2))

    async def stream_events(self):
        if self._on_stream:
            await self._on_stream()
        return
        yield


class _Runner:
    def __init__(self, drive=None):
        self.calls = []
        self.drive = drive

    def run_streamed(self, agent, input, **kwargs):
        self.calls.append((agent, input))
        return _StreamResult(self.drive(agent) if self.drive else None)


def _engine(runner) -> AgentsSdkChatEngine:
    return AgentsSdkChatEngine(
        document_repository=InMemoryDocumentRepository(), vector_store_id_provider=lambda: None,
        attachment_storage=ChatAttachmentStorage("/tmp"), client=SimpleNamespace(), runner_factory=runner,
    )


def _message(session_id) -> ChatMessage:
    return ChatMessage(session_id=session_id, role=ChatMessageRole.USER.value, content="開始")


@pytest.mark.asyncio
async def test_skill_tools_instructions_and_workspace_context_only_in_skill_mode():
    runner = _Runner()
    engine = _engine(runner)
    repository = InMemoryChatRepository()
    session_id = uuid4()
    workspace = qa.new_workspace(session_id)
    qa.apply_questions(workspace, [qa.QuestionEdit(text="獨特題目")], confirmed=False)
    await repository.save_qa_workspace(workspace)
    runtime = qa.SkillRuntime(skill=qa.SKILL_ID, session_id=session_id, repository=repository)

    [e async for e in engine.run_turn(history=[], user_message=_message(session_id), attachments=[], model="gpt-6-luna")]
    plain_agent, plain_items = runner.calls[-1]
    assert {t.name for t in plain_agent.tools} == {"search_knowledge_base", "read_attachment"}
    assert plain_agent.instructions == BASE_INSTRUCTIONS and plain_items[-1]["content"] == "開始"

    [e async for e in engine.run_turn(
        history=[], user_message=_message(session_id), attachments=[], model="gpt-6-luna", skill=runtime,
    )]
    agent, items = runner.calls[-1]
    assert {t.name for t in agent.tools} == {
        "search_knowledge_base", "read_attachment", "set_questions", "suggest_documents", "record_evidence",
        "propose_outline",
    }
    assert agent.instructions.startswith(BASE_INSTRUCTIONS) and "立院QA" in agent.instructions
    assert "民國年" in agent.instructions
    context = items[-1]["content"][-1]["text"]
    assert context.startswith(TURN_CONTEXT_OPEN) and "1. 獨特題目" in context and "尚未確認" in context


@pytest.mark.asyncio
async def test_run_turn_yields_a_skill_card_event_after_a_tool_runs():
    repository = InMemoryChatRepository()
    session_id = uuid4()

    def drive(agent):
        async def run():
            tool = next(t for t in agent.tools if t.name == "set_questions")
            args = json.dumps(QUESTIONS)
            await tool.on_invoke_tool(_ctx("set_questions", QUESTIONS), args)
        return run

    engine = _engine(_Runner(drive))
    runtime = qa.SkillRuntime(skill=qa.SKILL_ID, session_id=session_id, repository=repository)
    events = [e async for e in engine.run_turn(
        history=[], user_message=_message(session_id), attachments=[], model="gpt-6-luna", skill=runtime,
    )]
    assert [e.type for e in events] == ["skill_card", "sources", "usage"]
    assert events[0].data["kind"] == "questions" and len(events[0].data["data"]["items"]) == 2


# ── Persistence ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sql_repository_round_trips_the_workspace_and_cascades_on_session_delete():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        repository = SQLModelChatRepository(db)
        session = await repository.create_session(ChatSession(skill="legislative_qa"))
        workspace = qa.new_workspace(session.id)
        qa.apply_questions(workspace, [qa.QuestionEdit(text="甲")], confirmed=True)
        await repository.save_qa_workspace(workspace)
        # A second save updates the same row.
        again = qa.clone(await repository.get_qa_workspace(session.id))
        qa.apply_outline(again, 1, qa.OutlineInput(short=["x"]))
        await repository.save_qa_workspace(again)
        await repository.create_message(ChatMessage(
            session_id=session.id, role="assistant", content="", cards=[{"kind": "questions", "data": {"items": []}}],
        ))

        stored = await repository.get_qa_workspace(session.id)
        assert stored.questions[0]["text"] == "甲" and "1" in stored.outline and stored.versions == []
        assert (await repository.list_messages(session.id))[0].cards[0]["kind"] == "questions"

        await repository.delete_session(session.id)
        assert db.exec(select(ChatQaWorkspace)).all() == []


@pytest.mark.asyncio
async def test_in_memory_delete_removes_the_workspace_but_clearing_the_skill_keeps_it():
    repository = InMemoryChatRepository()
    session = await repository.create_session(ChatSession(skill="legislative_qa"))
    await repository.save_qa_workspace(qa.new_workspace(session.id))
    session.skill = None
    await repository.update_session(session)
    assert await repository.get_qa_workspace(session.id) is not None
    await repository.delete_session(session.id)
    assert await repository.get_qa_workspace(session.id) is None
