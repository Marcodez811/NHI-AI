"""Workflow lifecycle orchestration built on the generic Codex runner."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from openai_codex import Sandbox

from .contracts import AgentTaskPayload, AgentTaskResult, DeterministicValidationError, TurnRequest, WorkflowStatus
from .registry import WorkflowRegistry, workflow_registry
from .runner import CodexRunResult, CodexRunner, WorkflowExecutionError, WorkflowTimeoutError
from .staging import stage_declared_skills


async def _call(value: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke a hook without letting synchronous adapter work block asyncio."""

    callable_value = getattr(value, "__call__", value)
    if inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(callable_value):
        return await value(*args, **kwargs)
    return await asyncio.to_thread(value, *args, **kwargs)


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


async def _execute_workflow(
    payload: AgentTaskPayload,
    *,
    registry: WorkflowRegistry = workflow_registry,
    runner: CodexRunner | None = None,
    workspace_root: Path = Path("/tmp/agentic/jobs"),
    skills_root: Path | None = None,
    progress_callback: Any = None,
) -> AgentTaskResult:
    """Execute one allowlisted workflow and always return a safe result."""

    started = datetime.now(timezone.utc)
    adapter: Any = None
    value: Any = None
    workspace: Path | None = None
    success = False
    try:
        payload = payload if isinstance(payload, AgentTaskPayload) else AgentTaskPayload.model_validate(payload)
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
        context_hook = getattr(adapter, "semantic_review_context", None) or getattr(adapter, "build_semantic_review_context", None)
        review_context = await _call(context_hook, value, workspace) if context_hook is not None else None
        prompt = await _call(adapter.build_prompt, value, workspace, semantic_review_context=review_context)
        turns = [TurnRequest(kind="initial", prompt=prompt, sandbox=Sandbox.workspace_write)]
        build_review = getattr(adapter, "build_review_prompt", None)
        if build_review is not None:
            execution_runner = runner or CodexRunner()
            max_rounds = max(1, int(getattr(adapter, "max_review_rounds", 3)))
            # A round consists of one write-capable generation/correction turn,
            # its deterministic validation, and (when valid) its semantic
            # review.  The initial generation is round one.
            review_round = 1
            pending_feedback: str | None = None

            async def correction(feedback: str) -> TurnRequest:
                nonlocal review_round, pending_feedback
                if review_round >= max_rounds:
                    raise WorkflowExecutionError("maximum review rounds exceeded")
                review_round += 1
                pending_feedback = str(feedback)[:12000]
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
                    sandbox=Sandbox.workspace_write,
                )

            async def next_turn(audit: Any, response: str | None) -> TurnRequest | None:
                if audit.kind in {"initial", "correction"}:
                    validate_generated = getattr(adapter, "validate_generated", None)
                    if validate_generated is not None:
                        try:
                            validation = await _call(validate_generated, value, workspace)
                            if validation is False:
                                raise DeterministicValidationError("deterministic validation failed")
                        except (DeterministicValidationError, ValueError) as exc:
                            # Deterministic failures skip semantic review for
                            # this round and go directly to correction while
                            # review rounds remain.
                            details = str(exc).strip() or "deterministic validation failed"
                            if pending_feedback:
                                details = f"{pending_feedback}\n\nDeterministic validator findings:\n{details}"
                            return await correction(details)
                        except Exception as exc:
                            # Domain validators may use a tagged JobError
                            # without depending on the generic package. Treat
                            # those expected findings like the shared error
                            # type; unrelated runtime errors remain terminal.
                            if getattr(exc, "stage", None) in {"verify_output", "validation", "deterministic"}:
                                details = str(exc).strip() or "deterministic validation failed"
                                if pending_feedback:
                                    details = f"{pending_feedback}\n\nDeterministic validator findings:\n{details}"
                                return await correction(details)
                            # A validator crash is an operational failure, not
                            # a user-correctable finding; fail closed without
                            # attempting another model turn.
                            raise WorkflowExecutionError("deterministic validator failed") from exc
                    review_prompt = await _call(build_review, value, workspace, audit, review_context)
                    schema = getattr(adapter, "review_output_schema", None)
                    return TurnRequest(kind="review", prompt=str(review_prompt), sandbox=Sandbox.read_only, output_schema=schema)
                if audit.kind != "review":
                    raise WorkflowExecutionError("workflow turn sequence is invalid")
                parse_review = getattr(adapter, "parse_review", None)
                if parse_review is None:
                    raise WorkflowExecutionError("semantic review parser is missing")
                review = await _call(parse_review, response, workspace)
                blocking_hook = getattr(adapter, "review_has_blocking_findings", None)
                if blocking_hook is not None:
                    blocking = await _call(blocking_hook, review)
                elif isinstance(review, dict):
                    blocking = bool(review.get("blocking_findings"))
                else:
                    blocking = bool(review)
                if not blocking:
                    return None
                feedback_hook = getattr(adapter, "revision_feedback", None) or getattr(adapter, "build_revision_feedback", None)
                feedback = await _call(feedback_hook, value, review, audit) if feedback_hook is not None else str(review)
                return await correction(str(feedback))

            run = await execution_runner.run(workspace, turns, skill_names=staged, progress_callback=progress_callback, audit_path=workspace / "work" / "codex_result.json", turn_callback=next_turn)
        else:
            run = await (runner or CodexRunner()).run(workspace, turns, skill_names=staged, progress_callback=progress_callback, audit_path=workspace / "work" / "codex_result.json")
        output = await _call(adapter.publish, value, run, workspace)
        output = await asyncio.to_thread(_validate_output, adapter, output)
        success = True
        return AgentTaskResult(job_id=payload.job_id, workflow=payload.workflow, status=WorkflowStatus.COMPLETED, output=_result_output(output), started_at=started, finished_at=datetime.now(timezone.utc), error=None)
    except WorkflowTimeoutError:
        return AgentTaskResult(job_id=getattr(payload, "job_id", "unknown"), workflow=getattr(payload, "workflow", "unknown"), status=WorkflowStatus.FAILED, output=None, started_at=started, finished_at=datetime.now(timezone.utc), error="Workflow timed out.")
    except Exception:
        # Details are logged server-side only; callers get a stable sentence.
        return AgentTaskResult(job_id=getattr(payload, "job_id", "unknown"), workflow=getattr(payload, "workflow", "unknown"), status=WorkflowStatus.FAILED, output=None, started_at=started, finished_at=datetime.now(timezone.utc), error="Workflow execution failed.")
    finally:
        if adapter is not None and workspace is not None:
            try:
                await _call(adapter.cleanup, value, workspace, success=success)
            except Exception:
                pass


