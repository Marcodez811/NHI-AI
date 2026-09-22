"""Workflow lifecycle orchestration built on the generic Codex runner."""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from loguru import logger
from pydantic import BaseModel
from openai_codex import Sandbox

from .contracts import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentPhase,
    AgentTaskPayload,
    AgentTaskResult,
    DeterministicValidationError,
    ValidationInfrastructureError,
    ReviewDecision,
    ReviewOutcome,
    evaluate_review,
    normalize_review_outcome,
    TurnRequest,
    WorkflowStatus,
)
from .registry import WorkflowRegistry, workflow_registry
from .runner import (
    CodexAgentRunner,
    CodexRunResult,
    CodexRunner,
    RunnerRegistry,
    WorkflowExecutionError,
    WorkflowTimeoutError,
    runner_registry,
    safe_error,
)
from .staging import stage_declared_skills


async def _call(value: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke a hook without letting synchronous adapter work block asyncio."""

    callable_value = getattr(value, "__call__", value)
    if inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(callable_value):
        return await value(*args, **kwargs)
    return await asyncio.to_thread(value, *args, **kwargs)


_PHASE_MESSAGES = {
    AgentPhase.QUEUED: "Agent workflow is queued.",
    AgentPhase.PREPARING: "Preparing the agent workflow.",
    AgentPhase.EXTRACTING: "Extracting source documents into the frozen evidence store.",
    AgentPhase.PLANNING: "Proposing a presentation outline.",
    AgentPhase.AWAITING_OUTLINE: "Waiting for outline approval.",
    AgentPhase.DRAFTING: "Drafting the presentation.",
    AgentPhase.VALIDATING: "Validating the generated presentation.",
    AgentPhase.REVIEWING: "Reviewing the generated presentation.",
    AgentPhase.REVISING: "Revising the presentation based on review findings.",
    AgentPhase.PUBLISHING: "Publishing the presentation.",
    AgentPhase.COMPLETED: "The presentation is ready.",
    AgentPhase.FAILED: "Agent workflow failed.",
}


async def _emit_phase(progress_callback: Any, phase: AgentPhase, message: str | None = None) -> None:
    """Send only stable phase data through the public progress callback."""

    if progress_callback is None:
        return
    event = {
        # Keep stage phase-valued as a compatibility bridge for older worker
        # progress writers that forward stage/message but predate ``phase``.
        "stage": phase.value,
        "phase": phase.value,
        "message": message or _PHASE_MESSAGES[phase],
        "heartbeat": False,
    }
    try:
        result = progress_callback(event)
        if inspect.isawaitable(result):
            await result
    except Exception:
        # Progress is advisory and must not alter the workflow outcome.
        return None


async def _emit_event(event_callback: Any, event_type: str, **fields: Any) -> None:
    """Publish diagnostic lifecycle events without affecting execution."""

    if event_callback is None:
        return
    try:
        value = event_callback({"event_type": event_type, **fields})
        if inspect.isawaitable(value):
            await value
    except Exception:
        # Telemetry is advisory; the workflow must remain independent of it.
        return None


def _validate_output(adapter: Any, output: Any) -> Any:
    output_type = getattr(adapter, "output_type", None)
    if output_type is None or output is None:
        return output
    if isinstance(output_type, type) and issubclass(output_type, BaseModel):
        return output_type.model_validate(output)
    if not isinstance(output, output_type):
        raise ValueError("workflow publication returned an invalid output type")
    return output


def _result_output(output: Any) -> Any:
    return output.model_dump(mode="json") if isinstance(output, BaseModel) else output


async def _check_author_completion(adapter: Any, value: Any, result: AgentExecutionResult, workspace: Path) -> None:
    check = getattr(adapter, "post_author_completion_check", None)
    if check is None:
        return
    await _call(check, value, result, workspace)


def _validation_telemetry(exc: BaseException) -> str:
    """Render a safe validator diagnostic while retaining stable codes."""

    codes = tuple(
        str(code).strip()
        for code in getattr(exc, "diagnostic_codes", getattr(exc, "codes", ())) or ()
        if str(code).strip()
    )
    fallback = (
        "Deterministic validator infrastructure failure."
        if isinstance(exc, ValidationInfrastructureError)
        else "Deterministic validation failed."
    )
    finding = safe_error(str(exc), fallback)
    if codes:
        finding = f"{finding} [diagnostic_codes: {', '.join(codes)}]"
    return finding[:12000]


def _validation_infrastructure_cause(exc: BaseException) -> ValidationInfrastructureError | None:
    """Find an infrastructure validation error wrapped by a legacy runner."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ValidationInfrastructureError):
            return current
        current = current.__cause__
    return None


class SemanticReviewRejected(WorkflowExecutionError):
    """Controlled terminal failure for an unresolved semantic review."""

    def __init__(self, blocking_count: int, *, reason: ReviewDecision = ReviewDecision.REJECT_MAX_ATTEMPTS):
        self.blocking_count = blocking_count
        self.reason = reason
        # Keep the public task error stable; the structured reason is emitted
        # through telemetry and retained in the workspace review history.
        super().__init__("Publication rejected by semantic review.")


def _blocking_message(count: int) -> str:
    noun = "finding" if count == 1 else "findings"
    return f"Reviewer found {count} blocking {noun}."


async def _persist_review_history(
    workspace: Path,
    review_attempt: int,
    review: ReviewOutcome,
    evaluation: Any,
) -> None:
    """Keep an immutable-in-practice review trace for diagnosis and replay."""

    path = workspace / "work" / "intermediate" / "semantic_review_history.json"

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        history: list[dict[str, Any]] = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    history = loaded
            except (OSError, TypeError, ValueError):
                history = []
        history.append(
            {
                "attempt": review_attempt,
                "review": review.model_dump(mode="json"),
                "evaluation": evaluation.model_dump(mode="json"),
            }
        )
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        latest = path.parent / "semantic_review.json"
        latest_temporary = latest.with_suffix(".json.tmp")
        latest_temporary.write_text(review.model_dump_json(indent=2) + "\n", encoding="utf-8")
        latest_temporary.replace(latest)

    await asyncio.to_thread(write)


async def _build_review_prompt(adapter: Any, value: Any, workspace: Path, audit: Any, context: Any, previous: ReviewOutcome | None) -> str:
    """Invoke the typed review prompt hook, retaining old hook arity briefly."""

    hook = adapter.build_review_prompt
    try:
        parameters = inspect.signature(hook).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs = {"previous_review": previous} if "previous_review" in parameters else {}
    return str(await _call(hook, value, workspace, audit, context, **kwargs))


def _uses_execution_request(runner: Any) -> bool:
    """Identify the new runner contract while retaining old test doubles.

    ``CodexRunner`` historically accepted ``(workspace, turns, ...)`` and is
    still public.  The coordinator accepts those legacy implementations as
    well; runner implementations using the provider-neutral contract expose a
    first parameter named ``request``.  Explicit Codex adapters are marked by
    their concrete type and do not depend on introspection.
    """

    if isinstance(runner, (CodexAgentRunner,)):
        return True
    if isinstance(runner, CodexRunner):
        return False
    try:
        parameters = list(inspect.signature(runner.run).parameters.values())
    except (TypeError, ValueError, AttributeError):
        return True
    positional = [item for item in parameters if item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD)]
    return bool(positional and positional[0].name in {"request", "execution_request"})


