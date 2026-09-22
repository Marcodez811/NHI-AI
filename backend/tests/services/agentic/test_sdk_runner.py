from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import MultiProvider, Usage
from agents.extensions.models.litellm_model import LitellmModel
from agents.stream_events import AgentUpdatedStreamEvent, RunItemStreamEvent
from pydantic import SecretStr

from app.services.agentic.contracts import (
    AgentExecutionRequest,
    AgentRunner,
    ReviewFinding,
    ReviewFindingStatus,
    ReviewOutcome,
    ReviewSeverity,
)
from app.services.agentic.runner import WorkflowExecutionError, WorkflowTimeoutError
from app.services.agentic.sdk_runner import (
    AgentsSdkRunner,
    _build_path_grants,
    _build_sandbox_manifest,
    _RESTRICTED_MANIFEST_ROOT,
)


def _request(tmp_path, **overrides) -> AgentExecutionRequest:
    fields = dict(
        run_id=uuid4(),
        node_id="extraction",
        role="extractor",
        workspace=tmp_path,
        prompt="extract the deck",
    )
    fields.update(overrides)
    return AgentExecutionRequest(**fields)


# ---------------------------------------------------------------------------
# Path-grant translation
# ---------------------------------------------------------------------------


def test_read_only_and_writable_paths_become_distinct_grants(tmp_path):
    input_dir = tmp_path / "input"
    extracted_dir = tmp_path / "work" / "extracted"
    input_dir.mkdir()
    extracted_dir.mkdir(parents=True)

    request = _request(tmp_path, read_only_paths=(input_dir,), writable_paths=(extracted_dir,))
    grants = _build_path_grants(request, workspace=tmp_path.resolve())

    by_path = {grant.path: grant for grant in grants}
    assert by_path[str(input_dir.resolve())].read_only is True
    assert by_path[str(extracted_dir.resolve())].read_only is False


def test_restrict_workspace_points_the_manifest_root_at_a_sandbox_virtual_allowlist(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    restricted = _request(tmp_path, read_only_paths=(input_dir,), restrict_workspace=True)
    manifest, _scope = _build_sandbox_manifest(restricted)
    assert manifest.root == _RESTRICTED_MANIFEST_ROOT
    assert len(manifest.extra_path_grants) == 1

    unrestricted = _request(tmp_path, read_only_paths=(input_dir,), restrict_workspace=False)
    manifest, _scope = _build_sandbox_manifest(unrestricted)
    assert manifest.root == str(tmp_path.resolve())


def test_hidden_path_colliding_with_a_granted_path_is_rejected(tmp_path):
    shared = tmp_path / "work" / "evidence.json"
    shared.parent.mkdir(parents=True)
    shared.write_text("{}", encoding="utf-8")

    request = _request(tmp_path, hidden_paths=(shared,), read_only_paths=(shared,))
    with pytest.raises(WorkflowExecutionError):
        _build_path_grants(request, workspace=tmp_path.resolve())


def test_hidden_path_outside_the_workspace_is_accepted(tmp_path):
    # The slides adapter hides the original source documents, which live on the
    # shared documents volume rather than in the job workspace. In this sandbox they
    # are invisible by construction, so hiding them must not fail the request.
    workspace = tmp_path / "job"
    evidence = workspace / "work" / "evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}", encoding="utf-8")
    original_source = tmp_path / "documents" / "doc-1" / "報告書.docx"

    request = _request(workspace, hidden_paths=(original_source,), read_only_paths=(evidence,))
    grants = _build_path_grants(request, workspace=workspace.resolve())

    assert [grant.path for grant in grants] == [evidence.resolve().as_posix()]


def test_granting_a_directory_that_contains_a_hidden_file_is_rejected(tmp_path):
    hidden_history = tmp_path / "work" / "intermediate" / "semantic_review_history.json"
    hidden_history.parent.mkdir(parents=True)

    request = _request(tmp_path, hidden_paths=(hidden_history,), read_only_paths=(hidden_history.parent,))
    with pytest.raises(WorkflowExecutionError, match="both hidden and granted"):
        _build_path_grants(request, workspace=tmp_path.resolve())


def test_granting_a_file_inside_a_hidden_directory_is_rejected(tmp_path):
    hidden_inputs = tmp_path / "input"
    hidden_inputs.mkdir()

    request = _request(tmp_path, hidden_paths=(hidden_inputs,), writable_paths=(hidden_inputs / "source.pdf",))
    with pytest.raises(WorkflowExecutionError, match="both hidden and granted"):
        _build_path_grants(request, workspace=tmp_path.resolve())


def test_path_outside_the_workspace_is_rejected(tmp_path, monkeypatch):
    outside = tmp_path.parent / "sibling-job" / "secret.txt"
    request = _request(tmp_path, read_only_paths=(outside,))
    with pytest.raises(WorkflowExecutionError, match="escapes the workflow workspace"):
        _build_path_grants(request, workspace=tmp_path.resolve())


