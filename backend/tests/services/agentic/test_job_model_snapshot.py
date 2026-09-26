"""A model-setting edit must never move an already-started job to another model."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.db as database
from app.models.agent_settings import AgentStageSettings
from app.services.agentic.contracts import (
    AgentExecutionResult, AgentReasoningEffort, AgentTaskPayload,
    BaseWorkflowAdapter, WorkflowStatus,
)
from app.services.agentic.job_model_context import stage_settings
from app.services.agentic.model_settings import load_snapshot
from app.services.agentic.registry import WorkflowRegistry
from app.services.agentic.service import execute_workflow


class SnapshotAdapter(BaseWorkflowAdapter[dict, str]):
    name = "snapshot-test"
    uses_model_settings_snapshot = True
    model_setting_stages = ("extraction", "author")
    declared_skills = ("extractor",)
    extraction_skills = ("extractor",)

    @property
    def extraction_model(self) -> str:
        return stage_settings("extraction").model

    @property
    def extraction_reasoning_effort(self) -> AgentReasoningEffort:
        return stage_settings("extraction").reasoning_effort

    @property
    def author_model(self) -> str:
        return stage_settings("author").model

    @property
    def author_reasoning_effort(self) -> AgentReasoningEffort:
        return stage_settings("author").reasoning_effort

    def prepare_workspace(self, value: dict, workspace: Path) -> None:
        (workspace / "work").mkdir(parents=True, exist_ok=True)

    def post_extraction(self, value: dict, workspace: Path) -> None:
        pass

    def build_prompt(self, value: dict, workspace: Path, **kwargs: object) -> str:
        return "Write the article"

    def publish(self, value: dict, result: AgentExecutionResult, workspace: Path) -> str:
        return result.response


class EditingRunner:
    timeout_seconds = 5

    def __init__(self, engine) -> None:
        self.engine = engine
        self.requests = []

    async def run(self, request, *, progress_callback=None) -> AgentExecutionResult:
        self.requests.append(request)
        if request.node_id == "extraction":
            with Session(self.engine) as session:
                row = session.get(AgentStageSettings, ("snapshot-test", "author"))
                row.model = "new-author-model"
                session.add(row)
                session.commit()
        return AgentExecutionResult(provider_run_id=request.node_id, response="written")


@pytest.mark.asyncio
async def test_snapshot_is_written_before_extraction_and_held_through_author(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    with Session(engine) as session:
        session.add(AgentStageSettings(workflow="snapshot-test", stage="extraction", model="original-extraction-model"))
        session.add(AgentStageSettings(workflow="snapshot-test", stage="author", model="original-author-model"))
        session.commit()

    skills = tmp_path / "skills" / "extractor"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# Extractor", encoding="utf-8")
    runner = EditingRunner(engine)
    adapter = SnapshotAdapter()
    result = await execute_workflow(
        AgentTaskPayload(job_id="first", workflow=adapter.name, input={}),
        registry=WorkflowRegistry({adapter.name: adapter}),
        runner=runner,
        workspace_root=tmp_path / "jobs",
        skills_root=tmp_path / "skills",
    )

    assert result.status is WorkflowStatus.COMPLETED
    assert [(request.node_id, request.model) for request in runner.requests] == [
        ("extraction", "original-extraction-model"),
        ("author", "original-author-model"),
    ]
    first_workspace = tmp_path / "jobs" / "first"
    assert load_snapshot(first_workspace, adapter.model_setting_stages)["author"].model == "original-author-model"
    assert (first_workspace / "work" / "model_settings.json").is_file()
    assert "api_key" not in (first_workspace / "work" / "model_settings.json").read_text(encoding="utf-8")
    assert stage_settings("author") is None

    # Even a retry for this job uses its first selection; a newly created job
    # resolves the changed database row instead.
    with Session(engine) as session:
        row = session.get(AgentStageSettings, ("snapshot-test", "author"))
        row.model = "later-model"
        session.commit()
    from app.services.agentic.service import _job_model_snapshot

    assert (
        _job_model_snapshot(first_workspace, adapter.name, adapter.model_setting_stages)["author"].model
        == "original-author-model"
    )
    second_workspace = tmp_path / "jobs" / "second"
    second_workspace.mkdir()
    assert (
        _job_model_snapshot(second_workspace, adapter.name, adapter.model_setting_stages)["author"].model
        == "later-model"
    )