def _as_execution_result(value: Any) -> AgentExecutionResult:
    """Normalize provider/fake results at the coordinator boundary."""

    if isinstance(value, AgentExecutionResult):
        return value
    if isinstance(value, CodexRunResult):
        return AgentExecutionResult(
            provider_run_id=value.thread_id,
            response=value.response,
            duration_ms=None,
            usage=value.last_turn.usage if value.last_turn is not None else None,
            audits=value.audits,
        )
    audits = getattr(value, "audits", ()) or ()
    provider_run_id = getattr(value, "provider_run_id", None) or getattr(value, "thread_id", None) or "unknown"
    return AgentExecutionResult(
        provider_run_id=str(provider_run_id),
        response=getattr(value, "response", None),
        duration_ms=getattr(value, "duration_ms", None),
        usage=getattr(value, "usage", None),
        audits=list(audits),
    )


async def _run_agent(
    runner: Any,
    request: AgentExecutionRequest,
    *,
    progress_callback: Any = None,
) -> AgentExecutionResult:
    """Invoke a provider-neutral runner and normalize its result."""

    if isinstance(runner, CodexRunner):
        # The public CodexRunner predates AgentExecutionRequest.  Wrapping the
        # same operational primitive here gives reviewed workflows independent
        # threads without changing its long-standing direct API.
        runner = CodexAgentRunner(codex_runner=runner)
    if _uses_execution_request(runner):
        result = runner.run(request, progress_callback=progress_callback)
        if inspect.isawaitable(result):
            result = await result
        return _as_execution_result(result)
    # Compatibility path for pre-abstraction runners.  It intentionally runs
    # one turn only; the coordinator still gives each logical activation a
    # separate invocation when using a legacy CodexRunner instance.
    result = runner.run(
        request.workspace,
        [TurnRequest(kind=request.node_id, prompt=request.prompt, sandbox=request.sandbox, output_schema=request.output_schema)],
        skill_names=request.skill_names,
        progress_callback=progress_callback,
        audit_path=request.audit_path,
    )
    if inspect.isawaitable(result):
        result = await result
    return _as_execution_result(result)


def _node_progress_callback(
    progress_callback: Any,
    request: AgentExecutionRequest,
    runner_name: str,
) -> Any:
    """Attach activation identity to provider progress and heartbeat events."""

    if progress_callback is None:
        return None

    async def report(event: dict[str, Any]) -> None:
        enriched = {
            **event,
            "node_id": request.node_id,
            "role": request.role,
            "runner": runner_name,
            "model": request.model,
            "attempt": request.attempt,
        }
        result = progress_callback(enriched)
        if inspect.isawaitable(result):
            await result

    return report


def _runner_for_node(
    selected: Any,
    runner_name: str,
) -> Any:
    """Resolve one server-selected runner, or use an explicit test override."""

    if selected is None:
        return runner_registry.resolve(runner_name)
    if isinstance(selected, RunnerRegistry):
        return selected.resolve(runner_name)
    # Duck-typed registries are useful in integration tests without requiring
    # callers to import the concrete registry implementation.
    if callable(getattr(selected, "resolve", None)) and not callable(getattr(selected, "run", None)):
        return selected.resolve(runner_name)
    return selected


def _attempt_audit_path(workspace: Path, node_id: str, attempt: int) -> Path:
    return workspace / "work" / "agents" / node_id / f"attempt-{attempt}.json"


def _stage_skills(adapter: Any, stage: str, staged: Sequence[str]) -> tuple[str, ...]:
    """Resolve the adapter's allowlisted skill set for one activation."""

    configured = getattr(adapter, f"{stage}_skills", ())
    if stage in {"extraction", "planner"}:
        # Both stages get an explicit, narrow skill set rather than falling
        # back to every staged skill: extraction is the only stage allowed to
        # open sources, and the planner needs no skill at all -- it only reads
        # the frozen evidence store through its sandbox grant.
        return tuple(configured)
    return tuple(configured or staged)


def _stage_hidden_paths(adapter: Any, stage: str, workspace: Path) -> tuple[Path, ...]:
    """Ask the adapter which source/history paths a stage must not inspect."""

    hook = getattr(adapter, "stage_hidden_paths", None)
    if hook is None:
        return ()
    try:
        values = hook(stage, workspace)
    except TypeError:
        values = hook(workspace, stage)
    return tuple(Path(item) for item in (values or ()))


def _stage_read_only_paths(adapter: Any, stage: str, workspace: Path) -> tuple[Path, ...]:
    """Resolve immutable artifacts that must stay readable but never writable."""

    hook = getattr(adapter, "stage_read_only_paths", None)
    if hook is None:
        return ()
    try:
        values = hook(stage, workspace)
    except TypeError:
        values = hook(workspace, stage)
    return tuple(Path(item) for item in (values or ()))


def _stage_writable_paths(adapter: Any, stage: str, workspace: Path) -> tuple[Path, ...]:
    """Resolve the narrow writable surface for an isolated activation."""

    hook = getattr(adapter, "stage_writable_paths", None)
    if hook is None:
        return ()
    try:
        values = hook(stage, workspace)
    except TypeError:
        values = hook(workspace, stage)
    return tuple(Path(item) for item in (values or ()))


def _stage_sandbox(adapter: Any, stage: str) -> Any:
    """Keep existing workflows compatible unless they opt into stage isolation."""

    if not bool(getattr(adapter, "stage_isolation", False)):
        return Sandbox.full_access
    # The planner never writes to the workspace -- it returns its proposal as
    # typed output, the same reason the reviewer is read-only.
    return Sandbox.read_only if stage in {"reviewer", "planner"} else Sandbox.workspace_write