# ---------------------------------------------------------------------------
# Stream-event -> TurnAudit / progress mapping, via a fake SDK runner
# ---------------------------------------------------------------------------


class FakeStreamResult:
    def __init__(self, *, events=(), final_output="extracted the deck", error: Exception | None = None):
        self._events = list(events)
        self._error = error
        self.new_items: list = []
        self.final_output = final_output
        self.context_wrapper = SimpleNamespace(
            usage=Usage(requests=1, input_tokens=20, output_tokens=8, total_tokens=28)
        )

    async def stream_events(self):
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error

    def final_output_as(self, cls, raise_if_incorrect_type=False):
        """Mirror ``agents.result.RunResultBase.final_output_as`` for tests.

        The real accessor is what ``AgentsSdkRunner._typed_output`` reads the
        SDK's already-validated structured output through; this fake keeps the
        same contract (a type mismatch raises ``TypeError`` only when asked)
        so ``sdk_runner.py`` is exercised through its real read path.
        """

        if raise_if_incorrect_type and not isinstance(self.final_output, cls):
            raise TypeError(f"final output is not of type {cls.__name__}")
        return self.final_output


class FakeSdkRunner:
    """Fake ``agents.Runner`` double that never touches the network."""

    def __init__(self, *, events=(), final_output="extracted the deck", error: Exception | None = None):
        self.events = events
        self.final_output = final_output
        self.error = error
        self.last_call: dict | None = None

    def run_streamed(self, agent, input, **kwargs):
        self.last_call = {"agent": agent, "input": input, **kwargs}
        return FakeStreamResult(events=self.events, final_output=self.final_output, error=self.error)


def test_agents_sdk_runner_satisfies_the_agent_runner_protocol():
    assert isinstance(AgentsSdkRunner(), AgentRunner)
    assert AgentsSdkRunner.name == "agents"


