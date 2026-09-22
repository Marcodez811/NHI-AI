"""Taskiq entrypoint for allowlisted generic agentic workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import socket
from typing import Any
from uuid import UUID

import redis.asyncio as redis
from sqlmodel import Session, select
from loguru import logger

from taskiq.depends.progress_tracker import TaskProgress

from app.broker import result_backend, tasks_broker as broker
from app.config import settings
from app.services.agentic import AgentPhase, AgentTaskPayload, AgentTaskResult, WorkflowStatus, workflow_registry
from app.services.agentic.service import execute_workflow
from app.services.agentic.runner import (
    CodexAgentRunner,
    CodexRunner,
    RunnerRegistry,
    bwrap_available,
    safe_error,
)
from app.services.agentic.sdk_runner import AgentsSdkRunner
from app.services.agentic.events import AgentEventType, AgentTelemetryStore
from app.services.slides.adapter import slides_adapter  # noqa: F401 - registers the built-in workflow
from app.services.slides.approval_outbox import dispatch_approval_outbox
from app.services.slides.artifacts import cleanup_job
from app.db import engine
from app.models.slides import JobStatus, SlideJob, SlidesTaskResult
from app.services.slides.outline_repository import (
    SQLModelSlideOutlineRepository,
    SlideOutlineApprovalOutbox,
)
from app.services.slides.repository import SQLModelSlideJobRepository, job_request

_SAFE_STAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_OUTBOX_DISPATCH_INTERVAL_SECONDS = 60

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

    if settings.agent_require_process_isolation and not bwrap_available():
        raise RuntimeError("AGENT_REQUIRE_PROCESS_ISOLATION is enabled but bubblewrap is unavailable")

    timeout_seconds = float(getattr(settings, "agent_timeout_minutes", 45)) * 60
    codex_runner = CodexAgentRunner(
        CodexRunner(
            model=settings.agent_default_model,
            reasoning_effort=settings.agent_default_reasoning_effort,
            api_key=settings.openai_api_key,
            timeout_seconds=timeout_seconds,
            heartbeat_seconds=settings.agent_heartbeat_seconds,
            require_process_isolation=settings.agent_require_process_isolation,
        ),
    )
    # Registered, not selected: every ``agent_*_runner`` setting defaults to
    # ``"codex"`` (app/config.py), so an adapter only reaches ``AgentsSdkRunner`` through
    # an explicit configuration change, never by default.
    agents_sdk_runner = AgentsSdkRunner(
        model=settings.agent_default_model,
        reasoning_effort=settings.agent_default_reasoning_effort,
        timeout_seconds=timeout_seconds,
        heartbeat_seconds=settings.agent_heartbeat_seconds,
        litellm_api_keys={
            "gemini": settings.gemini_api_key,
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
        },
    )
    return RunnerRegistry({"codex": codex_runner, "agents": agents_sdk_runner}), codex_runner.timeout_seconds


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
                # Only an explicit resume (the outline-approval endpoint
                # enqueues ``resume_from="author"``) may reclaim a job parked
                # in AWAITING_INPUT; an ordinary delivery must not silently
                # resume a workflow a human has not yet approved.
                allow_resume_from_awaiting_input=payload.resume_from == "author",
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

    if result.status == WorkflowStatus.RUNNING and result.phase == AgentPhase.AWAITING_OUTLINE:
        # Stage 5 (docs/agents-sdk-migration-plan.md): the planner paused this
        # workflow for a human decision. This is a deliberate, non-terminal
        # pause, never a failure -- the worker must never block on a human, so
        # it releases its lease and returns instead of waiting.
        message = "Waiting for outline approval."
        if slide_repository is not None:
            try:
                persisted = await slide_repository.pause_for_outline_approval(
                    payload.job_id,
                    message=message,
                    lease_token=slide_lease_token,
                )
                if persisted is None:
                    raise RuntimeError("slide job lease is no longer owned")
            except Exception:
                logger.exception("Unable to persist durable slide job outline pause", job_id=job_id)
                result = AgentTaskResult(
                    job_id=result.job_id,
                    workflow=result.workflow,
                    status=WorkflowStatus.FAILED,
                    phase=AgentPhase.FAILED,
                    output=None,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    error="Presentation outline could not be persisted.",
                )
                await _set_progress(job_id, status=WorkflowStatus.FAILED, stage=AgentPhase.FAILED.value, phase=AgentPhase.FAILED, message=safe_error(result.error, "Agent workflow failed."), started_at=result.started_at, finished_at=result.finished_at)
                if slide_db_session is not None:
                    slide_db_session.close()
                return result
        await _set_progress(job_id, status=WorkflowStatus.RUNNING, stage=AgentPhase.AWAITING_OUTLINE.value, phase=AgentPhase.AWAITING_OUTLINE, message=message, started_at=result.started_at)
        await _emit_telemetry(
            job_id,
            AgentEventType.PHASE_CHANGED,
            workflow=payload.workflow,
            status=WorkflowStatus.RUNNING,
            phase=AgentPhase.AWAITING_OUTLINE,
            message=message,
        )
        if slide_db_session is not None:
            slide_db_session.close()
        return result

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


def _stale_awaiting_outline_job_ids(cutoff: datetime) -> list[UUID]:
    """Read the ids of jobs parked in AWAITING_INPUT past the TTL cutoff.

    A plain, lock-free read: the race safety against a concurrent resume
    lives entirely in ``SlideJobRepository.expire_awaiting_input``, which
    re-locks and re-checks each row's status before writing anything, so a
    stale id returned here that has already resumed is simply skipped below.
    """

    with Session(engine) as session:
        return list(
            session.exec(
                select(SlideJob.id).where(
                    SlideJob.status == JobStatus.AWAITING_INPUT.value,
                    SlideJob.updated_at <= cutoff,
                )
            ).all()
        )


@broker.task(
    task_name="agents.expire_awaiting_outline",
    schedule=[{"interval": settings.agent_awaiting_outline_sweep_interval_seconds}],
)
async def expire_awaiting_outline_task() -> dict[str, int]:
    """Expire outline approvals abandoned past their TTL and reclaim workspace.

    Risk 3 (docs/agents-sdk-migration-plan.md, Stage 5b): a job paused in
    AWAITING_INPUT holds its workspace under ``agent_jobs_root`` indefinitely
    until a human approves or rejects the outline. This sweep only ever
    touches a job that is still parked in AWAITING_INPUT at expiry time; a
    job a concurrent resume has already moved to RUNNING is left untouched,
    because ``expire_awaiting_input`` re-locks and re-checks each row's
    status before writing, and a lost race there simply comes back ``None``
    and is skipped here.
    """

    ttl_seconds = settings.agent_awaiting_outline_ttl_seconds
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    reason = f"Outline approval was not completed within {ttl_seconds} seconds and the job was automatically expired."
    jobs_root = Path(settings.agent_jobs_root).expanduser().resolve()
    expired = 0
    for job_id in _stale_awaiting_outline_job_ids(cutoff):
        with Session(engine) as session:
            repository = SQLModelSlideJobRepository(session)
            job = await repository.expire_awaiting_input(job_id, cutoff=cutoff, error=reason)
        if job is None:
            # Already resumed, already terminal, or refreshed past the cutoff
            # since the read above -- nothing to clean up.
            continue
        cleanup_job(jobs_root / str(job_id))
        expired += 1
        logger.info("Expired abandoned outline job {} after {}s TTL", job_id, ttl_seconds)
    return {"expired": expired}


def _pending_approval_outbox_ids() -> list[UUID]:
    """Return undelivered approval events without holding a DB session open.

    Each event is re-read by the dispatcher before publishing.  We do not
    claim rows before TaskIQ accepts them: a process death at any point must
    leave the event eligible for the next sweep, and duplicate publication is
    safe because ``run`` claims the durable slide job before authoring.
    """

    with Session(engine) as session:
        return list(
            session.exec(
                select(SlideOutlineApprovalOutbox.id)
                .where(SlideOutlineApprovalOutbox.dispatched_at.is_(None))
                .order_by(SlideOutlineApprovalOutbox.created_at)
                .limit(100)
            ).all()
        )


@broker.task(
    task_name="agents.dispatch_outline_approval_outbox",
    schedule=[{"interval": _OUTBOX_DISPATCH_INTERVAL_SECONDS}],
)
async def dispatch_outline_approval_outbox_task() -> dict[str, int]:
    """Recover approvals committed before their TaskIQ publication completed."""

    delivered = 0
    failed = 0
    for outbox_id in _pending_approval_outbox_ids():
        with Session(engine) as session:
            repository = SQLModelSlideOutlineRepository(session)
            event = session.get(SlideOutlineApprovalOutbox, outbox_id)
            if event is None or event.dispatched_at is not None:
                continue
            revision = await repository.get_revision(event.job_id, event.revision)
            if revision is None:
                # An approval row is immutable, so this is corruption rather
                # than a client retry.  Keep the event pending and visible.
                await repository.record_approval_outbox_failure(
                    event.id, "Approved outline revision is missing."
                )
                failed += 1
                continue
            try:
                if await dispatch_approval_outbox(
                    event=event,
                    revision=revision,
                    repository=repository,
                    task=run,
                    jobs_root=Path(settings.agent_jobs_root),
                ):
                    delivered += 1
            except Exception:
                failed += 1
                logger.warning(
                    "Could not dispatch outline approval outbox event {}",
                    outbox_id,
                    exc_info=True,
                )
    return {"delivered": delivered, "failed": failed}


# Readable aliases for callers and tests; Taskiq's decorated object remains
# ``run`` and exposes ``original_func`` as usual.
agents_run = run
run_agent_task = run


__all__ = [
    "run",
    "agents_run",
    "run_agent_task",
    "expire_awaiting_outline_task",
    "dispatch_outline_approval_outbox_task",
]