async def _execute_extraction(
    *,
    run_id: str,
    adapter: Any,
    value: Any,
    workspace: Path,
    staged: list[str],
    selected_runner: Any,
    progress_callback: Any,
    event_callback: Any = None,
) -> AgentExecutionResult | None:
    """Run source extraction exactly once, then freeze its EvidenceStore."""

    skills = _stage_skills(adapter, "extraction", staged)
    if not skills:
        return None
    await _emit_phase(progress_callback, AgentPhase.EXTRACTING)
    prompt_hook = getattr(adapter, "build_extraction_prompt", None)
    prompt = str(await _call(prompt_hook, value, workspace)) if prompt_hook else (
        "Extract every staged source document into work/extracted/. Return only "
        "after the extraction artifacts have been written and validated."
    )
    runner_name = str(getattr(adapter, "extraction_runner", getattr(adapter, "author_runner", "codex")))
    runner = _runner_for_node(selected_runner, runner_name)
    request = AgentExecutionRequest(
        run_id=run_id,
        node_id="extraction",
        role=str(getattr(adapter, "extraction_role", "source_document_extractor")),
        attempt=1,
        model=getattr(adapter, "extraction_model", None) or getattr(adapter, "author_model", None),
        reasoning_effort=getattr(adapter, "extraction_reasoning_effort", None) or getattr(adapter, "author_reasoning_effort", None),
        workspace=workspace,
        prompt=prompt,
        sandbox=_stage_sandbox(adapter, "extraction"),
        skill_names=skills,
        audit_path=_attempt_audit_path(workspace, "extraction", 1),
        hidden_paths=_stage_hidden_paths(adapter, "extraction", workspace),
        read_only_paths=_stage_read_only_paths(adapter, "extraction", workspace),
        writable_paths=_stage_writable_paths(adapter, "extraction", workspace),
        restrict_workspace=bool(getattr(adapter, "stage_isolation", False)),
    )
    await _emit_event(
        event_callback,
        "node_started",
        node_id="extraction",
        role=request.role,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        runner=runner_name,
        attempt=1,
        status="running",
        message="Source extraction agent started.",
    )
    started = asyncio.get_running_loop().time()
    try:
        result = await _run_agent(
            runner,
            request,
            progress_callback=_node_progress_callback(progress_callback, request, runner_name),
        )
        finalize = getattr(adapter, "post_extraction", None) or getattr(adapter, "consolidate_extraction", None)
        if finalize is None:
            raise WorkflowExecutionError("extraction consolidation hook is missing")
        await _call(finalize, value, workspace)
    except Exception:
        await _emit_event(
            event_callback,
            "node_failed",
            node_id="extraction",
            role=request.role,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
            runner=runner_name,
            attempt=1,
            status="failed",
            message="Source extraction failed.",
            duration_ms=round((asyncio.get_running_loop().time() - started) * 1000),
        )
        raise
    await _emit_event(
        event_callback,
        "node_completed",
        node_id="extraction",
        role=request.role,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        runner=runner_name,
        attempt=1,
        status="completed",
        provider_run_id=result.provider_run_id,
        message="Source extraction was consolidated into the frozen evidence store.",
        duration_ms=result.duration_ms or round((asyncio.get_running_loop().time() - started) * 1000),
    )
    return result


async def _execute_planning(
    *,
    run_id: str,
    adapter: Any,
    value: Any,
    workspace: Path,
    staged: list[str],
    selected_runner: Any,
    progress_callback: Any,
    event_callback: Any = None,
) -> None:
    """Run the planner exactly once and hand its typed output to the adapter.

    Stage 5 (docs/agents-sdk-migration-plan.md): placement is extraction ->
    planning -> author, because an outline must be grounded in already-frozen
    evidence. This mirrors ``_execute_extraction``'s shape deliberately: one
    request, one runner activation, adapter-owned finalization
    (``post_planning`` here plays the role ``post_extraction`` plays there).
    Persistence of the resulting revision is entirely the adapter's concern --
    this module stays ignorant of any concrete outline schema -- so the only
    thing the caller does after this returns is transition the workflow to
    ``AgentPhase.AWAITING_OUTLINE`` and pause.
    """

    role = str(getattr(adapter, "planner_role", "") or "")
    if not role:
        return None
    await _emit_phase(progress_callback, AgentPhase.PLANNING)
    prompt_hook = getattr(adapter, "build_planning_prompt", None)
    prompt = str(await _call(prompt_hook, value, workspace)) if prompt_hook else str(value)
    runner_name = str(getattr(adapter, "planner_runner", "codex"))
    runner = _runner_for_node(selected_runner, runner_name)
    request = AgentExecutionRequest(
        run_id=run_id,
        node_id="planning",
        role=role,
        attempt=1,
        model=getattr(adapter, "planner_model", None),
        reasoning_effort=getattr(adapter, "planner_reasoning_effort", None),
        workspace=workspace,
        prompt=prompt,
        sandbox=_stage_sandbox(adapter, "planner"),
        output_type=getattr(adapter, "planner_output_type", None),
        skill_names=_stage_skills(adapter, "planner", staged),
        audit_path=_attempt_audit_path(workspace, "planning", 1),
        hidden_paths=_stage_hidden_paths(adapter, "planner", workspace),
        read_only_paths=_stage_read_only_paths(adapter, "planner", workspace),
        writable_paths=_stage_writable_paths(adapter, "planner", workspace),
        restrict_workspace=bool(getattr(adapter, "stage_isolation", False)),
    )
    await _emit_event(
        event_callback,
        "node_started",
        node_id="planning",
        role=request.role,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        runner=runner_name,
        attempt=1,
        status="running",
        message="Planning agent started.",
    )
    started = asyncio.get_running_loop().time()
    try:
        result = await _run_agent(
            runner,
            request,
            progress_callback=_node_progress_callback(progress_callback, request, runner_name),
        )
        finalize = getattr(adapter, "post_planning", None)
        if finalize is None:
            raise WorkflowExecutionError("planning finalization hook is missing")
        await _call(finalize, value, result, workspace)
    except Exception:
        await _emit_event(
            event_callback,
            "node_failed",
            node_id="planning",
            role=request.role,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
            runner=runner_name,
            attempt=1,
            status="failed",
            message="Planning failed.",
            duration_ms=round((asyncio.get_running_loop().time() - started) * 1000),
        )
        raise
    await _emit_event(
        event_callback,
        "node_completed",
        node_id="planning",
        role=request.role,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        runner=runner_name,
        attempt=1,
        status="completed",
        provider_run_id=result.provider_run_id,
        message="Planning revision 1 was persisted for human review.",
        duration_ms=result.duration_ms or round((asyncio.get_running_loop().time() - started) * 1000),
    )
    return None


