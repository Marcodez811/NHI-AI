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
    assert done.checked is True and [i.model_dump() for i in done.issues] == [issue]
    assert done.review == {"rounds": 3, "found": [4, 2, 1]}
