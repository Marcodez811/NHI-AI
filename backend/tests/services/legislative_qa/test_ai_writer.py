from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.db as database
from app.config import settings
from app.models.chat import ChatQaWorkspace, ChatSession
from app.models.legislative_qa import QaTaskPayload
from app.services.agentic.contracts import AgentExecutionResult, AgentTaskPayload, DeterministicValidationError, WorkflowStatus
from app.services.agentic.registry import WorkflowRegistry
from app.services.agentic.service import execute_workflow
from app.services.legislative_qa.adapter import LegislativeQaAdapter
from app.services.legislative_qa.writer import build_writer_input, check_output


def _evidence(text: str, source: str) -> dict:
    return {"text": text, "source": {"id": str(uuid4()), "name": source}, "quote": f"原文：{text}"}


def _workspace(session_id=None) -> ChatQaWorkspace:
    return ChatQaWorkspace(
        session_id=session_id or uuid4(),
        questions=[{"no": 1, "text": "甲題"}, {"no": 2, "text": "乙題"}],
        questions_confirmed=True,
        documents={"confirmed": True, "used": []},
        evidence={
            "1": {"figures": [_evidence("點值 0.9594", "甲.pdf")], "aim": [_evidence("保障基本收入", "乙.docx")]},
            "2": {"status": [_evidence("乙題現況", "丙.pdf")]},
        },
        outline={
            "1": {
                "short": ["點值達 0.9594【甲.pdf】"],
                "detail": [{"title": "背景說明", "points": ["背景"]}, {"title": "目前辦理情形", "points": []}],
                "notes": ["待補：不可外流"],
                "confirmed": True,
            },
            "2": {"short": ["乙"], "detail": [{"title": "背景說明", "points": []}], "confirmed": True},
        },
    )


def _answer(no: int, headings: list[str], cites: list[str]) -> dict:
    para = {"text": "本署說明 0.9594 元。", "cites": cites}
    return {
        "no": no,
        "short": [{"heading": "成效數據", "paragraphs": [para]}],
        "detail": [{"heading": h, "paragraphs": [para]} for h in headings],
    }


def _good() -> dict:
    return {"questions": [_answer(1, ["背景說明", "目前辦理情形"], ["q1-e1", "q1-e2"]), _answer(2, ["背景說明"], ["q2-e1"])]}


def test_input_has_stable_ids_and_excludes_notes_and_foreign_evidence():
    frozen = build_writer_input(_workspace(), " 11-5 ")
    assert frozen["session_label"] == "11-5"
    first, second = frozen["questions"]
    assert [item["id"] for item in first["evidence"]] == ["q1-e1", "q1-e2"]
    assert first["evidence"][0] == {
        "id": "q1-e1", "field": "figures", "text": "點值 0.9594", "source": "甲.pdf", "quote": "原文：點值 0.9594",
    }
    assert [item["id"] for item in second["evidence"]] == ["q2-e1"]
    dumped = json.dumps(frozen, ensure_ascii=False)
    assert "待補" not in dumped and "【" not in dumped and "丙.pdf" not in json.dumps(first, ensure_ascii=False)


def _problem(data: dict) -> str:
    with pytest.raises(DeterministicValidationError) as error:
        check_output(build_writer_input(_workspace(), None), json.dumps(data, ensure_ascii=False))
    return str(error.value)


def test_check_accepts_a_valid_output():
    output = check_output(build_writer_input(_workspace(), None), json.dumps(_good(), ensure_ascii=False))
    assert [q.no for q in output.questions] == [1, 2]


def test_check_rejects_unknown_cites_and_foreign_question_cites():
    data = _good()
    data["questions"][0]["short"][0]["paragraphs"][0]["cites"] = ["q2-e1"]
    assert "q2-e1" in _problem(data)


def test_check_rejects_wrong_detail_order_and_missing_question():
    data = _good()
    data["questions"][0]["detail"].reverse()
    assert "詳答標題必須依序為" in _problem(data)
    data = _good()
    data["questions"].pop()
    assert "必須依序包含" in _problem(data)