async def _execute_bounded_agents(
    *,
    run_id: str,
    adapter: Any,
    value: Any,
    workspace: Path,
    staged: list[str],
    review_context: Any,
    initial_prompt: str,
    selected_runner: Any,
    progress_callback: Any,
    event_callback: Any = None,
) -> AgentExecutionResult:
    """Run the server-defined author/validator/reviewer state machine.

    Each author attempt and each reviewer activation calls ``AgentRunner``
    separately.  This is the key isolation boundary: a reviewer cannot mutate
    the author's provider session, and a correction never inherits hidden
    conversational state from an earlier attempt.
    """

    max_rounds = max(1, int(getattr(adapter, "max_author_attempts", getattr(adapter, "max_review_rounds", 5))))
    stagnation_limit = max(1, int(getattr(adapter, "review_stagnation_limit", 2)))
    author_runner_name = str(getattr(adapter, "author_runner", "codex"))
    reviewer_runner_name = str(getattr(adapter, "reviewer_runner", author_runner_name))
    author_model = getattr(adapter, "author_model", None)
    reviewer_model = getattr(adapter, "reviewer_model", None)
    author_reasoning_effort = getattr(adapter, "author_reasoning_effort", None)
    reviewer_reasoning_effort = getattr(adapter, "reviewer_reasoning_effort", None)
    author_role = str(getattr(adapter, "author_role", "presentation_author"))
    reviewer_role = str(getattr(adapter, "reviewer_role", "presentation_reviewer"))
    author_skills = _stage_skills(adapter, "author", staged)
    reviewer_skills = _stage_skills(adapter, "reviewer", staged)
    independent_review = bool(getattr(adapter, "independent_semantic_review", False))
    author_attempt = 1
    reviewer_attempt = 0
    prompt = str(initial_prompt)
    author_result: AgentExecutionResult | None = None
    feedback: str | None = None
    prior_revision_feedback: list[str] = []
    previous_review: ReviewOutcome | None = None
    stagnant_transitions = 0

    async def build_revision_prompt(latest_feedback: str, *, source: str, completed_attempt: int) -> str:
        """Retain prior blockers only for adapters that opt into cross-attempt context."""

        preserve_history = bool(getattr(adapter, "preserve_revision_feedback_history", False))
        history_kwargs = (
            {"prior_revision_feedback": tuple(prior_revision_feedback)} if preserve_history else {}
        )
        revised_prompt = str(
            await _call(
                adapter.build_prompt,
                value,
                workspace,
                semantic_review_context=review_context,
                revision_feedback=latest_feedback,
                **history_kwargs,
            )
        )
        if preserve_history:
            prior_revision_feedback.append(
                f"After author attempt {completed_attempt} ({source}):\n{latest_feedback}"
            )
        return revised_prompt

    while author_attempt <= max_rounds:
        await _emit_phase(progress_callback, AgentPhase.DRAFTING if author_attempt == 1 else AgentPhase.REVISING)
        author_request = AgentExecutionRequest(
            run_id=run_id,
            node_id="author",
            role=author_role,
            attempt=author_attempt,
            model=author_model,
            reasoning_effort=author_reasoning_effort,
            workspace=workspace,
            prompt=prompt,
            sandbox=_stage_sandbox(adapter, "author"),
            skill_names=author_skills,
            audit_path=_attempt_audit_path(workspace, "author", author_attempt),
            hidden_paths=_stage_hidden_paths(adapter, "author", workspace),
            read_only_paths=_stage_read_only_paths(adapter, "author", workspace),
            writable_paths=_stage_writable_paths(adapter, "author", workspace),
            restrict_workspace=bool(getattr(adapter, "stage_isolation", False)),
        )
        author_runner = _runner_for_node(selected_runner, author_runner_name)
        await _emit_event(
            event_callback,
            "node_started",
            node_id="author",
            role=author_role,
            model=author_model,
            reasoning_effort=author_reasoning_effort,
            runner=author_runner_name,
            attempt=author_attempt,
            status="running",
            message="Author agent started.",
        )
        author_started = asyncio.get_running_loop().time()
        try:
            author_result = await _run_agent(
                author_runner,
                author_request,
                progress_callback=_node_progress_callback(progress_callback, author_request, author_runner_name),
            )
        except Exception as exc:
            await _emit_event(
                event_callback,
                "node_failed",
                node_id="author",
                role=author_role,
                model=author_model,
                reasoning_effort=author_reasoning_effort,
                runner=author_runner_name,
                attempt=author_attempt,
                status="failed",
                message="Author agent failed.",
                duration_ms=round((asyncio.get_running_loop().time() - author_started) * 1000),
            )
            raise
        try:
            await _check_author_completion(adapter, value, author_result, workspace)
        except Exception as exc:
            await _emit_event(
                event_callback,
                "node_failed",
                node_id="author",
                role=author_role,
                model=author_model,
                reasoning_effort=author_reasoning_effort,
                runner=author_runner_name,
                attempt=author_attempt,
                status="failed",
                message="Author output check failed.",
                duration_ms=round((asyncio.get_running_loop().time() - author_started) * 1000),
            )
            raise WorkflowExecutionError("author output check failed") from exc
        await _emit_event(
            event_callback,
            "node_completed",
            node_id="author",
            role=author_role,
            model=author_model,
            reasoning_effort=author_reasoning_effort,
            runner=author_runner_name,
            attempt=author_attempt,
            status="completed",
            provider_run_id=author_result.provider_run_id,
            message="Author agent completed.",
            duration_ms=author_result.duration_ms or round((asyncio.get_running_loop().time() - author_started) * 1000),
        )

        await _emit_phase(progress_callback, AgentPhase.VALIDATING)
        validator_started = asyncio.get_running_loop().time()
        await _emit_event(
            event_callback,
            "node_started",
            node_id="validator",
            role="deterministic_validator",
            runner="system",
            attempt=author_attempt,
            status="running",
            message="Deterministic validation started.",
        )
        validate_generated = getattr(adapter, "validate_generated", None)
        if validate_generated is not None:
            try:
                validation = await _call(validate_generated, value, workspace)
                if validation is False:
                    raise DeterministicValidationError("deterministic validation failed")
            except DeterministicValidationError as exc:
                if author_attempt >= max_rounds:
                    await _emit_event(
                        event_callback,
                        "node_failed",
                        node_id="validator",
                        role="deterministic_validator",
                        runner="system",
                        attempt=author_attempt,
                        status="failed",
                        message=_validation_telemetry(exc),
                        duration_ms=round((asyncio.get_running_loop().time() - validator_started) * 1000),
                    )
                    raise WorkflowExecutionError("maximum author attempts exceeded") from exc
                feedback = str(exc).strip() or "deterministic validation failed"
                await _emit_event(
                    event_callback,
                    "node_completed",
                    node_id="validator",
                    role="deterministic_validator",
                    runner="system",
                    attempt=author_attempt,
                    status="completed",
                    message="Deterministic validation returned correctable findings.",
                    duration_ms=round((asyncio.get_running_loop().time() - validator_started) * 1000),
                )
                prompt = await build_revision_prompt(
                    feedback, source="validator", completed_attempt=author_attempt
                )
                author_attempt += 1
                continue
            except ValidationInfrastructureError as exc:
                await _emit_event(
                    event_callback,
                    "node_failed",
                    node_id="validator",
                    role="deterministic_validator",
                    runner="system",
                    attempt=author_attempt,
                    status="failed",
                    message=_validation_telemetry(exc),
                    metadata={"error_code": ",".join(exc.diagnostic_codes)},
                    duration_ms=round((asyncio.get_running_loop().time() - validator_started) * 1000),
                )
                raise
            except Exception as exc:
                await _emit_event(
                    event_callback,
                    "node_failed",
                    node_id="validator",
                    role="deterministic_validator",
                    runner="system",
                    attempt=author_attempt,
                    status="failed",
                    message="Deterministic validator failed.",
                    duration_ms=round((asyncio.get_running_loop().time() - validator_started) * 1000),
                )
                raise WorkflowExecutionError("deterministic validator failed") from exc
        await _emit_event(
            event_callback,
            "node_completed",
            node_id="validator",
            role="deterministic_validator",
            runner="system",
            attempt=author_attempt,
            status="completed",
            message="Deterministic validation passed.",
            duration_ms=round((asyncio.get_running_loop().time() - validator_started) * 1000),
        )

        await _emit_phase(progress_callback, AgentPhase.REVIEWING)
        reviewer_attempt += 1
        review_prompt = await _build_review_prompt(
            adapter,
            value,
            workspace,
            author_result.last_audit or author_result,
            review_context,
            None if independent_review else previous_review,
        )
        review_request = AgentExecutionRequest(
            run_id=run_id,
            node_id="reviewer",
            role=reviewer_role,
            attempt=reviewer_attempt,
            model=reviewer_model,
            reasoning_effort=reviewer_reasoning_effort,
            workspace=workspace,
            prompt=str(review_prompt),
            sandbox=_stage_sandbox(adapter, "reviewer"),
            # The reviewer always requests the provider-neutral typed output
            # (docs/agents-sdk-migration-plan.md Stage 2): the selected runner
            # -- Codex or the Agents SDK -- hands back an already-validated
            # ``ReviewOutcome`` on ``AgentExecutionResult.output`` rather than
            # text this coordinator would have to parse itself.
            output_type=ReviewOutcome,
            skill_names=reviewer_skills,
            audit_path=_attempt_audit_path(workspace, "reviewer", reviewer_attempt),
            hidden_paths=_stage_hidden_paths(adapter, "reviewer", workspace),
            read_only_paths=_stage_read_only_paths(adapter, "reviewer", workspace),
            writable_paths=_stage_writable_paths(adapter, "reviewer", workspace),
            restrict_workspace=bool(getattr(adapter, "stage_isolation", False)),
        )
        reviewer_runner = _runner_for_node(selected_runner, reviewer_runner_name)
        await _emit_event(
            event_callback,
            "node_started",
            node_id="reviewer",
            role=reviewer_role,
            model=reviewer_model,
            reasoning_effort=reviewer_reasoning_effort,
            runner=reviewer_runner_name,
            attempt=reviewer_attempt,
            status="running",
            message="Reviewer agent started.",
        )
        reviewer_started = asyncio.get_running_loop().time()
        try:
            review_result = await _run_agent(
                reviewer_runner,
                review_request,
                progress_callback=_node_progress_callback(progress_callback, review_request, reviewer_runner_name),
            )
        except Exception as exc:
            await _emit_event(
                event_callback,
                "node_failed",
                node_id="reviewer",
                role=reviewer_role,
                model=reviewer_model,
                reasoning_effort=reviewer_reasoning_effort,
                runner=reviewer_runner_name,
                attempt=reviewer_attempt,
                status="failed",
                message=safe_error(str(exc), "Reviewer agent failed."),
                duration_ms=round((asyncio.get_running_loop().time() - reviewer_started) * 1000),
            )
            raise
        try:
            continuity = None if independent_review else previous_review
            if not isinstance(review_result.output, ReviewOutcome):
                # Both runners are required to populate ``output`` for a
                # typed request (see ``output_type=ReviewOutcome`` above); a
                # runner that returns free-form text instead is a defect in
                # that runner, not a correctable reviewer finding.
                raise WorkflowExecutionError("reviewer runner did not return a validated review outcome")
            review = normalize_review_outcome(review_result.output, continuity, attempt=reviewer_attempt)
            evaluation = evaluate_review(
                review,
                continuity,
                attempt=author_attempt,
                max_attempts=max_rounds,
                stagnant_transitions=stagnant_transitions,
                # Slides reviews are independent observations of the current
                # candidate.  Repeated blockers still consume attempts, but
                # never trip a semantic stagnation shortcut.
                stagnation_limit=(10**9 if independent_review else stagnation_limit),
            )
        except Exception as exc:
            await _emit_event(
                event_callback,
                "node_failed",
                node_id="reviewer",
                role=reviewer_role,
                model=reviewer_model,
                reasoning_effort=reviewer_reasoning_effort,
                runner=reviewer_runner_name,
                attempt=reviewer_attempt,
                status="failed",
                message=safe_error(str(exc), "Reviewer response was invalid."),
                duration_ms=round((asyncio.get_running_loop().time() - reviewer_started) * 1000),
            )
            raise
        review_count = evaluation.blocking_count
        await _emit_event(
            event_callback,
            "node_completed",
            node_id="reviewer",
            role=reviewer_role,
            model=reviewer_model,
            reasoning_effort=reviewer_reasoning_effort,
            runner=reviewer_runner_name,
            attempt=reviewer_attempt,
            status="completed",
            provider_run_id=review_result.provider_run_id,
            message=("Reviewer approved publication." if evaluation.decision is ReviewDecision.PUBLISH else _blocking_message(review_count)),
            metadata={
                "blocking_count": evaluation.blocking_count,
                "advisory_count": evaluation.advisory_count,
                "resolved_count": evaluation.resolved_count,
                "new_count": evaluation.new_count,
                "persistent_count": evaluation.persistent_count,
                "stagnant_transitions": evaluation.stagnant_transitions,
                "decision": evaluation.decision.value,
            },
            duration_ms=review_result.duration_ms or round((asyncio.get_running_loop().time() - reviewer_started) * 1000),
        )
        await _persist_review_history(workspace, reviewer_attempt, review, evaluation)
        if not independent_review:
            previous_review = review
            stagnant_transitions = evaluation.stagnant_transitions
        if evaluation.decision is ReviewDecision.PUBLISH:
            return author_result
        if evaluation.decision in {ReviewDecision.REJECT_MAX_ATTEMPTS, ReviewDecision.REJECT_STAGNATED}:
            failure_message = (
                "Semantic review stagnated; no prior blockers were resolved."
                if evaluation.decision is ReviewDecision.REJECT_STAGNATED
                else "Maximum revision attempts reached."
            )
            await _emit_phase(progress_callback, AgentPhase.FAILED, failure_message)
            raise SemanticReviewRejected(review_count, reason=evaluation.decision)
        feedback_hook = getattr(adapter, "revision_feedback", None) or getattr(adapter, "build_revision_feedback", None)
        feedback = await _call(feedback_hook, value, review, author_result) if feedback_hook is not None else str(review)
        # Do not silently discard findings when the reviewer returned a large
        # structured response. The adapter may provide a JSON file path or a
        # compact string; the full value is passed through unchanged.
        feedback = str(feedback)
        await _emit_phase(progress_callback, AgentPhase.REVISING)
        prompt = await build_revision_prompt(
            feedback, source="reviewer", completed_attempt=author_attempt
        )
        author_attempt += 1

    raise WorkflowExecutionError("maximum author attempts exceeded")


