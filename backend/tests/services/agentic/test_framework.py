from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agentic import (
    AgentPhase,
    AgentTaskPayload,
    BaseWorkflowAdapter,
    DeterministicValidationError,
    SkillStagingError,
    TurnRequest,
    UnknownWorkflowError,
    WorkflowRegistry,
    WorkflowStatus,
    stage_declared_skills,
)
from app.services.agentic.runner import CodexRunResult, CodexRunner, WorkflowTimeoutError, safe_error
from app.services.agentic.service import execute_workflow


class EchoAdapter(BaseWorkflowAdapter[dict, str]):
    name = "echo"

    def build_prompt(self, value, workspace, **kwargs):
        return "echo"

    def publish(self, value, result, workspace):
        return result.response or ""


class FakeTurn:
    id = "turn-1"

    async def stream(self):
        yield SimpleNamespace(method="turn/started", payload=SimpleNamespace(turn_id=self.id))
        item = SimpleNamespace(root=SimpleNamespace(type="agentMessage", text="done", phase="final_answer"))
        yield SimpleNamespace(method="item/completed", payload=SimpleNamespace(turn_id=self.id, item=item))
        turn = SimpleNamespace(id=self.id, status="completed", duration_ms=7)
        yield SimpleNamespace(method="turn/completed", payload=SimpleNamespace(turn=turn))


class FakeThread:
    id = "thread-1"

    async def turn(self, inputs):
        return FakeTurn()


class FakeCodex:
    def __init__(self, config):
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def login_api_key(self, key):
        self.key = key

    async def thread_start(self, **kwargs):
        return FakeThread()


def test_registry_rejects_unknown_workflow_and_payload_extras():
    registry = WorkflowRegistry({"echo": EchoAdapter()})
    with pytest.raises(UnknownWorkflowError):
        registry.resolve("missing")
    with pytest.raises(ValueError):
        AgentTaskPayload.model_validate({"job_id": "j", "workflow": "echo", "input": {}, "module": "os"})


def test_slide_adapter_can_be_imported_directly_without_circular_import():
    from app.services.slides.adapter import SlidesWorkflowAdapter

    assert SlidesWorkflowAdapter.name == "slides"


def test_safe_error_hides_secrets_and_paths():
    assert safe_error("token=secret") == "Workflow execution failed."
    assert safe_error("/tmp/private/output.pptx") == "Workflow execution failed."


def test_skill_staging_rejects_unknown_and_path_names(tmp_path):
    root = tmp_path / "skills"
    (root / "known").mkdir(parents=True)
    (root / "known" / "SKILL.md").write_text("# known")
    workspace = tmp_path / "workspace"
    assert stage_declared_skills(workspace, ["known"], skills_root=root) == ["known"]
    assert (workspace / ".agents/skills/known/SKILL.md").exists()
    with pytest.raises(SkillStagingError):
        stage_declared_skills(workspace, ["../known"], skills_root=root)
    with pytest.raises(SkillStagingError):
        stage_declared_skills(workspace, ["missing"], skills_root=root)


@pytest.mark.asyncio
async def test_runner_collects_turn_audit_and_sanitizes_progress(tmp_path):
    progress = []
    runner = CodexRunner(codex_factory=FakeCodex, api_key="key", timeout_seconds=2, heartbeat_seconds=0)
    result = await runner.run(tmp_path, [TurnRequest(kind="initial", prompt="go")], progress_callback=progress.append)
    assert result.thread_id == "thread-1"
    assert result.response == "done"
    assert result.audits[0].model_dump()["turn_id"] == "turn-1"
    assert result.audits[0].kind == "initial"
    assert AgentPhase.DRAFTING.value in [event["phase"] for event in progress]
    assert all("key" not in event["message"] for event in progress)


@pytest.mark.asyncio
async def test_runner_enforces_total_timeout(tmp_path):
    class HangingTurn(FakeTurn):
        async def stream(self):
            await asyncio.sleep(1)
            yield SimpleNamespace(method="turn/completed", payload=None)

    class HangingThread(FakeThread):
        async def turn(self, inputs):
            return HangingTurn()

    class HangingCodex(FakeCodex):
        async def thread_start(self, **kwargs):
            return HangingThread()

    runner = CodexRunner(codex_factory=HangingCodex, api_key="key", timeout_seconds=0.01, heartbeat_seconds=0)
    with pytest.raises(WorkflowTimeoutError):
        await runner.run(tmp_path, [TurnRequest(kind="initial", prompt="go")])


@pytest.mark.asyncio
async def test_runner_emits_heartbeat_while_turn_is_running(tmp_path):
    class HangingTurn(FakeTurn):
        async def stream(self):
            await asyncio.sleep(1)
            yield SimpleNamespace(method="turn/completed", payload=None)

    class HangingThread(FakeThread):
        async def turn(self, inputs):
            return HangingTurn()

    class HangingCodex(FakeCodex):
        async def thread_start(self, **kwargs):
            return HangingThread()

    progress = []
    runner = CodexRunner(codex_factory=HangingCodex, api_key="key", timeout_seconds=0.04, heartbeat_seconds=0.005)
    with pytest.raises(WorkflowTimeoutError):
        await runner.run(tmp_path, [TurnRequest(kind="initial", prompt="go")], progress_callback=progress.append)
    assert any(event["heartbeat"] for event in progress)


@pytest.mark.asyncio
async def test_execute_workflow_returns_safe_failure_for_unknown_workflow(tmp_path):
    result = await execute_workflow(AgentTaskPayload(job_id="job", workflow="missing", input={}), registry=WorkflowRegistry(), workspace_root=tmp_path)
    assert result.status == WorkflowStatus.FAILED
    assert result.error == "Workflow execution failed."


