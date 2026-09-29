"""Agents SDK execution primitives for generic agentic workflows.

This is the runner seam described in ``docs/agents-sdk-migration-plan.md``: a second
:class:`~app.services.agentic.contracts.AgentRunner` implementation, backed by the
``openai-agents`` sandbox runtime instead of ``openai_codex``, that ``RunnerRegistry``
can resolve by name alongside ``CodexAgentRunner``. It is wired into
``_build_runner_registry`` but never selected in production while every
``agent_*_runner`` setting defaults to ``"codex"`` -- flipping a node onto this runner is
a configuration change, not a deploy. The extraction node (Stage 1) and reviewer node
(Stage 2) both select this runner through that per-node setting; the author node does not
yet, and stays on Codex until Stage 3.

Structured output (Stage 2) is a typed ``AgentExecutionRequest.output_type``, not the raw
``output_schema`` dict Codex accepts: ``SandboxAgent.output_type`` makes the SDK itself
parse and validate the model's final response, and ``_typed_output`` reads that already-
validated value back out through ``RunResultStreaming.final_output_as``. A raw
``output_schema`` is still rejected outright -- see the guard at the top of ``run`` --
because it has no equivalent here.

``AgentsSdkRunner.run`` mirrors ``CodexAgentRunner.run``'s external shape exactly: one
provider activation per :class:`AgentExecutionRequest`, one :class:`TurnAudit`, audits
persisted the same way, progress reported through the existing ``ProgressReporter``, and
every failure routed through ``safe_error`` before it reaches a caller. That parity is
what lets ``service.py``, ``events.py``, and TaskIQ progress stay untouched by this
module.

A note on path isolation, confirmed by reading the installed ``agents`` package rather
than assumed from the plan: ``SandboxPathGrant`` only grants access to paths *outside* an
already-scoped sandbox manifest root (``WorkspacePathPolicy._sandbox_path_and_grant``
returns a path unconditionally, with no grant lookup at all, once it is already under the
root). It has no subtractive counterpart -- there is no way to mask or downgrade a path
that is already inside the root, which is exactly what Codex's bwrap launch achieves with
``--tmpfs``/``--ro-bind`` remounts. This runner therefore honors ``restrict_workspace``
by choosing *where the manifest root points*, not by post-hoc masking:

* ``restrict_workspace=True`` points the manifest root at a sandbox-virtual path that has
  nothing mounted under it, so only the explicitly granted ``read_only_paths`` and
  ``writable_paths`` are ever visible. A path is "hidden" by simply never being granted --
  this is a true allowlist, matching Codex's own ``restrict_workspace`` branch.
* ``restrict_workspace=False`` points the manifest root at the real workspace directory
  (matching Codex's default ``cwd``), so the whole workspace is visible as one tree.
  ``read_only_paths`` and ``writable_paths`` are still translated into
  ``SandboxPathGrant`` objects for API and audit parity, but because those paths already
  live under the root, the grants have no additional effect in this branch -- the SDK
  gives us no primitive to downgrade or mask a subtree of an already-visible root. Closing
  that gap needs either a narrower per-node root (the Stage 1 "narrowest grants" plan) or
  session/manifest-level work beyond ``SandboxPathGrant`` and ``SandboxWorkspaceScope``.

No grants -> no sandbox (Stage 5, docs/agents-sdk-migration-plan.md): a request that asks
for neither ``read_only_paths`` nor ``writable_paths`` never touches the workspace, so it
runs as a plain ``agents.Agent`` -- no ``SandboxAgent``, no ``Manifest``, no
``Filesystem``/``Shell`` capabilities, and no ``SandboxRunConfig`` on the ``RunConfig`` at
all. This was forced by two facts about the installed SDK, not a style preference: the
runner never supplies ``run_config.sandbox.client`` (a concrete sandbox backend is out of
scope here, per the class docstring below), so any sandboxed run refuses to start outright;
and even a supplied local backend (``UnixLocalSandboxClient``) inherits the worker's
environment and does not confine shell commands on Linux, while the ``Filesystem``
capability exposes only ``view_image``/``apply_patch`` -- no text-read tool, so reading a
file at all would additionally require ``Shell``. A stage with nothing to read (the slides
planner works from evidence already folded into its prompt -- see
``app/services/slides/evidence.py``'s ``render_compact_evidence``) needs none of that
machinery, so it gets the plain path instead. A request WITH grants is unaffected and keeps
building the ``SandboxAgent``/``Manifest``/``SandboxRunConfig`` exactly as before.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openai.types.shared import Reasoning
from pydantic import BaseModel, SecretStr

from agents import (
    Agent,
    AgentsException,
    InputGuardrailTripwireTriggered,
    ItemHelpers,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelSettings,
    ModelProvider,
    MultiProvider,
    OutputGuardrailTripwireTriggered,
    RunConfig,
    RunResultStreaming,
    Runner,
    Usage,
    UserError,
)
from agents.sandbox import (
    Capability,
    Manifest,
    SandboxAgent,
    SandboxPathGrant,
    SandboxRunConfig,
    SandboxWorkspaceScope,
)
from agents.extensions.models.litellm_model import LitellmModel
from agents.models.multi_provider import MultiProviderMap
from agents.sandbox.capabilities import Filesystem, Shell, Skills
from agents.sandbox.entries import LocalDir
from agents.stream_events import AgentUpdatedStreamEvent, RawResponsesStreamEvent, RunItemStreamEvent

from .contracts import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentPhase,
    AgentReasoningEffort,
    ProgressCallback,
    TurnAudit,
)
from .runner import ProgressReporter, WorkflowExecutionError, WorkflowTimeoutError, _turn_phase, safe_error

# The manifest root used when a request asks for ``restrict_workspace``. It is a
# sandbox-virtual path -- it never corresponds to a real host directory -- so it starts
# with nothing mounted and only the paths explicitly granted below ever become visible.
_RESTRICTED_MANIFEST_ROOT = "/workspace"

StreamEvent = RawResponsesStreamEvent | RunItemStreamEvent | AgentUpdatedStreamEvent

# Maps ``RunItemStreamEvent.name`` onto the same non-sensitive (stage, message)
# vocabulary ``runner._safe_progress`` uses for Codex, so a progress consumer sees one
# vocabulary regardless of which provider produced the event.
_RUN_ITEM_PROGRESS: dict[str, tuple[str, str]] = {
    "message_output_created": ("working", "The agent produced a response message"),
    "reasoning_item_created": ("planning", "The agent updated its reasoning"),
    "tool_called": ("working", "The agent started a workflow work step"),
    "tool_output": ("working", "The agent completed a workflow work step"),
    "tool_search_called": ("working", "The agent started a workflow work step"),
    "tool_search_output_created": ("working", "The agent completed a workflow work step"),
    "handoff_requested": ("agent", "The agent requested a handoff"),
    "handoff_occured": ("agent", "The agent completed a handoff"),
    "mcp_approval_requested": ("warning", "The agent requested a tool approval"),
    "mcp_approval_response": ("agent", "The agent received a tool approval response"),
    "mcp_list_tools": ("working", "The agent listed available tools"),
}


def _sdk_stream_progress(event: StreamEvent) -> tuple[str, str, str] | None:
    """Return ``(stage, message, sdk_event_name)`` for a reportable SDK event, else ``None``.

    Raw model deltas (``RawResponsesStreamEvent``) are token-level and are not surfaced
    as discrete progress events, matching how Codex's per-token deltas are not surfaced
    either.
    """

    if isinstance(event, RunItemStreamEvent):
        mapped = _RUN_ITEM_PROGRESS.get(event.name)
        return (mapped[0], mapped[1], event.name) if mapped is not None else None
    if isinstance(event, AgentUpdatedStreamEvent):
        return ("agent", "The agent run continued with a new agent", "agent_updated_stream_event")
    return None


def _require_within_workspace(raw_path: Path, workspace: Path, *, label: str) -> Path:
    path = Path(raw_path).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise WorkflowExecutionError(f"{label} escapes the workflow workspace") from exc
    return path


def _overlaps(first: Path, second: Path) -> bool:
    """Whether one path is the other, or contains it."""

    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _build_path_grants(request: AgentExecutionRequest, *, workspace: Path) -> tuple[SandboxPathGrant, ...]:
    """Translate the stage path policy into ``SandboxPathGrant`` objects.

    ``hidden_paths`` has no positive representation here -- see the module docstring. In
    this sandbox nothing is visible unless granted, so a hidden path is already invisible
    and is used only to reject a grant that would expose it. Hidden paths may therefore
    lie outside the workspace: the slides adapter hides the original source documents on
    the shared documents volume so that no stage after extraction can reread them.

    A grant is rejected when it overlaps a hidden path in either direction: granting a
    directory that contains a hidden file would expose it, and granting a file inside a
    hidden directory would contradict the policy.
    """

    hidden = tuple(Path(path).resolve() for path in request.hidden_paths)
    grants: list[SandboxPathGrant] = []
    for raw_path, read_only, label in (
        *((raw_path, True, "read-only stage path") for raw_path in request.read_only_paths),
        *((raw_path, False, "writable stage path") for raw_path in request.writable_paths),
    ):
        path = _require_within_workspace(Path(raw_path), workspace, label=label)
        if any(_overlaps(path, hidden_path) for hidden_path in hidden):
            raise WorkflowExecutionError("a stage path cannot be both hidden and granted")
        grants.append(SandboxPathGrant(path=path.as_posix(), read_only=read_only))
    return tuple(grants)


def _build_sandbox_manifest(request: AgentExecutionRequest) -> tuple[Manifest, SandboxWorkspaceScope]:
    """Build the manifest root and grants for one request; see the module docstring."""

    workspace = request.workspace.resolve()
    grants = _build_path_grants(request, workspace=workspace)
    root = _RESTRICTED_MANIFEST_ROOT if request.restrict_workspace else str(workspace)
    return Manifest(root=root, extra_path_grants=grants), SandboxWorkspaceScope()


def _reasoning_model_settings(effort: AgentReasoningEffort | None) -> ModelSettings:
    if effort is None:
        return ModelSettings()
    return ModelSettings(reasoning=Reasoning(effort=effort.value))


class _KeyedLitellmProvider(ModelProvider):
    """Pass a server-owned key to LiteLLM without relying on process environment."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def get_model(self, model_name: str | None) -> LitellmModel:
        if not model_name:
            raise WorkflowExecutionError("LiteLLM model name is missing")
        return LitellmModel(model_name, api_key=self.api_key)