async def _execute_workflow(
    payload: AgentTaskPayload,
    *,
    started_at: datetime,
    registry: WorkflowRegistry = workflow_registry,
    runner: Any = None,
    workspace_root: Path = Path("/tmp/agentic/jobs"),
    skills_root: Path | None = None,
    progress_callback: Any = None,
    event_callback: Any = None,
) -> AgentTaskResult:
    """Execute one allowlisted workflow and always return a safe result."""

    started = started_at
    adapter: Any = None
    value: Any = None
    workspace: Path | None = None
    success = False
    # Set only on the Stage 5 human-in-the-loop pause: the workspace (frozen
    # evidence, and soon a pending outline revision) must survive for the
    # eventual ``resume_from="author"`` re-entry, so the ordinary success/failure
    # cleanup in ``finally`` below must not run for it.
    paused = False
    try:
        payload = payload if isinstance(payload, AgentTaskPayload) else AgentTaskPayload.model_validate(payload)
        await _emit_phase(progress_callback, AgentPhase.PREPARING)
        adapter = registry.resolve(payload.workflow)
        value = await _call(adapter.validate_input, payload.input)
        workspace_root = Path(workspace_root).resolve()
        await asyncio.to_thread(workspace_root.mkdir, parents=True, exist_ok=True)
        candidate = workspace_root / str(payload.job_id)
        # AgentTaskPayload disallows separators, but this second containment
        # check protects callers that construct models in custom ways.
        if candidate.is_symlink():
            raise WorkflowExecutionError("workflow workspace is invalid")
        await asyncio.to_thread(candidate.mkdir, parents=False, exist_ok=True)
        resolved_workspace = candidate.resolve(strict=True)
        try:
            resolved_workspace.relative_to(workspace_root)
        except ValueError as exc:
            raise WorkflowExecutionError("workflow workspace is invalid") from exc
        workspace = resolved_workspace
        deterministic = getattr(adapter, "deterministic_validate", None) or getattr(adapter, "validate_deterministically", None)
        if deterministic is not None:
            await _call(deterministic, value, workspace)
        await _call(adapter.prepare_workspace, value, workspace)
        await _call(adapter.prepare_input, value, workspace)
        staged = await asyncio.to_thread(
            stage_declared_skills,
            workspace,
            getattr(adapter, "declared_skills", ()),
            skills_root=skills_root,
        )
        selected_runner = runner
        # The default registry and all provider-neutral implementations use
        # one request per logical activation.  Legacy test doubles are kept on
        # the compatibility callback path below.
        modern_runner = (
            selected_runner is None
            or isinstance(selected_runner, RunnerRegistry)
            or bool(getattr(adapter, "extraction_skills", ()))
            or bool(getattr(adapter, "planner_role", None))
        )
        if selected_runner is not None and not isinstance(selected_runner, RunnerRegistry):
            if callable(getattr(selected_runner, "resolve", None)) and not callable(getattr(selected_runner, "run", None)):
                modern_runner = True
            else:
                modern_runner = isinstance(selected_runner, CodexRunner) or _uses_execution_request(selected_runner)

        # ``resume_from`` is set only on re-entry after a human has approved an
        # outline (Stage 5).  Extraction and planning already ran -- and their
        # outputs are already frozen on disk -- the first time this job's
        # workspace was prepared, so both are skipped here.
        resuming = payload.resume_from is not None

        # Extraction is a distinct, one-time activation.  It is intentionally
        # completed before the author prompt is built so author revisions can
        # never cause a source document to be reinterpreted.
        if not resuming and getattr(adapter, "extraction_skills", ()):
            await _execute_extraction(
                run_id=str(payload.job_id),
                adapter=adapter,
                value=value,
                workspace=workspace,
                staged=staged,
                selected_runner=selected_runner,
                progress_callback=progress_callback,
                event_callback=event_callback,
            )

        # Planning sits between extraction and authoring: the outline must be
        # grounded in already-frozen evidence, so it cannot run any earlier.
        # A declared planner always pauses after its first revision -- there is
        # no author-style retry loop here, and no adapter opts out per run.
        if not resuming and getattr(adapter, "planner_role", None):
            await _execute_planning(
                run_id=str(payload.job_id),
                adapter=adapter,
                value=value,
                workspace=workspace,
                staged=staged,
                selected_runner=selected_runner,
                progress_callback=progress_callback,
                event_callback=event_callback,
            )
            await _emit_phase(progress_callback, AgentPhase.AWAITING_OUTLINE)
            paused = True
            # A non-terminal result: ``status`` stays RUNNING (WorkflowStatus has
            # no AWAITING_INPUT of its own -- that distinction belongs to
            # ``JobStatus`` at the durable-job boundary) and ``phase`` carries the
            # pause.  ``app/tasks/agents.py`` is responsible for recognizing this
            # combination and releasing the worker's lease without marking the
            # job failed or retried.
            return AgentTaskResult(
                job_id=payload.job_id,
                workflow=payload.workflow,
                status=WorkflowStatus.RUNNING,
                phase=AgentPhase.AWAITING_OUTLINE,
                output=None,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                error=None,
            )

        if resuming:
            outline_path = workspace / "work" / "outline.json"
            if not await asyncio.to_thread(outline_path.is_file):
                raise WorkflowExecutionError(
                    "workflow resume was requested before an approved outline was written"
                )

        context_hook = getattr(adapter, "semantic_review_context", None) or getattr(adapter, "build_semantic_review_context", None)
        review_context = await _call(context_hook, value, workspace) if context_hook is not None else None
        prompt = await _call(adapter.build_prompt, value, workspace, semantic_review_context=review_context)
        turns = [TurnRequest(kind="initial", prompt=prompt, sandbox=Sandbox.workspace_write)]
        build_review = getattr(adapter, "build_review_prompt", None)
        if build_review is not None and modern_runner:
            run = await _execute_bounded_agents(
                run_id=str(payload.job_id),
                adapter=adapter,
                value=value,
                workspace=workspace,
                staged=staged,
                review_context=review_context,
                initial_prompt=str(prompt),
                selected_runner=selected_runner,
                progress_callback=progress_callback,
                event_callback=event_callback,
            )
        elif build_review is None and modern_runner:
            await _emit_phase(progress_callback, AgentPhase.DRAFTING)
            author_runner_name = str(getattr(adapter, "author_runner", "codex"))
            author_runner = _runner_for_node(selected_runner, author_runner_name)
            request = AgentExecutionRequest(
                run_id=str(payload.job_id),
                node_id="author",
                role=str(getattr(adapter, "author_role", "presentation_author")),
                attempt=1,
                model=getattr(adapter, "author_model", None),
                reasoning_effort=getattr(adapter, "author_reasoning_effort", None),
                workspace=workspace,
                prompt=str(prompt),
                sandbox=_stage_sandbox(adapter, "author"),
                skill_names=_stage_skills(adapter, "author", staged),
                audit_path=_attempt_audit_path(workspace, "author", 1),
                hidden_paths=_stage_hidden_paths(adapter, "author", workspace),
                read_only_paths=_stage_read_only_paths(adapter, "author", workspace),
                writable_paths=_stage_writable_paths(adapter, "author", workspace),
                restrict_workspace=bool(getattr(adapter, "stage_isolation", False)),
            )
            author_role = str(getattr(adapter, "author_role", "presentation_author"))
            await _emit_event(
                event_callback,
                "node_started",
                node_id="author",
                role=author_role,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                runner=author_runner_name,
                attempt=1,
                status="running",
                message="Author agent started.",
            )
            author_started = asyncio.get_running_loop().time()
            try:
                run = await _run_agent(
                    author_runner,
                    request,
                    progress_callback=_node_progress_callback(progress_callback, request, author_runner_name),
                )
            except Exception:
                await _emit_event(
                    event_callback,
                    "node_failed",
                    node_id="author",
                    role=author_role,
                    model=request.model,
                    reasoning_effort=request.reasoning_effort,
                    runner=author_runner_name,
                    attempt=1,
                    status="failed",
                    message="Author agent failed.",
                    duration_ms=round((asyncio.get_running_loop().time() - author_started) * 1000),
                )
                raise
            try:
                await _check_author_completion(adapter, value, run, workspace)
            except Exception as exc:
                await _emit_event(
                    event_callback,
                    "node_failed",
                    node_id="author",
                    role=author_role,
                    model=request.model,
                    reasoning_effort=request.reasoning_effort,
                    runner=author_runner_name,
                    attempt=1,
                    status="failed",
                    message="Author output check failed.",
                    duration_ms=round((asyncio.get_running_loop().time() - author_started) * 1000),
                )
                raise WorkflowExecutionError("author output check failed") from exc
            await _emit_event(
                event_callback,
                "node_completed",
                node_id="author",
                role=author_role,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                runner=author_runner_name,
                attempt=1,
                status="completed",
                provider_run_id=run.provider_run_id,
                message="Author agent completed.",
                duration_ms=run.duration_ms or round((asyncio.get_running_loop().time() - author_started) * 1000),
            )
        elif build_review is not None:
            execution_runner = runner or CodexRunner()
            max_rounds = max(1, int(getattr(adapter, "max_author_attempts", getattr(adapter, "max_review_rounds", 5))))
            stagnation_limit = max(1, int(getattr(adapter, "review_stagnation_limit", 2)))
            # A round consists of one write-capable generation/correction turn,
            # its deterministic validation, and (when valid) its semantic
            # review.  The initial generation is round one.
            review_round = 1
            reviewer_attempt = 0
            previous_review: ReviewOutcome | None = None
            stagnant_transitions = 0
            pending_feedback: str | None = None

            async def correction(feedback: str) -> TurnRequest:
                nonlocal review_round, pending_feedback
                if review_round >= max_rounds:
                    raise WorkflowExecutionError("maximum author attempts exceeded")
                review_round += 1
                pending_feedback = str(feedback)
                await _emit_phase(progress_callback, AgentPhase.REVISING)
                revision_prompt = await _call(
                    adapter.build_prompt,
                    value,
                    workspace,
                    semantic_review_context=review_context,
                    revision_feedback=pending_feedback,
                )
                return TurnRequest(
                    kind="correction",
                    prompt=str(revision_prompt),
                    sandbox=Sandbox.full_access,
                )

            async def next_turn(audit: Any, response: str | None) -> TurnRequest | None:
                nonlocal previous_review, stagnant_transitions, reviewer_attempt
                if audit.kind in {"initial", "correction"}:
                    await _emit_phase(progress_callback, AgentPhase.VALIDATING)
                    validate_generated = getattr(adapter, "validate_generated", None)
                    if validate_generated is not None:
                        try:
                            validation = await _call(validate_generated, value, workspace)
                            if validation is False:
                                raise DeterministicValidationError("deterministic validation failed")
                        except DeterministicValidationError as exc:
                            # Deterministic failures skip semantic review for
                            # this round and go directly to correction while
                            # author attempts remain.
                            details = str(exc).strip() or "deterministic validation failed"
                            if pending_feedback:
                                details = f"{pending_feedback}\n\nDeterministic validator findings:\n{details}"
                            return await correction(details)
                        except ValidationInfrastructureError as exc:
                            await _emit_event(
                                event_callback,
                                "node_failed",
                                node_id="validator",
                                role="deterministic_validator",
                                runner="system",
                                attempt=review_round,
                                status="failed",
                                message=_validation_telemetry(exc),
                                metadata={"error_code": ",".join(exc.diagnostic_codes)},
                            )
                            raise
                        except Exception as exc:
                            # A validator crash is an operational failure, not
                            # a user-correctable finding; fail closed without
                            # attempting another model turn.
                            raise WorkflowExecutionError("deterministic validator failed") from exc
                    await _emit_phase(progress_callback, AgentPhase.REVIEWING)
                    review_prompt = await _build_review_prompt(
                        adapter,
                        value,
                        workspace,
                        audit,
                        review_context,
                        previous_review,
                    )
                    schema = getattr(adapter, "review_output_schema", None)
                    return TurnRequest(kind="review", prompt=str(review_prompt), sandbox=Sandbox.full_access, output_schema=schema)
                if audit.kind != "review":
                    raise WorkflowExecutionError("workflow turn sequence is invalid")
                reviewer_attempt += 1
                # This legacy per-turn-callback path (service.py:1063-1270, removed in
                # Stage 4) predates ``AgentExecutionRequest.output_type`` and only ever
                # gets a raw provider ``output_schema`` here, so it still needs an
                # adapter-supplied parser to turn that text into a ``ReviewOutcome``.
                # The modern node path above no longer needs this dispatch -- it reads
                # an already-validated outcome straight off the runner result -- so it
                # is kept inline for this single remaining caller rather than as a
                # shared helper.
                parser = getattr(adapter, "parse_review", None)
                if parser is None:
                    raise WorkflowExecutionError("semantic review parser is missing")
                try:
                    parser_parameters = inspect.signature(parser).parameters
                except (TypeError, ValueError):
                    parser_parameters = {}
                parser_kwargs = {"previous_review": previous_review} if "previous_review" in parser_parameters else {}
                parsed_review = await _call(parser, response, workspace, **parser_kwargs)
                if not isinstance(parsed_review, ReviewOutcome):
                    raise ValueError("semantic review parser returned an invalid contract")
                review = normalize_review_outcome(parsed_review, previous_review, attempt=reviewer_attempt)
                evaluation = evaluate_review(
                    review,
                    previous_review,
                    attempt=review_round,
                    max_attempts=max_rounds,
                    stagnant_transitions=stagnant_transitions,
                    stagnation_limit=stagnation_limit,
                )
                await _persist_review_history(workspace, reviewer_attempt, review, evaluation)
                previous_review = review
                stagnant_transitions = evaluation.stagnant_transitions
                if evaluation.decision is ReviewDecision.PUBLISH:
                    return None
                if evaluation.decision in {ReviewDecision.REJECT_MAX_ATTEMPTS, ReviewDecision.REJECT_STAGNATED}:
                    failure_message = (
                        "Semantic review stagnated; no prior blockers were resolved."
                        if evaluation.decision is ReviewDecision.REJECT_STAGNATED
                        else "Maximum revision attempts reached."
                    )
                    await _emit_phase(progress_callback, AgentPhase.FAILED, failure_message)
                    raise SemanticReviewRejected(evaluation.blocking_count, reason=evaluation.decision)
                feedback_hook = getattr(adapter, "revision_feedback", None) or getattr(adapter, "build_revision_feedback", None)
                feedback = await _call(feedback_hook, value, review, audit) if feedback_hook is not None else str(review)
                return await correction(str(feedback))

            await _emit_phase(progress_callback, AgentPhase.DRAFTING)
            run = await execution_runner.run(workspace, turns, skill_names=staged, progress_callback=progress_callback, audit_path=workspace / "work" / "codex_result.json", turn_callback=next_turn)
        else:
            await _emit_phase(progress_callback, AgentPhase.DRAFTING)
            run = await (runner or CodexRunner()).run(workspace, turns, skill_names=staged, progress_callback=progress_callback, audit_path=workspace / "work" / "codex_result.json")
        await _emit_phase(progress_callback, AgentPhase.PUBLISHING)
        output = await _call(adapter.publish, value, run, workspace)
        output = await asyncio.to_thread(_validate_output, adapter, output)
        success = True
        await _emit_phase(progress_callback, AgentPhase.COMPLETED)
        return AgentTaskResult(job_id=payload.job_id, workflow=payload.workflow, status=WorkflowStatus.COMPLETED, phase=AgentPhase.COMPLETED, output=_result_output(output), started_at=started, finished_at=datetime.now(timezone.utc), error=None)
    except WorkflowTimeoutError:
        await _emit_phase(progress_callback, AgentPhase.FAILED, "Agent workflow timed out.")
        return AgentTaskResult(job_id=getattr(payload, "job_id", "unknown"), workflow=getattr(payload, "workflow", "unknown"), status=WorkflowStatus.FAILED, phase=AgentPhase.FAILED, output=None, started_at=started, finished_at=datetime.now(timezone.utc), error="Workflow timed out.")
    except SemanticReviewRejected as exc:
        message = safe_error(str(exc), "Publication rejected by semantic review.")
        await _emit_phase(progress_callback, AgentPhase.FAILED, message)
        return AgentTaskResult(job_id=getattr(payload, "job_id", "unknown"), workflow=getattr(payload, "workflow", "unknown"), status=WorkflowStatus.FAILED, phase=AgentPhase.FAILED, output=None, started_at=started, finished_at=datetime.now(timezone.utc), error=message)
    except ValidationInfrastructureError as exc:
        # Infrastructure findings stop immediately, but their stable codes
        # remain visible in the safe operational result and validator event.
        message = _validation_telemetry(exc)
        await _emit_phase(progress_callback, AgentPhase.FAILED, message)
        return AgentTaskResult(
            job_id=getattr(payload, "job_id", "unknown"),
            workflow=getattr(payload, "workflow", "unknown"),
            status=WorkflowStatus.FAILED,
            phase=AgentPhase.FAILED,
            output=None,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            error=message,
        )
    except WorkflowExecutionError as exc:
        infrastructure = _validation_infrastructure_cause(exc)
        if infrastructure is None:
            logger.exception(
                "Agent workflow failed",
                workflow=getattr(payload, "workflow", "unknown"),
                job_id=str(getattr(payload, "job_id", "unknown")),
            )
            await _emit_phase(progress_callback, AgentPhase.FAILED)
            return AgentTaskResult(
                job_id=getattr(payload, "job_id", "unknown"),
                workflow=getattr(payload, "workflow", "unknown"),
                status=WorkflowStatus.FAILED,
                phase=AgentPhase.FAILED,
                output=None,
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                error="Workflow execution failed.",
            )
        message = _validation_telemetry(infrastructure)
        await _emit_phase(progress_callback, AgentPhase.FAILED, message)
        return AgentTaskResult(
            job_id=getattr(payload, "job_id", "unknown"),
            workflow=getattr(payload, "workflow", "unknown"),
            status=WorkflowStatus.FAILED,
            phase=AgentPhase.FAILED,
            output=None,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            error=message,
        )
    except Exception:
        logger.exception(
            "Agent workflow failed",
            workflow=getattr(payload, "workflow", "unknown"),
            job_id=str(getattr(payload, "job_id", "unknown")),
        )
        await _emit_phase(progress_callback, AgentPhase.FAILED)

        return AgentTaskResult(
            job_id=getattr(payload, "job_id", "unknown"),
            workflow=getattr(payload, "workflow", "unknown"),
            status=WorkflowStatus.FAILED,
            phase=AgentPhase.FAILED,
            output=None,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            error="Workflow execution failed.",
        )
    finally:
        if adapter is not None and workspace is not None and not paused:
            try:
                await _call(adapter.cleanup, value, workspace, success=success)
            except Exception:
                pass


