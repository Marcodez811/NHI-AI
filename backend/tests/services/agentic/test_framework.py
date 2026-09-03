from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai_codex import Sandbox

from app.services.agentic import (
    AgentExecutionRequest,
    AgentExecutionResult,
    CodexAgentRunner,
    AgentPhase,
    AgentReasoningEffort,
    AgentTaskPayload,
    BaseWorkflowAdapter,
    DeterministicValidationError,
    SkillStagingError,
    TurnRequest,
    UnknownWorkflowError,
    WorkflowRegistry,
    WorkflowStatus,
    RunnerRegistry,
    UnknownRunnerError,
    stage_declared_skills,
)
from app.services.agentic.runner import CodexRunResult, CodexRunner, WorkflowExecutionError, WorkflowTimeoutError, safe_error
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
    last_turn_kwargs = None

    async def turn(self, inputs, **kwargs):
        FakeThread.last_turn_kwargs = kwargs
        return FakeTurn()


class FakeCodex:
    last_thread_kwargs = None

    def __init__(self, config):
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def login_api_key(self, key):
        self.key = key

    async def thread_start(self, **kwargs):
        FakeCodex.last_thread_kwargs = kwargs
        return FakeThread()


def test_registry_rejects_unknown_workflow_and_payload_extras():
    registry = WorkflowRegistry({"echo": EchoAdapter()})
    with pytest.raises(UnknownWorkflowError):
        registry.resolve("missing")
    with pytest.raises(ValueError):
        AgentTaskPayload.model_validate({"job_id": "j", "workflow": "echo", "input": {}, "module": "os"})


def test_execution_request_validates_reasoning_effort():
    with pytest.raises(ValueError):
        AgentExecutionRequest(
            run_id="run-1",
            node_id="author",
            role="author",
            workspace="/tmp/workspace",
            prompt="draft",
            reasoning_effort="unsupported",
        )


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
    assert result.audits[0].prompt == "go"
    assert AgentPhase.DRAFTING.value in [event["phase"] for event in progress]
    assert any(event["event_type"] == "node_progress" for event in progress)
    assert all("key" not in event["message"] for event in progress)


@pytest.mark.asyncio
async def test_runner_uses_per_activation_model_override_and_preserves_default(tmp_path):
    runner = CodexRunner(
        codex_factory=FakeCodex,
        model="default-model",
        api_key="key",
        timeout_seconds=2,
        heartbeat_seconds=0,
    )

    await runner.run(tmp_path, [TurnRequest(kind="initial", prompt="go")])
    assert FakeCodex.last_thread_kwargs["model"] == "default-model"

    runner.reasoning_effort = AgentReasoningEffort.HIGH
    await runner.run(tmp_path, [TurnRequest(kind="initial", prompt="go")])
    assert FakeCodex.last_thread_kwargs["config"] == {"model_reasoning_effort": "high"}

    await runner.run(tmp_path, [TurnRequest(kind="reviewer", prompt="review")], model="reviewer-model")
    assert FakeCodex.last_thread_kwargs["model"] == "reviewer-model"

    await runner.run(
        tmp_path,
        [TurnRequest(kind="reviewer", prompt="review")],
        model="reviewer-model",
        reasoning_effort=AgentReasoningEffort.XHIGH,
    )
    assert FakeCodex.last_thread_kwargs["config"] == {"model_reasoning_effort": "xhigh"}


@pytest.mark.asyncio
async def test_runner_preserves_failed_turn_error_in_exception_and_audit(tmp_path):
    class FailedTurn(FakeTurn):
        async def stream(self):
            yield SimpleNamespace(
                method="turn/completed",
                payload=SimpleNamespace(
                    turn=SimpleNamespace(
                        id=self.id,
                        status="failed",
                        error=SimpleNamespace(message="review output schema was rejected"),
                        duration_ms=12,
                    ),
                ),
            )

    class FailedThread(FakeThread):
        async def turn(self, inputs, **kwargs):
            return FailedTurn()

    class FailedCodex(FakeCodex):
        async def thread_start(self, **kwargs):
            return FailedThread()

    audit_path = tmp_path / "audit.json"
    runner = CodexRunner(codex_factory=FailedCodex, api_key="key", timeout_seconds=2, heartbeat_seconds=0)
    with pytest.raises(WorkflowExecutionError, match="review output schema was rejected"):
        await runner.run(tmp_path, [TurnRequest(kind="reviewer", prompt="review")], audit_path=audit_path)

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["turns"][0]["error"] == "review output schema was rejected"