def build_litellm_run_config(model: str | None, api_keys: Mapping[str, SecretStr | None]) -> RunConfig | None:
    """Use the SDK's prefix routing while supplying a key to its LiteLLM adapter.

    Returns ``None`` for a non-``litellm/...`` model, so callers fall back to
    a plain ``RunConfig()`` (the OpenAI provider reads its key from process
    configuration the same way it already does elsewhere). Public so other
    callers that build an ``agents.Agent`` directly -- ``app.services.chat.engine``
    is the first -- can reuse this exact key-wiring instead of duplicating it;
    ``AgentsSdkRunner.run`` below is just its first caller.
    """

    if model is None or not model.startswith("litellm/"):
        return None
    provider_name = model.removeprefix("litellm/").partition("/")[0]
    if not provider_name or provider_name not in api_keys:
        raise WorkflowExecutionError("No configured API key for this LiteLLM provider")
    secret = api_keys[provider_name]
    if secret is None or not secret.get_secret_value():
        raise WorkflowExecutionError(f"No configured API key for LiteLLM provider '{provider_name}'")
    provider_map = MultiProviderMap()
    provider_map.add_provider("litellm", _KeyedLitellmProvider(secret.get_secret_value()))
    return RunConfig(
        model_provider=MultiProvider(provider_map=provider_map),
        model_settings=ModelSettings(include_usage=True),
        tracing_disabled=True,
    )