async def execute_workflow(
    payload: AgentTaskPayload,
    *,
    registry: WorkflowRegistry = workflow_registry,
    runner: Any = None,
    workspace_root: Path = Path("/tmp/agentic/jobs"),
    skills_root: Path | None = None,
    progress_callback: Any = None,
    event_callback: Any = None,
    timeout_seconds: float | None = None,
) -> AgentTaskResult:
    """Execute the entire lifecycle under one total workflow deadline."""

    started = datetime.now(timezone.utc)
    deadline = timeout_seconds
    if deadline is None and runner is not None:
        deadline = getattr(runner, "timeout_seconds", None)
    try:
        operation = _execute_workflow(payload, started_at=started, registry=registry, runner=runner, workspace_root=workspace_root, skills_root=skills_root, progress_callback=progress_callback, event_callback=event_callback)
        if deadline is not None:
            return await asyncio.wait_for(operation, timeout=deadline)
        return await operation
    except TimeoutError:
        if isinstance(payload, AgentTaskPayload):
            parsed = payload
        else:
            try:
                parsed = AgentTaskPayload.model_validate(payload)
            except Exception:
                parsed = None
        await _emit_phase(progress_callback, AgentPhase.FAILED, "Agent workflow timed out.")
        return AgentTaskResult(job_id=parsed.job_id if parsed else "unknown", workflow=parsed.workflow if parsed else "unknown", status=WorkflowStatus.FAILED, phase=AgentPhase.FAILED, output=None, started_at=started, finished_at=datetime.now(timezone.utc), error="Workflow timed out.")


from .coordinator import WorkflowCoordinator

__all__ = ["WorkflowCoordinator", "execute_workflow"]