@pytest.mark.asyncio
async def test_run_maps_stream_events_onto_progress_and_a_single_turn_audit(tmp_path):
    events = [
        RunItemStreamEvent(name="reasoning_item_created", item=None),
        RunItemStreamEvent(name="tool_called", item=None),
        RunItemStreamEvent(name="tool_output", item=None),
        AgentUpdatedStreamEvent(new_agent=None),
    ]
    fake_runner = FakeSdkRunner(events=events)
    runner = AgentsSdkRunner(runner_factory=fake_runner, timeout_seconds=2, heartbeat_seconds=0)
    request = _request(tmp_path, audit_path=tmp_path / "audit.json")

    progress: list[dict] = []
    result = await runner.run(request, progress_callback=progress.append)

    assert result.provider_run_id == str(request.run_id)
    assert result.response == "extracted the deck"
    assert result.usage == {"requests": 1, "input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
    assert len(result.audits) == 1
    audit = result.audits[0]
    assert audit.kind == "extraction"
    assert audit.status == "completed"
    assert audit.error is None
    assert audit.prompt == "extract the deck"

    node_progress = [event for event in progress if event["event_type"] == "node_progress"]
    assert node_progress, "SDK stream events should surface as node progress"
    assert all(event["node_id"] == "extraction" for event in node_progress)
    assert all(event["runner"] == "agents" for event in node_progress)
    # SDK event names are audit detail; the public progress callback never sees them.
    assert all("sdk_event" not in event for event in progress)

    persisted = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert persisted["thread_id"] == str(request.run_id)
    assert persisted["turns"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_persists_a_failed_audit_and_raises_workflow_execution_error(tmp_path):
    from agents import ModelBehaviorError

    fake_runner = FakeSdkRunner(error=ModelBehaviorError("the model returned an invalid tool call"))
    runner = AgentsSdkRunner(runner_factory=fake_runner, timeout_seconds=2, heartbeat_seconds=0)
    request = _request(tmp_path, audit_path=tmp_path / "audit.json")

    with pytest.raises(WorkflowExecutionError):
        await runner.run(request)

    persisted = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert persisted["turns"][0]["status"] == "failed"
    assert persisted["turns"][0]["error"] is not None


@pytest.mark.asyncio
async def test_run_times_out_on_a_hanging_stream(tmp_path):
    import asyncio

    class HangingStreamResult(FakeStreamResult):
        async def stream_events(self):
            await asyncio.sleep(1)
            if False:
                yield None  # pragma: no cover - keeps this an async generator

    class HangingSdkRunner:
        def run_streamed(self, agent, input, **kwargs):
            return HangingStreamResult()

    runner = AgentsSdkRunner(runner_factory=HangingSdkRunner(), timeout_seconds=0.01, heartbeat_seconds=0)
    with pytest.raises(WorkflowTimeoutError):
        await runner.run(_request(tmp_path))


@pytest.mark.asyncio
async def test_run_rejects_a_raw_output_schema(tmp_path):
    runner = AgentsSdkRunner(runner_factory=FakeSdkRunner(), timeout_seconds=2, heartbeat_seconds=0)
    request = _request(tmp_path, output_schema={"type": "object"})

    with pytest.raises(WorkflowExecutionError):
        await runner.run(request)


# ---------------------------------------------------------------------------
# Typed output (Stage 2: ``output_type``, not a raw ``output_schema``)
# ---------------------------------------------------------------------------


def _review_outcome() -> ReviewOutcome:
    return ReviewOutcome(
        summary="one blocking finding",
        findings=[
            ReviewFinding(
                status=ReviewFindingStatus.OPEN,
                severity=ReviewSeverity.BLOCKING,
                category="factual",
                issue_key="fix",
                locations=("slide:1",),
                description="fix",
                correction="fix",
            )
        ],
    )


@pytest.mark.asyncio
async def test_run_returns_the_runner_validated_typed_output(tmp_path):
    outcome = _review_outcome()
    fake_runner = FakeSdkRunner(final_output=outcome)
    runner = AgentsSdkRunner(runner_factory=fake_runner, timeout_seconds=2, heartbeat_seconds=0)
    request = _request(tmp_path, node_id="reviewer", role="presentation_reviewer", output_type=ReviewOutcome)

    result = await runner.run(request)

    # The SDK already validated ``final_output`` into ``ReviewOutcome`` because
    # ``_build_agent`` declared ``output_type=request.output_type`` on the
    # ``SandboxAgent``; this runner only has to read it back out.
    assert result.output == outcome
    # No text message was streamed, so the audit trail falls back to the
    # validated output's own JSON rather than an empty response.
    assert result.response == outcome.model_dump_json()

    assert fake_runner.last_call["agent"].output_type is ReviewOutcome


@pytest.mark.asyncio
async def test_run_raises_when_the_sdk_ignores_the_requested_output_type(tmp_path):
    fake_runner = FakeSdkRunner(final_output="not a review outcome")
    runner = AgentsSdkRunner(runner_factory=fake_runner, timeout_seconds=2, heartbeat_seconds=0)
    request = _request(tmp_path, node_id="reviewer", role="presentation_reviewer", output_type=ReviewOutcome)

    with pytest.raises(WorkflowExecutionError):
        await runner.run(request)


@pytest.mark.asyncio
async def test_run_leaves_output_unset_when_no_output_type_was_requested(tmp_path):
    fake_runner = FakeSdkRunner(final_output="extracted the deck")
    runner = AgentsSdkRunner(runner_factory=fake_runner, timeout_seconds=2, heartbeat_seconds=0)
    request = _request(tmp_path)

    result = await runner.run(request)

    assert result.output is None
    assert fake_runner.last_call["agent"].output_type is None


@pytest.mark.asyncio
async def test_litellm_run_uses_explicit_key_usage_and_private_tracing(tmp_path):
    fake_runner = FakeSdkRunner()
    runner = AgentsSdkRunner(
        runner_factory=fake_runner,
        timeout_seconds=2,
        heartbeat_seconds=0,
        litellm_api_keys={"gemini": SecretStr("configured-gemini-key")},
    )

    await runner.run(_request(tmp_path, model="litellm/gemini/example-model"))

    call = fake_runner.last_call
    assert call["agent"].model == "litellm/gemini/example-model"
    config = call["run_config"]
    assert isinstance(config.model_provider, MultiProvider)
    routed_model = config.model_provider.get_model(call["agent"].model)
    assert isinstance(routed_model, LitellmModel)
    assert routed_model.model == "gemini/example-model"
    assert routed_model.api_key == "configured-gemini-key"
    assert config.model_settings.include_usage is True
    assert config.tracing_disabled is True


@pytest.mark.asyncio
async def test_litellm_run_fails_before_model_call_when_key_is_missing(tmp_path):
    fake_runner = FakeSdkRunner()
    runner = AgentsSdkRunner(runner_factory=fake_runner, timeout_seconds=2, heartbeat_seconds=0)

    with pytest.raises(WorkflowExecutionError, match="No configured API key") as caught:
        await runner.run(_request(tmp_path, model="litellm/anthropic/example-model"))

    assert fake_runner.last_call is None
    assert "example-model" not in str(caught.value)


@pytest.mark.asyncio
async def test_bare_model_keeps_default_openai_run_configuration(tmp_path):
    fake_runner = FakeSdkRunner()
    runner = AgentsSdkRunner(runner_factory=fake_runner, timeout_seconds=2, heartbeat_seconds=0)

    await runner.run(_request(tmp_path, model="gpt-example"))

    config = fake_runner.last_call["run_config"]
    assert config.model_settings is None
    assert config.tracing_disabled is False
    assert config.model_provider.get_model("gpt-example").model == "gpt-example"