@pytest.mark.asyncio
async def test_runner_forwards_reviewer_schema_and_reports_review_phase(tmp_path):
    schema = {"type": "object", "additionalProperties": False}
    progress = []
    runner = CodexRunner(codex_factory=FakeCodex, api_key="key", timeout_seconds=2, heartbeat_seconds=0)
    await runner.run(
        tmp_path,
        [TurnRequest(kind="reviewer", prompt="review", output_schema=schema)],
        progress_callback=progress.append,
    )

    assert FakeThread.last_turn_kwargs["output_schema"] == schema
    assert any(event["phase"] == AgentPhase.REVIEWING.value for event in progress)


@pytest.mark.asyncio
async def test_provider_progress_is_enriched_with_logical_node_context(tmp_path):
    request = AgentExecutionRequest(
        run_id="progress-job",
        node_id="reviewer",
        role="presentation_reviewer",
        attempt=2,
        model="reviewer-model",
        workspace=tmp_path,
        prompt="review",
    )
    progress = []
    runner = CodexAgentRunner(
        CodexRunner(codex_factory=FakeCodex, api_key="key", timeout_seconds=2, heartbeat_seconds=0),
    )
    await runner.run(request, progress_callback=progress.append)

    provider_events = [event for event in progress if event["event_type"] == "node_progress"]
    assert provider_events
    assert all(event["node_id"] == "reviewer" for event in provider_events)
    assert all(event["role"] == "presentation_reviewer" for event in provider_events)
    assert all(event["model"] == "reviewer-model" for event in provider_events)
    assert all(event["attempt"] == 2 for event in provider_events)


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