@pytest.mark.asyncio
async def test_execute_workflow_typed_publication(tmp_path):
    registry = WorkflowRegistry({"echo": EchoAdapter()})
    runner = CodexRunner(codex_factory=FakeCodex, api_key="key", timeout_seconds=2, heartbeat_seconds=0)
    result = await execute_workflow(AgentTaskPayload(job_id="job", workflow="echo", input={}), registry=registry, runner=runner, workspace_root=tmp_path)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.output == "done"


class LoopAdapter(BaseWorkflowAdapter[dict, str]):
    name = "loop"
    max_review_rounds = 3

    def __init__(self, *, validation_failures=0, blocking_reviews=0):
        self.validation_failures = validation_failures
        self.blocking_reviews = blocking_reviews
        self.validations = 0
        self.published = False

    def build_prompt(self, value, workspace, **kwargs):
        return "generate"

    def build_review_prompt(self, value, workspace, audit, context):
        return "review"

    async def validate_generated(self, value, workspace):
        self.validations += 1
        if self.validations <= self.validation_failures:
            raise DeterministicValidationError("deterministic validator finding")

    def parse_review(self, response, workspace):
        if self.blocking_reviews:
            self.blocking_reviews -= 1
            return {"summary": "needs work", "blocking_findings": [{"detail": "fix"}], "findings": []}
        return {"summary": "clean", "blocking_findings": [], "findings": []}

    def publish(self, value, result, workspace):
        self.published = True
        return "published"


class ScriptedRunner:
    timeout_seconds = 10

    def __init__(self):
        self.turns = []

    async def run(self, workspace, turns, **kwargs):
        callback = kwargs["turn_callback"]
        pending = list(turns)
        audits = []
        while pending:
            request = pending.pop(0)
            self.turns.append(request)
            audit = SimpleNamespace(kind=request.kind, turn_id=str(len(audits) + 1), status="completed")
            audits.append(audit)
            follow_up = await callback(audit, "review response")
            if follow_up is not None:
                pending.append(follow_up)
        return CodexRunResult(thread_id="thread-loop", audits=[], response="review response")


@pytest.mark.asyncio
async def test_execute_workflow_deterministic_failure_corrects_before_review(tmp_path):
    adapter = LoopAdapter(validation_failures=1)
    runner = ScriptedRunner()
    progress = []
    result = await execute_workflow(
        AgentTaskPayload(job_id="loop-job", workflow="loop", input={}),
        registry=WorkflowRegistry({"loop": adapter}),
        runner=runner,
        workspace_root=tmp_path,
        progress_callback=progress.append,
    )
    assert result.status == WorkflowStatus.COMPLETED
    assert result.phase is AgentPhase.COMPLETED
    assert [turn.kind for turn in runner.turns] == ["initial", "correction", "review"]
    phases = [event["phase"] for event in progress]
    assert phases.index(AgentPhase.DRAFTING.value) < phases.index(AgentPhase.VALIDATING.value)
    assert AgentPhase.REVISING.value in phases
    assert AgentPhase.PUBLISHING.value in phases
    assert adapter.published


@pytest.mark.asyncio
async def test_execute_workflow_semantic_revision_is_bounded_and_publishes_after_pass(tmp_path):
    adapter = LoopAdapter(blocking_reviews=1)
    runner = ScriptedRunner()
    progress = []
    result = await execute_workflow(
        AgentTaskPayload(job_id="semantic-job", workflow="loop", input={}),
        registry=WorkflowRegistry({"loop": adapter}),
        runner=runner,
        workspace_root=tmp_path,
        progress_callback=progress.append,
    )
    assert result.status == WorkflowStatus.COMPLETED
    assert result.phase is AgentPhase.COMPLETED
    assert [turn.kind for turn in runner.turns] == ["initial", "review", "correction", "review"]
    phases = [event["phase"] for event in progress]
    assert phases.index(AgentPhase.REVIEWING.value) < phases.index(AgentPhase.REVISING.value)
    assert phases.index(AgentPhase.REVISING.value) < phases.index(AgentPhase.PUBLISHING.value)
    assert adapter.published


@pytest.mark.asyncio
async def test_execute_workflow_round_exhaustion_does_not_publish(tmp_path):
    adapter = LoopAdapter(blocking_reviews=10)
    adapter.max_review_rounds = 2
    runner = ScriptedRunner()
    result = await execute_workflow(
        AgentTaskPayload(job_id="exhaust-job", workflow="loop", input={}),
        registry=WorkflowRegistry({"loop": adapter}),
        runner=runner,
        workspace_root=tmp_path,
    )
    assert result.status == WorkflowStatus.FAILED
    assert result.phase is AgentPhase.FAILED
    assert not adapter.published
    assert [turn.kind for turn in runner.turns] == ["initial", "review", "correction", "review"]


@pytest.mark.asyncio
async def test_unexpected_validator_exception_fails_closed(tmp_path):
    adapter = LoopAdapter()

    async def crash(value, workspace):
        raise RuntimeError("validator crashed")

    adapter.validate_generated = crash
    runner = ScriptedRunner()
    result = await execute_workflow(
        AgentTaskPayload(job_id="validator-job", workflow="loop", input={}),
        registry=WorkflowRegistry({"loop": adapter}),
        runner=runner,
        workspace_root=tmp_path,
    )
    assert result.status == WorkflowStatus.FAILED
    assert result.phase is AgentPhase.FAILED
    assert not adapter.published
    assert [turn.kind for turn in runner.turns] == ["initial"]