def _build_agent(
    request: AgentExecutionRequest,
    *,
    default_model: str | None,
    default_reasoning_effort: AgentReasoningEffort | None,
    manifest: Manifest,
) -> SandboxAgent:
    effective_model = request.model if request.model is not None else default_model
    effective_effort = request.reasoning_effort if request.reasoning_effort is not None else default_reasoning_effort
    capabilities: list[Capability] = [Filesystem(), Shell()]
    if request.skill_names:
        # Skills are already staged onto disk at this path by
        # ``staging.stage_declared_skills`` before any runner is invoked, so mounting the
        # whole directory tree mounts exactly the requested, and only the requested,
        # skills -- one ``SKILL.md`` per name, matching the Codex ``SkillInput`` layout.
        skills_source = request.workspace / ".agents" / "skills"
        capabilities.append(Skills(from_=LocalDir(src=skills_source), skills_path=".agents/skills"))
    return SandboxAgent(
        name=request.role,
        instructions=request.instructions,
        model=effective_model,
        model_settings=_reasoning_model_settings(effective_effort),
        capabilities=capabilities,
        default_manifest=manifest,
        # ``output_type=None`` keeps the agent's ordinary free-form text
        # output; a declared type is this runner's native structured-output
        # path (Stage 2), unlike ``output_schema`` above which it rejects.
        output_type=request.output_type,
    )


