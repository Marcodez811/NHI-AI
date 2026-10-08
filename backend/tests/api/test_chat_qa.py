from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes.chat import (
    QaDocumentsConfirm,
    QaOutlineUpdate,
    QaQuestionsUpdate,
    confirm_all_qa_outlines,
    confirm_qa_documents,
    confirm_qa_outline,
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    get_qa_workspace,
    send_chat_message,
    set_chat_session_skill,
    update_qa_outline,
    update_qa_questions,
)
from app.models.chat import (
    ChatMessageCreate,
    ChatSessionCreate,
    ChatSessionSkillUpdate,
    UserFile,
)
from app.services.chat.engine import ChatEvent
from app.services.chat.repository import InMemoryChatRepository
from app.services.chat.skills import legislative_qa as qa
from pydantic import ValidationError


async def _session(repository):
    created = await create_chat_session(ChatSessionCreate(), repository)
    return created.id


async def _attach_file(repository, session_id, name="資料甲.txt") -> UserFile:
    file = await repository.create_file(UserFile(
        display_name=name, mime_type="text/plain", kind="document", size_bytes=1, storage_key="k", text_chars=5,
    ))
    await repository.link_file(session_id, file.id)
    return file


@pytest.mark.asyncio
async def test_set_and_clear_skill_is_sticky_and_shown_in_the_session():
    repository = InMemoryChatRepository()
    session_id = await _session(repository)
    assert (await get_chat_session(session_id, repository)).skill is None

    summary = await set_chat_session_skill(session_id, ChatSessionSkillUpdate(skill="legislative_qa"), repository)
    assert summary.skill == "legislative_qa"
    assert (await get_chat_session(session_id, repository)).skill == "legislative_qa"

    cleared = await set_chat_session_skill(session_id, ChatSessionSkillUpdate(skill=None), repository)
    assert cleared.skill is None

    with pytest.raises(ValidationError):
        ChatSessionSkillUpdate(skill="other")
    with pytest.raises(HTTPException) as missing:
        await set_chat_session_skill(uuid4(), ChatSessionSkillUpdate(skill=None), repository)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_card_actions_walk_the_stage_and_leaving_the_skill_keeps_the_workspace():
    repository = InMemoryChatRepository()
    session_id = await _session(repository)
    empty = await get_qa_workspace(session_id, repository)
    assert empty.stage == "questions" and empty.questions == {"items": [], "confirmed": False}
    assert empty.versions == [] and empty.documents == {"confirmed": False, "used": []}

    workspace = await update_qa_questions(
        session_id,
        QaQuestionsUpdate(questions=[qa.QuestionEdit(text="甲題"), qa.QuestionEdit(text="乙題", note="n")]),
        repository,
    )
    assert workspace.stage == "documents" and workspace.questions["confirmed"] is True

    with pytest.raises(HTTPException) as unattached:
        await confirm_qa_documents(session_id, QaDocumentsConfirm(attachment_ids=[uuid4()]), repository)
    assert unattached.value.status_code == 422
    assert unattached.value.detail["code"] == "qa_document_not_attached"

    file = await _attach_file(repository, session_id)
    workspace = await confirm_qa_documents(session_id, QaDocumentsConfirm(attachment_ids=[file.id]), repository)
    assert workspace.stage == "evidence"
    assert workspace.documents == {
        "confirmed": True, "used": [{"id": str(file.id), "name": "資料甲.txt", "source": "upload"}],
    }

    with pytest.raises(HTTPException) as early:
        await confirm_all_qa_outlines(session_id, repository)
    assert early.value.status_code == 409

    outline = QaOutlineUpdate(short=["重點"])
    workspace = await update_qa_outline(session_id, 1, outline, repository)
    assert workspace.stage == "outline" and workspace.outline["1"]["confirmed"] is False
    assert [s["title"] for s in workspace.outline["1"]["detail"]] == ["背景說明", "目前辦理情形", "未來工作重點"]
    workspace = await confirm_qa_outline(session_id, 1, repository)
    assert workspace.outline["1"]["confirmed"] is True and workspace.stage == "outline"
    with pytest.raises(HTTPException) as no_outline:
        await confirm_qa_outline(session_id, 2, repository)
    assert no_outline.value.status_code == 409
    with pytest.raises(HTTPException) as unknown:
        await update_qa_outline(session_id, 9, outline, repository)
    assert unknown.value.detail["code"] == "qa_question_not_found"
    with pytest.raises(HTTPException) as dispute:
        await update_qa_outline(
            session_id, 2, QaOutlineUpdate(short=["x"], detail=[qa.DetailSection(title="爭議點", points=[])]), repository,
        )
    assert dispute.value.detail["code"] == "qa_outline_dispute"

    # Editing an outline un-confirms it; confirm=true confirms in the same step.
    workspace = await update_qa_outline(session_id, 2, QaOutlineUpdate(short=["乙"], confirm=True), repository)
    assert workspace.outline["2"]["confirmed"] is True
    workspace = await confirm_all_qa_outlines(session_id, repository)
    assert workspace.stage == "ready"

    # Leaving the mode keeps the data; changing a question steps the flow back.
    await set_chat_session_skill(session_id, ChatSessionSkillUpdate(skill="legislative_qa"), repository)
    await set_chat_session_skill(session_id, ChatSessionSkillUpdate(skill=None), repository)
    assert (await get_qa_workspace(session_id, repository)).stage == "ready"
    workspace = await update_qa_questions(
        session_id,
        QaQuestionsUpdate(questions=[qa.QuestionEdit(no=1, text="甲題"), qa.QuestionEdit(no=2, text="乙題改")]),
        repository,
    )
    assert workspace.stage == "outline" and "2" not in workspace.outline

    with pytest.raises(HTTPException) as gone:
        await get_qa_workspace(uuid4(), repository)
    assert gone.value.status_code == 404


