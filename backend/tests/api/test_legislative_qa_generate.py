from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError

from app.api.routes.chat import create_chat_session
from app.api.routes.legislative_qa import generate_qa_document, get_qa_generation
from app.models.chat import ChatQaWorkspace, ChatSessionCreate
from app.models.legislative_qa import GenerateQaRequest
from app.services.chat.repository import InMemoryChatRepository


class Backend:
    def __init__(self):
        self.progress, self.result = {}, None

    async def set_progress(self, key, progress):
        self.progress[key] = progress

    async def get_progress(self, key):
        return self.progress.get(key)

    async def get_result(self, key):
        if self.result is None:
            raise ResultIsMissingError
        return self.result


class Task:
    def __init__(self):
        self.payload = None

    def kicker(self):
        return self

    def with_task_id(self, value):
        return self

    async def kiq(self, payload):
        self.payload = payload


async def _ready(repository) -> UUID:
    session_id = (await create_chat_session(ChatSessionCreate(), repository)).id
    await repository.save_qa_workspace(ChatQaWorkspace(
        session_id=session_id, questions=[{"no": 1, "text": "甲"}], questions_confirmed=True,
        documents={"confirmed": True, "used": []},
        outline={"1": {"short": ["a"], "detail": [{"title": "背景說明", "points": []}], "confirmed": True}},
    ))
    return session_id


@pytest.mark.asyncio
async def test_generate_starts_a_job_and_marks_the_workspace_generating():
    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id = await _ready(repository)
    started = await generate_qa_document(session_id, GenerateQaRequest(session_label="11-5"), repository, backend, task)
    assert started.status.value == "queued"
    assert task.payload.workflow == "legislative_qa"
    assert task.payload.input["session_id"] == str(session_id) and task.payload.input["session_label"] == "11-5"
    stored = await repository.get_qa_workspace(session_id)
    assert stored.generation["job_id"] == str(started.job_id) and stored.stage == "generating"


@pytest.mark.asyncio
async def test_generate_refuses_unconfirmed_outlines_and_a_running_job():
    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id = await _ready(repository)
    stored = await repository.get_qa_workspace(session_id)
    stored.outline["1"]["confirmed"] = False
    with pytest.raises(HTTPException) as error:
        await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)
    assert error.value.status_code == 409 and error.value.detail["code"] == "qa_outline_unconfirmed"

    stored.outline["1"]["confirmed"] = True
    await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)
    with pytest.raises(HTTPException) as running:
        await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)
    assert running.value.status_code == 409 and running.value.detail["code"] == "qa_generation_running"


@pytest.mark.asyncio
async def test_status_reports_progress_and_releases_the_workspace_when_the_job_fails():
    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id = await _ready(repository)
    job_id = (await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)).job_id
    backend.progress[str(job_id)] = TaskProgress(state="running", meta={"status": "running", "phase": "drafting", "message": "撰寫中"})
    live = await get_qa_generation(session_id, job_id, repository, backend)
    assert live.phase.value == "drafting" and live.message == "撰寫中"
    assert (await repository.get_qa_workspace(session_id)).generation

    backend.result = SimpleNamespace(return_value={"workflow": "legislative_qa", "status": "failed", "error": "AI 失敗"})
    failed = await get_qa_generation(session_id, job_id, repository, backend)
    assert failed.status.value == "failed" and failed.error == "AI 失敗"
    assert (await repository.get_qa_workspace(session_id)).generation == {}
    # A new run may start once the failed one is released.
    await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)


@pytest.mark.asyncio
async def test_status_of_a_completed_job_links_the_download():
    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id = await _ready(repository)
    job_id = (await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)).job_id
    artifact_id = uuid4()
    backend.result = SimpleNamespace(return_value={
        "workflow": "legislative_qa", "status": "completed", "output": {"job_id": str(job_id), "artifact_id": str(artifact_id)},
    })
    done = await get_qa_generation(session_id, job_id, repository, backend)
    assert done.artifact_id == artifact_id and done.download_url == f"/api/v1/artifacts/{artifact_id}/download"


@pytest.mark.asyncio
async def test_status_of_a_completed_job_carries_the_open_issues():
    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id = await _ready(repository)
    job_id = (await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)).job_id
    issue = {"question_no": 2, "part": "detail", "kind": "unsupported", "message": "沒有依據"}
    backend.result = SimpleNamespace(return_value={
        "workflow": "legislative_qa", "status": "completed",
        "output": {"job_id": str(job_id), "artifact_id": str(uuid4()), "checked": True, "issues": [issue], "review": {"rounds": 3, "found": [4, 2, 1]}},
    })
    done = await get_qa_generation(session_id, job_id, repository, backend)
    assert done.checked is True and [i.model_dump(exclude_none=True) for i in done.issues] == [issue]
    assert done.review == {"rounds": 3, "found": [4, 2, 1]}


# ── structured versions and scoped revisions ────────────────────────────────

from app.api.routes.legislative_qa import get_qa_version, revise_qa_document
from app.models.artifacts import Artifact
from app.models.legislative_qa import ReviseQaRequest
from app.services.artifacts import ArtifactStorage, InMemoryArtifactRepository
from app.services.legislative_qa.document import Paragraph, QaDocQuestion, QaDocument, QaIssue, Section
from app.services.legislative_qa.publish import publish_document, record_version


def _document() -> QaDocument:
    return QaDocument(
        kind="ai", title="t", session_label="11-5", checked=True, sources=["甲"],
        questions=[QaDocQuestion(
            no=1, text="甲", short=[Section(heading="摘要", paragraphs=[Paragraph(text="短答", cites=[1])])],
            detail=[Section(heading="背景說明", paragraphs=[Paragraph(text="詳答", cites=[1])])],
        )],
        issues=[QaIssue(question_no=1, part="short", kind="style", message="m", section=0, paragraph=0)],
    )