def _build_plain_agent(
    request: AgentExecutionRequest,
    *,
    default_model: str | None,
    default_reasoning_effort: AgentReasoningEffort | None,
) -> Agent:
    """Build a tool-less ``agents.Agent`` for a request with no path grants.

    See the "no grants -> no sandbox" rule in the module docstring: a stage with nothing to
    read needs none of ``SandboxAgent``'s manifest or ``Filesystem``/``Shell``
    capabilities. Model, reasoning effort, and structured-output policy are resolved
    exactly as ``_build_agent`` resolves them for a sandboxed request.
    """

    effective_model = request.model if request.model is not None else default_model
    effective_effort = request.reasoning_effort if request.reasoning_effort is not None else default_reasoning_effort
    return Agent(
        name=request.role,
        instructions=request.instructions,
        model=effective_model,
        model_settings=_reasoning_model_settings(effective_effort),
        output_type=request.output_type,
    )


def _typed_output(result: RunResultStreaming, output_type: type[BaseModel] | None) -> BaseModel | None:
    """Return the SDK's validated structured output, when one was requested.

    Setting ``Agent.output_type`` makes the SDK itself parse and validate the
    model's final response into that type; ``final_output_as`` is the SDK's
    own accessor for that already-validated value, with
    ``raise_if_incorrect_type`` turning a provider that ignored the schema
    into a controlled failure instead of a silent type mismatch downstream.
    """

    if output_type is None:
        return None
    try:
        return result.final_output_as(output_type, raise_if_incorrect_type=True)
    except TypeError as exc:
        raise WorkflowExecutionError("Agents SDK did not return the requested typed output") from exc


def _response_text(result: RunResultStreaming) -> str | None:
    text = ItemHelpers.text_message_outputs(result.new_items)
    if text:
        return text
    if isinstance(result.final_output, str) and result.final_output:
        return result.final_output
    return None


def _usage_payload(usage: Usage) -> dict[str, int]:
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


