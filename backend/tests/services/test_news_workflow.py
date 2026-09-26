from pathlib import Path
from uuid import uuid4

import pytest
from openai_codex import Sandbox
from sqlmodel import SQLModel, create_engine

import app.db as database

from app.models.news import NewsTaskPayload
from app.services.agentic.contracts import AgentExecutionResult, AgentTaskPayload, WorkflowStatus
from app.services.agentic.registry import WorkflowRegistry
from app.services.agentic.service import execute_workflow
from app.services.news.adapter import NewsWorkflowAdapter


class StubNewsAdapter(NewsWorkflowAdapter):
    def prepare_input(self, value, workspace):
        (workspace / "work" / "sources.json").write_text('{"names":["source.txt"],"paths":[]}', encoding="utf-8")

    def post_extraction(self, value, workspace):
        (workspace / "work" / "evidence.json").write_text('{"blocks":[]}', encoding="utf-8")


class Runner:
    def __init__(self):
        self.requests = []

    async def run(self, request, *, progress_callback=None):
        self.requests.append(request)
        response = "Extracted." if request.node_id == "extraction" else "# 健保擴大服務\n\n" + "健保署說明，將依來源文件所列範圍提供相關服務。" * 5
        return AgentExecutionResult(provider_run_id=request.node_id, response=response)


@pytest.mark.asyncio
async def test_news_runs_extraction_then_one_isolated_draft(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.sqlite'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    job_id = uuid4()
    value = NewsTaskPayload(job_id=job_id, document_ids=[uuid4()], guidance="")
    runner = Runner()
    result = await execute_workflow(
        AgentTaskPayload(job_id=job_id, workflow="news", input=value.model_dump(mode="json")),
        registry=WorkflowRegistry({"news": StubNewsAdapter()}),
        runner=runner,
        workspace_root=tmp_path,
        timeout_seconds=5,
    )
    assert result.status is WorkflowStatus.COMPLETED
    assert result.output["article"].startswith("# 健保擴大服務")
    assert [request.node_id for request in runner.requests] == ["extraction", "author"]
    assert runner.requests[0].skill_names == ("source-document-extraction",)
    assert runner.requests[1].skill_names == ("nhi-news-writing",)
    assert runner.requests[1].sandbox is Sandbox.workspace_write
    assert runner.requests[1].restrict_workspace is True
    assert runner.requests[1].read_only_paths == (tmp_path / str(job_id) / "work" / "evidence.json",)