@pytest.mark.asyncio
async def test_execute_workflow_outer_timeout_preserves_lifecycle_start_time(tmp_path):
    class HangingRunner:
        def __init__(self):
            self.started_at = None

        async def run(self, workspace, turns, **kwargs):
            self.started_at = datetime.now(timezone.utc)
            await asyncio.sleep(1)

    runner = HangingRunner()
    result = await execute_workflow(
        AgentTaskPayload(job_id="timeout-job", workflow="echo", input={}),
        registry=WorkflowRegistry({"echo": EchoAdapter()}),
        runner=runner,
        workspace_root=tmp_path,
        timeout_seconds=0.05,
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error == "Workflow timed out."
    assert runner.started_at is not None
    assert result.started_at <= runner.started_at < result.finished_at
    assert (result.finished_at - result.started_at).total_seconds() >= 0.02


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


class ModernReviewAdapter(BaseWorkflowAdapter[dict, str]):
    name = "modern-review"
    max_review_rounds = 2
    author_runner = "fake"
    reviewer_runner = "fake"
    author_model = "author-model"
    reviewer_model = "reviewer-model"
    author_reasoning_effort = AgentReasoningEffort.HIGH
    reviewer_reasoning_effort = AgentReasoningEffort.XHIGH

    def build_prompt(self, value, workspace, **kwargs):
        return f"author-{kwargs.get('revision_feedback') or 'initial'}"

    def build_review_prompt(self, value, workspace, audit, context):
        return "review"

    def parse_review(self, response, workspace):
        return {"blocking_findings": response == "block"}

    def revision_feedback(self, value, review, result):
        return "correct the blocking finding"

    def publish(self, value, result, workspace):
        return result.response


class MissingArtifactAdapter(ModernReviewAdapter):
    name = "missing-artifact"

    def post_author_completion_check(self, value, result, workspace):
        raise RuntimeError("presentation artifact was not generated")


class ModernRunner:
    name = "fake"
    timeout_seconds = 10

    def __init__(self):
        self.requests = []

    async def run(self, request, *, progress_callback=None):
        self.requests.append(request)
        response = "block" if request.node_id == "reviewer" and request.attempt == 1 else f"author-{request.attempt}"
        return AgentExecutionResult(provider_run_id=f"provider-{len(self.requests)}", response=response)


class AlwaysBlockingRunner(ModernRunner):
    async def run(self, request, *, progress_callback=None):
        self.requests.append(request)
        response = "block" if request.node_id == "reviewer" else f"author-{request.attempt}"
        return AgentExecutionResult(provider_run_id=f"provider-{len(self.requests)}", response=response)


class SplitReviewAdapter(ModernReviewAdapter):
    name = "split-review"
    author_runner = "author-runner"
    reviewer_runner = "reviewer-runner"


class AuthorRunner(ModernRunner):
    name = "author-runner"


class ReviewerRunner(ModernRunner):
    name = "reviewer-runner"


def test_runner_registry_is_an_explicit_allowlist():
    registry = RunnerRegistry({"fake": ModernRunner()})
    assert registry.resolve("fake").name == "fake"
    with pytest.raises(UnknownRunnerError):
        registry.resolve("missing")
    with pytest.raises(ValueError):
        registry.register("fake", ModernRunner())


@pytest.mark.asyncio
async def test_modern_coordinator_resolves_author_and_reviewer_from_runner_registry(tmp_path):
    adapter = SplitReviewAdapter()
    author_runner = AuthorRunner()
    reviewer_runner = ReviewerRunner()
    registry = RunnerRegistry({
        "author-runner": author_runner,
        "reviewer-runner": reviewer_runner,
    })

    result = await execute_workflow(
        AgentTaskPayload(job_id="split-job", workflow="split-review", input={}),
        registry=WorkflowRegistry({"split-review": adapter}),
        runner=registry,
        workspace_root=tmp_path,
    )

    assert result.status is WorkflowStatus.COMPLETED
    assert [(item.node_id, item.model) for item in author_runner.requests] == [
        ("author", "author-model"),
        ("author", "author-model"),
    ]
    assert [(item.node_id, item.model) for item in reviewer_runner.requests] == [
        ("reviewer", "reviewer-model"),
        ("reviewer", "reviewer-model"),
    ]


@pytest.mark.asyncio
async def test_modern_coordinator_uses_independent_author_and_reviewer_sessions(tmp_path):
    adapter = ModernReviewAdapter()
    runner = ModernRunner()
    result = await execute_workflow(
        AgentTaskPayload(job_id="modern-job", workflow="modern-review", input={}),
        registry=WorkflowRegistry({"modern-review": adapter}),
        runner=runner,
        workspace_root=tmp_path,
    )
    assert result.status is WorkflowStatus.COMPLETED
    assert result.output == "author-2"
    assert [(item.node_id, item.attempt) for item in runner.requests] == [
        ("author", 1),
        ("reviewer", 1),
        ("author", 2),
        ("reviewer", 2),
    ]
    assert all(request.sandbox is Sandbox.full_access for request in runner.requests)
    assert [request.model for request in runner.requests] == [
        "author-model",
        "reviewer-model",
        "author-model",
        "reviewer-model",
    ]
    assert [request.reasoning_effort for request in runner.requests] == [
        AgentReasoningEffort.HIGH,
        AgentReasoningEffort.XHIGH,
        AgentReasoningEffort.HIGH,
        AgentReasoningEffort.XHIGH,
    ]
    assert runner.requests[0].audit_path != runner.requests[2].audit_path


@pytest.mark.asyncio
async def test_modern_coordinator_emits_node_events_without_affecting_result(tmp_path):
    adapter = ModernReviewAdapter()
    runner = ModernRunner()
    events = []
    result = await execute_workflow(
        AgentTaskPayload(job_id="event-job", workflow="modern-review", input={}),
        registry=WorkflowRegistry({"modern-review": adapter}),
        runner=runner,
        workspace_root=tmp_path,
        event_callback=events.append,
    )
    assert result.status is WorkflowStatus.COMPLETED
    assert [(event["event_type"], event["node_id"], event["attempt"]) for event in events] == [
        ("node_started", "author", 1),
        ("node_completed", "author", 1),
        ("node_started", "validator", 1),
        ("node_completed", "validator", 1),
        ("node_started", "reviewer", 1),
        ("node_completed", "reviewer", 1),
        ("node_started", "author", 2),
        ("node_completed", "author", 2),
        ("node_started", "validator", 2),
        ("node_completed", "validator", 2),
        ("node_started", "reviewer", 2),
        ("node_completed", "reviewer", 2),
    ]


@pytest.mark.asyncio
async def test_missing_author_artifact_fails_before_validation_or_review(tmp_path):
    adapter = MissingArtifactAdapter()
    runner = ModernRunner()
    events = []
    result = await execute_workflow(
        AgentTaskPayload(job_id="missing-artifact-job", workflow="missing-artifact", input={}),
        registry=WorkflowRegistry({"missing-artifact": adapter}),
        runner=runner,
        workspace_root=tmp_path,
        event_callback=events.append,
    )

    assert result.status is WorkflowStatus.FAILED
    assert [(request.node_id, request.attempt) for request in runner.requests] == [("author", 1)]
    assert [event["node_id"] for event in events if event["event_type"] == "node_started"] == ["author"]
    assert [event["node_id"] for event in events if event["event_type"] == "node_failed"] == ["author"]
    assert not any(event["node_id"] in {"validator", "reviewer"} for event in events)


@pytest.mark.asyncio
async def test_final_deterministic_failure_telemetry_keeps_validation_finding(tmp_path):
    adapter = LoopAdapter(validation_failures=10)
    adapter.max_review_rounds = 2
    runner = ModernRunner()
    events = []
    result = await execute_workflow(
        AgentTaskPayload(job_id="deterministic-exhaust-job", workflow="loop", input={}),
        registry=WorkflowRegistry({"loop": adapter}),
        runner=runner,
        workspace_root=tmp_path,
        event_callback=events.append,
    )

    assert result.status is WorkflowStatus.FAILED
    assert [(request.node_id, request.attempt) for request in runner.requests] == [
        ("author", 1),
        ("author", 2),
    ]
    validator_failures = [
        event for event in events
        if event["event_type"] == "node_failed" and event["node_id"] == "validator"
    ]
    assert len(validator_failures) == 1
    assert "deterministic validator finding" in validator_failures[0]["message"]


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
async def test_modern_semantic_rejection_reports_blockers_and_does_not_publish(tmp_path):
    adapter = ModernReviewAdapter()
    runner = AlwaysBlockingRunner()
    events = []
    progress = []
    result = await execute_workflow(
        AgentTaskPayload(job_id="modern-exhaust-job", workflow="modern-review", input={}),
        registry=WorkflowRegistry({"modern-review": adapter}),
        runner=runner,
        workspace_root=tmp_path,
        event_callback=events.append,
        progress_callback=progress.append,
    )

    assert result.status is WorkflowStatus.FAILED
    assert result.error == "Publication rejected by semantic review."
    assert [(request.node_id, request.attempt) for request in runner.requests] == [
        ("author", 1),
        ("reviewer", 1),
        ("author", 2),
        ("reviewer", 2),
    ]
    reviewer_completions = [
        event for event in events
        if event["event_type"] == "node_completed" and event["node_id"] == "reviewer"
    ]
    assert [event["message"] for event in reviewer_completions] == [
        "Reviewer found 1 blocking finding.",
        "Reviewer found 1 blocking finding.",
    ]
    assert "Maximum revision attempts reached." in [event["message"] for event in progress]


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