async def _with_version(repository, tmp_path, kind="ai", session_id=None):
    session_id = session_id or await _ready(repository)
    artifacts, storage = InMemoryArtifactRepository(), ArtifactStorage(tmp_path)
    artifact = await publish_document(_document(), session_id=session_id, repository=artifacts, storage=storage)
    workspace = await repository.get_qa_workspace(session_id)
    record_version(workspace, artifact, kind=kind, document=_document())
    await repository.save_qa_workspace(workspace)
    return session_id, artifact, artifacts, storage


@pytest.mark.asyncio
async def test_a_version_returns_its_structured_content_with_located_issues(tmp_path):
    repository = InMemoryChatRepository()
    session_id, artifact, artifacts, storage = await _with_version(repository, tmp_path)
    content = await get_qa_version(session_id, artifact.id, repository, artifacts, storage)
    assert content.kind == "ai" and content.download_url == f"/api/v1/artifacts/{artifact.id}/download"
    assert content.document.questions[0].short[0].paragraphs[0].text == "短答"
    assert (content.document.issues[0].section, content.document.issues[0].paragraph) == (0, 0)


@pytest.mark.asyncio
async def test_unknown_or_foreign_versions_are_404(tmp_path):
    repository = InMemoryChatRepository()
    session_id, artifact, artifacts, storage = await _with_version(repository, tmp_path)
    other = await _ready(repository)
    for sid, aid in ((session_id, uuid4()), (other, artifact.id)):  # unknown id; an id of another session
        with pytest.raises(HTTPException) as error:
            await get_qa_version(sid, aid, repository, artifacts, storage)
        assert error.value.status_code == 404
    (tmp_path / "artifacts" / f"{artifact.id}.json").unlink()
    with pytest.raises(HTTPException) as error:
        await get_qa_version(session_id, artifact.id, repository, artifacts, storage)
    assert error.value.status_code == 404


def _revise_request(**values) -> ReviseQaRequest:
    return ReviseQaRequest(**{"question_no": 1, "part": "short", "instruction": "肯定一點", **values})


@pytest.mark.asyncio
async def test_revise_queues_a_job_on_the_latest_ai_version_and_refuses_a_second(tmp_path):
    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id, artifact, artifacts, storage = await _with_version(repository, tmp_path)
    started = await revise_qa_document(session_id, _revise_request(), repository, backend, task, artifacts, storage)
    assert started.status.value == "queued"
    revision = task.payload.input["revision"]
    assert revision == {
        "base_artifact_id": str(artifact.id), "question_no": 1, "part": "short", "section": None, "instruction": "肯定一點",
    }
    assert task.payload.input["session_label"] == "11-5"
    stored = await repository.get_qa_workspace(session_id)
    assert stored.stage == "generating" and stored.generation["revision"]["part"] == "short"
    with pytest.raises(HTTPException) as running:
        await revise_qa_document(session_id, _revise_request(), repository, backend, task, artifacts, storage)
    assert running.value.status_code == 409 and running.value.detail["code"] == "qa_generation_running"
    with pytest.raises(HTTPException) as generating:
        await generate_qa_document(session_id, GenerateQaRequest(), repository, backend, task)
    assert generating.value.status_code == 409


@pytest.mark.asyncio
async def test_revise_validates_the_base_and_the_scope(tmp_path):
    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id, artifact, artifacts, storage = await _with_version(repository, tmp_path)
    cases = [
        (_revise_request(question_no=7), 422),
        (_revise_request(part="detail", section=5), 422),
        (_revise_request(part="both", section=0), 422),
        (_revise_request(base_artifact_id=uuid4()), 404),
    ]
    for request, code in cases:
        with pytest.raises(HTTPException) as error:
            await revise_qa_document(session_id, request, repository, backend, task, artifacts, storage)
        assert error.value.status_code == code
    assert task.payload is None
    # an outline-wording version is not revisable, and no AI version at all is a 404
    outline_session, outline, artifacts2, storage2 = await _with_version(InMemoryChatRepository(), tmp_path, kind="outline")
    fresh = InMemoryChatRepository()
    outline_session, outline, artifacts2, storage2 = await _with_version(fresh, tmp_path, kind="outline")
    with pytest.raises(HTTPException) as error:
        await revise_qa_document(outline_session, _revise_request(), fresh, backend, task, artifacts2, storage2)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await revise_qa_document(
            outline_session, _revise_request(base_artifact_id=outline.id), fresh, backend, task, artifacts2, storage2)
    assert error.value.status_code == 422


def test_revision_instruction_is_bounded():
    from pydantic import ValidationError

    for bad in ("", "字" * 501):
        with pytest.raises(ValidationError):
            _revise_request(instruction=bad)


@pytest.mark.asyncio
async def test_the_chat_tool_hook_starts_the_same_revision(tmp_path):
    from app.api.routes.chat import _qa_reviser

    repository, backend, task = InMemoryChatRepository(), Backend(), Task()
    session_id, artifact, artifacts, storage = await _with_version(repository, tmp_path)
    assert _qa_reviser(session_id, repository, None, None, None, None) is None
    revise = _qa_reviser(session_id, repository, backend, task, artifacts, storage)
    job = await revise(1, "detail", 0, "精簡")
    assert job.status.value == "queued" and task.payload.input["revision"]["section"] == 0
    with pytest.raises(ValueError) as running:
        await revise(1, "short", None, "x")
    assert running.value.code == "qa_generation_running"