async def _persist_audits(path: Path | None, provider_run_id: str, audits: list[TurnAudit]) -> None:
    """Persist audits the same way ``CodexRunner._persist_audits`` does.

    Kept as a small local copy rather than a call into ``runner.CodexRunner`` so this
    module does not depend on a Codex-specific class's implementation detail; the on-disk
    shape (``{"thread_id": ..., "turns": [...]}``) is kept identical so audit consumers
    and retention policy do not need to special-case the runner that produced a record.
    """

    if path is None:
        return
    payload = (
        json.dumps(
            {"thread_id": provider_run_id, "turns": [item.model_dump(mode="json") for item in audits]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, payload, encoding="utf-8")


# SDK errors that mean the run stopped without producing a usable result. They are
# caught separately from a bare ``Exception`` only so the docstring above stays true --
# every one of them is a documented ``agents`` exception, not a guess.
_SDK_TURN_FAILURES = (
    MaxTurnsExceeded,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    ModelBehaviorError,
    UserError,
    AgentsException,
)


class AgentsSdkRunner:
    """Provider-neutral runner backed by the OpenAI Agents SDK, sandboxed or plain.

    ``runner_factory`` defaults to the real ``agents.Runner`` class and is overridable so
    tests can supply a fake with a ``run_streamed`` method that never reaches the
    network. ``sandbox_client`` is left unset by Stage 0: selecting a concrete sandbox
    backend (``unix_local`` vs. ``docker``, per the migration plan's Stage 3 discussion)
    is deliberately out of scope here and is threaded through so a later stage can supply
    one without changing this class's shape. A request without path grants never reaches
    ``sandbox_client`` at all -- see ``run``'s "no grants -> no sandbox" branch and the
    module docstring.
    """

    name = "agents"

    def __init__(
        self,
        *,
        model: str | None = None,
        reasoning_effort: AgentReasoningEffort | None = AgentReasoningEffort.HIGH,
        timeout_seconds: float = 2700.0,
        heartbeat_seconds: float = 10.0,
        runner_factory: Any = Runner,
        sandbox_client: Any = None,
        litellm_api_keys: Mapping[str, SecretStr | None] | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.runner_factory = runner_factory
        self.sandbox_client = sandbox_client
        self.litellm_api_keys = litellm_api_keys or {}

    async def run(
        self,
        request: AgentExecutionRequest,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> AgentExecutionResult:
        if request.output_schema is not None:
            # A raw JSON schema (Codex's per-turn ``output_schema``) has no equivalent
            # here: ``SandboxAgent.output_type`` wants a Python type or
            # ``AgentOutputSchemaBase``, not a dict schema. Structured output arrives in
            # Stage 2 as a typed ``output_type=ReviewOutcome``, not as a schema pass-through.
            raise WorkflowExecutionError("Agents SDK runner does not support raw turn output schemas")

        provider_run_id = str(request.run_id)
        turn_phase = _turn_phase(request.node_id if request.node_id else request.role)
        effective_model = request.model if request.model is not None else self.model
        effective_effort = request.reasoning_effort if request.reasoning_effort is not None else self.reasoning_effort

        async def forward_progress(event: dict[str, Any]) -> None:
            if progress_callback is None:
                return
            enriched = dict(event)
            enriched.setdefault("event_type", "heartbeat" if event.get("heartbeat") else "node_progress")
            enriched.update(
                {
                    "node_id": request.node_id,
                    "role": request.role,
                    "runner": self.name,
                    "model": effective_model,
                    "reasoning_effort": getattr(effective_effort, "value", effective_effort),
                    "attempt": request.attempt,
                }
            )
            result = progress_callback(enriched)
            if inspect.isawaitable(result):
                await result

        reporter = ProgressReporter(
            callback=forward_progress,
            path=request.audit_path.with_name("progress.jsonl") if request.audit_path else None,
            heartbeat_seconds=self.heartbeat_seconds,
        )
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(reporter.heartbeat_loop(stop_heartbeat))
        started = asyncio.get_running_loop().time()

        async def execute() -> AgentExecutionResult:
            run_config = build_litellm_run_config(effective_model, self.litellm_api_keys) or RunConfig()
            # "No grants -> no sandbox" (module docstring): a request that asks for
            # neither read-only nor writable paths never touches the workspace, so it
            # runs as a plain agent with no capabilities and no SandboxRunConfig at
            # all, instead of the grant-bearing SandboxAgent path below.
            if request.read_only_paths or request.writable_paths:
                manifest, _workspace_scope = _build_sandbox_manifest(request)
                agent = _build_agent(
                    request,
                    default_model=self.model,
                    default_reasoning_effort=self.reasoning_effort,
                    manifest=manifest,
                )
                run_config.sandbox = SandboxRunConfig(client=self.sandbox_client, manifest=manifest)
            else:
                agent = _build_plain_agent(
                    request,
                    default_model=self.model,
                    default_reasoning_effort=self.reasoning_effort,
                )
            await reporter.emit(turn_phase.value, f"Agent {request.node_id} step is running.", phase=turn_phase)
            streamed = self.runner_factory.run_streamed(agent, request.prompt, run_config=run_config)
            try:
                async for event in streamed.stream_events():
                    progress = _sdk_stream_progress(event)
                    if progress is not None:
                        stage, message, sdk_event = progress
                        await reporter.emit(turn_phase.value, message, phase=turn_phase, sdk_event=sdk_event)
            except asyncio.CancelledError:
                # ``wait_for`` cancels the running turn on deadline. Retain a failed
                # record for the attempted turn before propagating cancellation to the
                # workflow deadline handler, matching ``CodexRunner``.
                audit = TurnAudit(
                    turn_id=provider_run_id,
                    kind=request.node_id,
                    status="failed",
                    duration_ms=round((asyncio.get_running_loop().time() - started) * 1000),
                    response=None,
                    error=None,
                    prompt=request.prompt,
                )
                await _persist_audits(request.audit_path, provider_run_id, [audit])
                raise
            except _SDK_TURN_FAILURES as exc:
                error_message = safe_error(str(exc), "Agents SDK turn failed.")
                audit = TurnAudit(
                    turn_id=provider_run_id,
                    kind=request.node_id,
                    status="failed",
                    duration_ms=round((asyncio.get_running_loop().time() - started) * 1000),
                    response=None,
                    error=error_message,
                    prompt=request.prompt,
                )
                await _persist_audits(request.audit_path, provider_run_id, [audit])
                raise WorkflowExecutionError(error_message) from exc

            usage = _usage_payload(streamed.context_wrapper.usage)
            typed_output = _typed_output(streamed, request.output_type)
            # A typed run's ``new_items`` text message is often empty -- the
            # model produced the declared type instead of a chat message --
            # so the audit trail falls back to the validated output's own
            # JSON rather than recording an empty response.
            response = _response_text(streamed) or (typed_output.model_dump_json() if typed_output is not None else None)
            audit = TurnAudit(
                turn_id=provider_run_id,
                kind=request.node_id,
                status="completed",
                duration_ms=round((asyncio.get_running_loop().time() - started) * 1000),
                usage=usage,
                response=response,
                error=None,
                prompt=request.prompt,
            )
            await _persist_audits(request.audit_path, provider_run_id, [audit])
            return AgentExecutionResult(
                provider_run_id=provider_run_id,
                response=response,
                duration_ms=audit.duration_ms,
                usage=usage,
                audits=[audit],
                output=typed_output,
            )

        try:
            return await asyncio.wait_for(execute(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            await reporter.emit(AgentPhase.FAILED.value, "Workflow timed out.", phase=AgentPhase.FAILED)
            raise WorkflowTimeoutError("Workflow timed out.") from exc
        except WorkflowExecutionError:
            await reporter.emit(AgentPhase.FAILED.value, "Workflow execution failed.", phase=AgentPhase.FAILED)
            raise
        except Exception as exc:
            await reporter.emit(AgentPhase.FAILED.value, "Workflow execution failed.", phase=AgentPhase.FAILED)
            raise WorkflowExecutionError("Workflow execution failed.") from exc
        finally:
            stop_heartbeat.set()
            await heartbeat


__all__ = ["AgentsSdkRunner", "build_litellm_run_config"]