@pytest.mark.asyncio
async def test_deleting_the_session_deletes_its_workspace():
    repository = InMemoryChatRepository()
    session_id = await _session(repository)
    await update_qa_questions(session_id, QaQuestionsUpdate(questions=[qa.QuestionEdit(text="甲")]), repository)
    await delete_chat_session(session_id, repository)
    assert repository.qa_workspaces == {}


class CardEngine:
    def __init__(self):
        self.calls: list[dict] = []

    async def summarize(self, **kwargs):
        raise AssertionError

    async def run_turn(self, *, history, user_message, attachments, model, summary=None, skill=None):
        self.calls.append({"skill": skill})
        yield ChatEvent("text_delta", {"text": "已整理"})
        if skill is not None:
            yield ChatEvent("skill_card", {"kind": "questions", "data": {"items": [{"no": 1, "text": "甲", "note": ""}], "confirmed": False}})
        yield ChatEvent("sources", {"sources": []})


@pytest.mark.asyncio
async def test_skill_cards_stream_and_persist_with_the_assistant_message():
    repository = InMemoryChatRepository()
    session_id = await _session(repository)
    engine = CardEngine()

    async def send(text):
        response = await send_chat_message(session_id, ChatMessageCreate(content=text, model="gpt-6-astra"), repository, engine)
        chunks = [chunk async for chunk in response.body_iterator]
        return [json.loads(c[len("data: "):]) for c in chunks if c.startswith("data: ")]

    plain = await send("一般")
    assert engine.calls[-1]["skill"] is None
    assert all(e["type"] != "skill_card" for e in plain)

    await set_chat_session_skill(session_id, ChatSessionSkillUpdate(skill="legislative_qa"), repository)
    events = await send("貼上題目")
    runtime = engine.calls[-1]["skill"]
    assert runtime.skill == "legislative_qa" and runtime.session_id == session_id
    card = {"kind": "questions", "data": {"items": [{"no": 1, "text": "甲", "note": ""}], "confirmed": False}}
    assert [e for e in events if e["type"] == "skill_card"] == [{"type": "skill_card", **card}]

    detail = await get_chat_session(session_id, repository)
    assistants = [m for m in detail.messages if m.role == "assistant"]
    assert assistants[0].cards == [] and assistants[1].cards == [card]
