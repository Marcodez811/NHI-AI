"""Taskiq entrypoint for allowlisted generic agentic workflows."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import socket
from typing import Any

import redis.asyncio as redis
from sqlmodel import Session
from loguru import logger

from taskiq.depends.progress_tracker import TaskProgress

from app.broker import result_backend, tasks_broker as broker
from app.config import settings
from app.services.agentic import AgentPhase, AgentTaskPayload, AgentTaskResult, WorkflowStatus, workflow_registry
from app.services.agentic.service import execute_workflow
from app.services.agentic.runner import CodexAgentRunner, CodexRunner, RunnerRegistry, safe_error
from app.services.agentic.events import AgentEventType, AgentTelemetryStore
from app.services.slides.adapter import slides_adapter  # noqa: F401 - registers the built-in workflow
from app.db import engine
from app.models.slides import JobStatus, SlidesTaskResult
from app.services.slides.repository import SQLModelSlideJobRepository, job_request

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


def _build_runner_registry() -> tuple[RunnerRegistry, float | None]:
    """Build the worker's configured provider allowlist.

    Runner selection remains server-owned by the workflow adapter while the
    Codex primitive supplies credentials and the fallback model.  The
    coordinator can therefore honor different runner names per node without
    exposing either choice through the task payload.
    """

    codex_runner = CodexAgentRunner(
        CodexRunner(
            model=settings.agent_default_model,
            reasoning_effort=settings.agent_default_reasoning_effort,
            api_key=settings.openai_api_key,
            timeout_seconds=float(getattr(settings, "agent_timeout_minutes", 45)) * 60,
            heartbeat_seconds=settings.agent_heartbeat_seconds,
        ),
    )
    return RunnerRegistry({"codex": codex_runner}), codex_runner.timeout_seconds


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _progress_telemetry_type(event: dict[str, Any]) -> AgentEventType:
    """Classify coordinator phases separately from provider node progress."""

    raw_event_type = str(getattr(event.get("event_type"), "value", event.get("event_type") or ""))
    if event.get("heartbeat") or raw_event_type == AgentEventType.HEARTBEAT.value:
        return AgentEventType.HEARTBEAT
    if raw_event_type == AgentEventType.NODE_PROGRESS.value:
        return AgentEventType.NODE_PROGRESS
    return AgentEventType.PHASE_CHANGED


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
    slide_db_session: Session | None = None
    slide_repository: SQLModelSlideJobRepository | None = None
    slide_lease_token: str | None = None
    if payload.workflow == "slides":
        # Claiming is fenced in the database before any workspace/provider
        # side effects.  This makes duplicate TaskIQ deliveries harmless.
        try:
            from uuid import UUID, uuid4
            slide_db_session = Session(engine)
            slide_repository = SQLModelSlideJobRepository(slide_db_session)
            slide_lease_token = uuid4().hex
            durable_job, claimed = await slide_repository.claim(
                UUID(job_id),
                lease_token=slide_lease_token,
                # A slide run can legitimately outlive the generic five-minute
                # lease; keep a duplicate stream delivery from purchasing a
                # second workflow while the original worker is still active.
                lease_seconds=max(300, settings.agent_timeout_minutes * 60 + 60),
            )
            if durable_job is None:
                slide_db_session.close()
                return AgentTaskResult(job_id=payload.job_id, workflow=payload.workflow, status=WorkflowStatus.FAILED, phase=AgentPhase.FAILED, started_at=started, finished_at=_now(), error="Presentation job was not found.")
            if not claimed:
                slide_db_session.close()
                if durable_job.status in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
                    terminal = AgentTaskResult(
                        job_id=payload.job_id,
                        workflow=payload.workflow,
                        status=WorkflowStatus(durable_job.status),
                        phase=AgentPhase(durable_job.phase),
                        started_at=durable_job.started_at or started,
                        finished_at=durable_job.finished_at or _now(),
                        error=durable_job.error,
                        output=SlidesTaskResult(
                            job_id=durable_job.id,
                            status=JobStatus(durable_job.status),
                            phase=AgentPhase(durable_job.phase),
                            artifact_key=durable_job.artifact_key,
                            download_filename=durable_job.download_filename,
                            started_at=durable_job.started_at,
                            finished_at=durable_job.finished_at,
                            error=durable_job.error,
                        ).model_dump(mode="json") if durable_job.status == JobStatus.COMPLETED.value else None,
                    )
                    return terminal
                return AgentTaskResult(job_id=payload.job_id, workflow=payload.workflow, status=WorkflowStatus.RUNNING, phase=AgentPhase(durable_job.phase), started_at=durable_job.started_at or started, finished_at=_now())
            payload = payload.model_copy(update={"input": job_request(durable_job)})
        except Exception:
            if slide_db_session is not None:
                slide_db_session.close()
            logger.exception("Unable to claim durable slide job", job_id=job_id)
            return AgentTaskResult(
                job_id=payload.job_id,
                workflow=payload.workflow,
                status=WorkflowStatus.FAILED,
                phase=AgentPhase.FAILED,
                started_at=started,
                finished_at=_now(),
                error="Presentation job could not be started.",
            )
    await _emit_telemetry(
        job_id,
        AgentEventType.RUN_STARTED,
        workflow=payload.workflow,
        status=WorkflowStatus.RUNNING,
        phase=AgentPhase.PREPARING,
        message="Preparing agent workflow.",
    )
    await _set_progress(job_id, status=WorkflowStatus.RUNNING, stage="preparing", phase=AgentPhase.PREPARING, message="Preparing agent workflow.", started_at=started)
    if slide_repository is not None:
        try:
            persisted = await slide_repository.update_progress(
                payload.job_id,
                phase=AgentPhase.PREPARING.value,
                stage="preparing",
                message="Preparing agent workflow.",
                lease_token=slide_lease_token,
            )
            if persisted is None:
                raise RuntimeError("slide job lease is no longer owned")
        except Exception:
            logger.exception("Unable to persist durable slide job phase", job_id=job_id)
            if slide_db_session is not None:
                slide_db_session.close()
            return AgentTaskResult(
                job_id=payload.job_id,
                workflow=payload.workflow,
                status=WorkflowStatus.FAILED,
                phase=AgentPhase.FAILED,
                output=None,
                started_at=started,
                finished_at=_now(),
                error="Presentation job could not be updated.",
            )

    async def progress(event: dict[str, Any]) -> None:
        await _set_progress(job_id, status=WorkflowStatus.RUNNING, stage=str(event.get("stage", "working")), phase=event.get("phase"), message=str(event.get("message", "Agent workflow is in progress.")), started_at=started)
        if slide_repository is not None:
            try:
                persisted = await slide_repository.update_progress(job_id, phase=str(getattr(event.get("phase"), "value", event.get("phase") or AgentPhase.PREPARING.value)), stage=str(event.get("stage", "working")), message=str(event.get("message", "Agent workflow is in progress.")), lease_token=slide_lease_token)
                if persisted is None:
                    raise RuntimeError("slide job lease is no longer owned")
            except Exception:
                logger.exception("Unable to persist durable slide job phase", job_id=job_id)
                raise
        telemetry_type = _progress_telemetry_type(event)
        # Provider SDK progress is retained in the worker's audit/progress
        # channel, but is too noisy for the public lifecycle timeline.
        if telemetry_type is not AgentEventType.NODE_PROGRESS:
            await _emit_telemetry(
                job_id,
                telemetry_type,
                workflow=payload.workflow,
                node_id=event.get("node_id"),
                role=event.get("role"),
                runner=event.get("runner"),
                model=event.get("model"),
                attempt=event.get("attempt"),
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
            model=event.get("model"),
            attempt=event.get("attempt"),
            status=event.get("status"),
            provider_run_id=event.get("provider_run_id"),
            message=event.get("message"),
            duration_ms=event.get("duration_ms"),
        )

    runner_registry, runner_timeout = _build_runner_registry()
    result = await execute_workflow(payload, registry=workflow_registry, runner=runner_registry, workspace_root=Path(getattr(settings, "agent_jobs_root", "/tmp/agentic/jobs")), skills_root=Path(__file__).resolve().parents[2] / ".agents" / "skills", progress_callback=progress, event_callback=telemetry_event, timeout_seconds=runner_timeout)
    terminal_phase = result.phase or (AgentPhase.COMPLETED if result.status == WorkflowStatus.COMPLETED else AgentPhase.FAILED)
    if slide_repository is not None:
        try:
            output = result.output
            output_data = output if isinstance(output, dict) else (output.model_dump(mode="json") if hasattr(output, "model_dump") else {})
            persisted = await slide_repository.mark_terminal(
                payload.job_id,
                status=JobStatus(result.status.value).value,
                phase=terminal_phase.value,
                error=result.error,
                artifact_key=output_data.get("artifact_key"),
                download_filename=output_data.get("download_filename"),
                finished_at=result.finished_at,
                lease_token=slide_lease_token,
            )
            if persisted is None:
                raise RuntimeError("slide job lease is no longer owned")
        except Exception:
            logger.exception("Unable to persist durable slide job terminal state", job_id=job_id)
            result = AgentTaskResult(
                job_id=result.job_id,
                workflow=result.workflow,
                status=WorkflowStatus.FAILED,
                phase=AgentPhase.FAILED,
                output=None,
                started_at=result.started_at,
                finished_at=result.finished_at,
                error="Presentation result could not be persisted.",
            )
            terminal_phase = AgentPhase.FAILED
    terminal_message = "Agent workflow completed." if result.status == WorkflowStatus.COMPLETED else safe_error(result.error, "Agent workflow failed.")
    await _set_progress(job_id, status=result.status, stage=terminal_phase.value, phase=terminal_phase, message=terminal_message, started_at=result.started_at, finished_at=result.finished_at)
    await _emit_telemetry(
        job_id,
        AgentEventType.RUN_COMPLETED if result.status == WorkflowStatus.COMPLETED else AgentEventType.RUN_FAILED,
        workflow=payload.workflow,
        status=result.status,
        phase=terminal_phase,
        message=terminal_message,
        duration_ms=round((result.finished_at - result.started_at).total_seconds() * 1000),
    )
    if slide_db_session is not None:
        slide_db_session.close()
    return result


# Readable aliases for callers and tests; Taskiq's decorated object remains
# ``run`` and exposes ``original_func`` as usual.
agents_run = run
run_agent_task = run


__all__ = ["run", "agents_run", "run_agent_task"]