async def execute_workflow(
    payload: AgentTaskPayload,
    *,
    registry: WorkflowRegistry = workflow_registry,
    runner: CodexRunner | None = None,
    workspace_root: Path = Path("/tmp/agentic/jobs"),
    skills_root: Path | None = None,
    progress_callback: Any = None,
    timeout_seconds: float | None = None,
) -> AgentTaskResult:
    """Execute the entire lifecycle under one total workflow deadline."""

    deadline = timeout_seconds
    if deadline is None and runner is not None:
        deadline = getattr(runner, "timeout_seconds", None)
    try:
        operation = _execute_workflow(payload, registry=registry, runner=runner, workspace_root=workspace_root, skills_root=skills_root, progress_callback=progress_callback)
        if deadline is not None:
            return await asyncio.wait_for(operation, timeout=deadline)
        return await operation
    except TimeoutError:
        started = datetime.now(timezone.utc)
        if isinstance(payload, AgentTaskPayload):
            parsed = payload
        else:
            try:
                parsed = AgentTaskPayload.model_validate(payload)
            except Exception:
                parsed = None
        return AgentTaskResult(job_id=parsed.job_id if parsed else "unknown", workflow=parsed.workflow if parsed else "unknown", status=WorkflowStatus.FAILED, output=None, started_at=started, finished_at=datetime.now(timezone.utc), error="Workflow timed out.")


__all__ = ["execute_workflow"]