def test_check_rejects_tags_files_and_uncited_numbers():
    for text in ("見【甲.pdf】", "依甲.pdf 所載", "根據證據", "引用 q1-e1"):
        data = _good()
        data["questions"][0]["short"][0]["paragraphs"][0]["text"] = text
        assert _problem(data)
    data = _good()
    data["questions"][0]["short"][0]["paragraphs"][0]["cites"] = []
    assert "含數字但沒有引用" in _problem(data)
    data = _good()
    data["questions"][0]["short"][0]["paragraphs"][0]["text"] = "  "
    assert "空白段落" in _problem(data)


def test_check_normalizes_numbered_headings():
    data = _good()
    data["questions"][0]["detail"][0]["heading"] = "一、背景說明："
    check_output(build_writer_input(_workspace(), None), json.dumps(data, ensure_ascii=False))


class WritingRunner:
    """Fake author: writes ``output/qa.json`` from a queue of payloads."""

    def __init__(self, outputs: list[dict]):
        self.outputs, self.requests = outputs, []

    async def run(self, request, *, progress_callback=None):
        self.requests.append(request)
        (request.workspace / "output" / "qa.json").write_text(json.dumps(self.outputs.pop(0), ensure_ascii=False), encoding="utf-8")
        return AgentExecutionResult(provider_run_id="r", response="done")


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(settings, "documents_root", tmp_path / "docs")
    session_id = uuid4()
    with Session(engine) as session:
        session.add(ChatSession(id=session_id))
        workspace = _workspace(session_id)
        workspace.generation = {"job_id": "x"}
        session.add(workspace)
        session.commit()
    return engine, session_id


async def _run(tmp_path, session_id, runner):
    job_id = uuid4()
    payload = QaTaskPayload(job_id=job_id, session_id=session_id, session_label="11-5")
    result = await execute_workflow(
        AgentTaskPayload(job_id=job_id, workflow="legislative_qa", input=payload.model_dump(mode="json")),
        registry=WorkflowRegistry({"legislative_qa": LegislativeQaAdapter()}),
        runner=runner, workspace_root=tmp_path / "jobs", timeout_seconds=5,
    )
    return job_id, result


@pytest.mark.asyncio
async def test_job_publishes_an_ai_version_with_deduplicated_sources(tmp_path: Path, setup):
    engine, session_id = setup
    runner = WritingRunner([_good()])
    job_id, result = await _run(tmp_path, session_id, runner)
    assert result.status is WorkflowStatus.COMPLETED, result.error
    request = runner.requests[0]
    assert request.node_id == "author" and request.skill_names == ("nhi-legislative-qa-writing",)
    assert request.restrict_workspace is True
    with Session(engine) as session:
        stored = session.get(ChatQaWorkspace, session_id)
        assert stored.generation == {} and stored.stage == "ready"
        version = stored.versions[0]
        assert version["kind"] == "ai" and version["artifact_id"] == str(result.output["artifact_id"])
    saved = json.loads(next((tmp_path / "docs" / "artifacts").glob("*.json")).read_text(encoding="utf-8"))
    assert saved["kind"] == "ai" and saved["sources"] == ["甲.pdf", "乙.docx", "丙.pdf"]
    assert saved["questions"][0]["short"][0]["paragraphs"][0]["cites"] == [1, 2]
    assert saved["questions"][0]["detail"][1]["heading"] == "目前辦理情形"
    assert saved["questions"][1]["short"][0]["paragraphs"][0]["cites"] == [3]


@pytest.mark.asyncio
async def test_a_rejected_output_is_retried_with_feedback(tmp_path: Path, setup):
    _, session_id = setup
    bad = _good()
    bad["questions"][0]["short"][0]["paragraphs"][0]["cites"] = ["q9-e9"]
    runner = WritingRunner([bad, _good()])
    _, result = await _run(tmp_path, session_id, runner)
    assert result.status is WorkflowStatus.COMPLETED, result.error
    assert [request.attempt for request in runner.requests] == [1, 2]
    assert "q9-e9" in runner.requests[1].prompt


@pytest.mark.asyncio
async def test_persistently_bad_output_fails_without_publishing(tmp_path: Path, setup):
    engine, session_id = setup
    bad = _good()
    bad["questions"].pop()
    runner = WritingRunner([bad, bad, bad, bad, bad])
    _, result = await _run(tmp_path, session_id, runner)
    assert result.status is WorkflowStatus.FAILED
    with Session(engine) as session:
        assert session.get(ChatQaWorkspace, session_id).versions == []
