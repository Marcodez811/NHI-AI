"""Taskiq entrypoint for allowlisted generic agentic workflows."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import socket
from typing import Any

import redis.asyncio as redis

from taskiq.depends.progress_tracker import TaskProgress

from app.broker import result_backend, tasks_broker as broker
from app.config import settings
from app.services.agentic import AgentPhase, AgentTaskPayload, AgentTaskResult, WorkflowStatus, workflow_registry
from app.services.agentic.service import execute_workflow
from app.services.agentic.runner import CodexRunner, safe_error
from app.services.agentic.events import AgentEventType, AgentTelemetryStore
from app.services.slides.adapter import slides_adapter  # noqa: F401 - registers the built-in workflow

_SAFE_STAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# TaskIQ workers do not import the FastAPI application lifespan, so they own a
# process-level telemetry client. Redis connections are opened lazily by the
# client and telemetry failures are swallowed by ``_emit_telemetry`` below.
_telemetry_redis = redis.from_url(settings.redis_url)
_telemetry_store = AgentTelemetryStore(
    _telemetry_redis,
    retention_seconds=settings.agent_event_retention_seconds,
)
_worker_id = f"{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _set_progress(job_id: str, *, status: str, stage: str, message: str, started_at: datetime, phase: AgentPhase | str | None = None, finished_at: datetime | None = None) -> None:
    try:
        clean_stage = stage if _SAFE_STAGE.fullmatch(str(stage or "")) else "working"
        try:
            clean_phase = AgentPhase(str(getattr(phase, "value", phase or "preparing")))
        except ValueError:
            clean_phase = AgentPhase.PREPARING
        await result_backend.set_progress(job_id, TaskProgress(state=status, meta={"status": status, "phase": clean_phase.value, "stage": clean_stage, "message": safe_error(message, "Agent workflow is in progress."), "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat() if finished_at else None}))
    except Exception:
        # Progress must never turn a successful workflow into a failed task.
        pass


async def _emit_telemetry(run_id: str, event_type: AgentEventType, **fields: Any) -> None:
    """Publish advisory, sanitized worker telemetry without changing outcome."""

    try:
        await _telemetry_store.emit(
            run_id=run_id,
            event_type=event_type,
            task_id=run_id,
            worker_id=_worker_id,
            **fields,
        )
    except Exception:
        # Redis telemetry is diagnostic only. Never make a workflow fail when
        # the telemetry backend is unavailable.
        pass


@broker.task(task_name="agents.run")
async def run(payload: AgentTaskPayload) -> AgentTaskResult:
    """Run an explicitly registered workflow; never import payload values."""

    started = _now()
    if not isinstance(payload, AgentTaskPayload):
        try:
            payload = AgentTaskPayload.model_validate(payload)
        except Exception:
            result = AgentTaskResult(job_id="unknown", workflow="unknown", status=WorkflowStatus.FAILED, started_at=started, finished_at=_now(), error="Invalid agent task payload.")
            await _set_progress("unknown", status=WorkflowStatus.FAILED, stage="failed", phase=AgentPhase.FAILED, message="Agent workflow failed.", started_at=started, finished_at=result.finished_at)
            return result
    job_id = str(payload.job_id)
    await _emit_telemetry(
        job_id,
        AgentEventType.RUN_STARTED,
        workflow=payload.workflow,
        status=WorkflowStatus.RUNNING,
        phase=AgentPhase.PREPARING,
        message="Preparing agent workflow.",
    )
    await _set_progress(job_id, status=WorkflowStatus.RUNNING, stage="preparing", phase=AgentPhase.PREPARING, message="Preparing agent workflow.", started_at=started)

    async def progress(event: dict[str, Any]) -> None:
        await _set_progress(job_id, status=WorkflowStatus.RUNNING, stage=str(event.get("stage", "working")), phase=event.get("phase"), message=str(event.get("message", "Agent workflow is in progress.")), started_at=started)
        await _emit_telemetry(
            job_id,
            AgentEventType.HEARTBEAT if event.get("heartbeat") else AgentEventType.PHASE_CHANGED,
            workflow=payload.workflow,
            status=WorkflowStatus.RUNNING,
            phase=event.get("phase"),
            message=event.get("message"),
            metadata={"stage": event.get("stage"), "heartbeat": bool(event.get("heartbeat"))},
        )

    async def telemetry_event(event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        try:
            event_type = AgentEventType(str(getattr(event_type, "value", event_type)))
        except ValueError:
            return
        await _emit_telemetry(
            job_id,
            event_type,
            workflow=payload.workflow,
            node_id=event.get("node_id"),
            role=event.get("role"),
            runner=event.get("runner"),
            attempt=event.get("attempt"),
            status=event.get("status"),
            provider_run_id=event.get("provider_run_id"),
            message=event.get("message"),
            duration_ms=event.get("duration_ms"),
        )

    runner = CodexRunner(model=settings.openai_model, api_key=settings.openai_api_key, timeout_seconds=float(getattr(settings, "agent_timeout_minutes", 45)) * 60)
    result = await execute_workflow(payload, registry=workflow_registry, runner=runner, workspace_root=Path(getattr(settings, "agent_jobs_root", "/tmp/agentic/jobs")), skills_root=Path(__file__).resolve().parents[2] / ".agents" / "skills", progress_callback=progress, event_callback=telemetry_event, timeout_seconds=runner.timeout_seconds)
    terminal_phase = result.phase or (AgentPhase.COMPLETED if result.status == WorkflowStatus.COMPLETED else AgentPhase.FAILED)
    await _set_progress(job_id, status=result.status, stage=terminal_phase.value, phase=terminal_phase, message="Agent workflow completed." if result.status == WorkflowStatus.COMPLETED else "Agent workflow failed.", started_at=result.started_at, finished_at=result.finished_at)
    await _emit_telemetry(
        job_id,
        AgentEventType.RUN_COMPLETED if result.status == WorkflowStatus.COMPLETED else AgentEventType.RUN_FAILED,
        workflow=payload.workflow,
        status=result.status,
        phase=terminal_phase,
        message="Agent workflow completed." if result.status == WorkflowStatus.COMPLETED else "Agent workflow failed.",
        duration_ms=round((result.finished_at - result.started_at).total_seconds() * 1000),
    )
    return result


# Readable aliases for callers and tests; Taskiq's decorated object remains
# ``run`` and exposes ``original_func`` as usual.
agents_run = run
run_agent_task = run


__all__ = ["run", "agents_run", "run_agent_task"]
