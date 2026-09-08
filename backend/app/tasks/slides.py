"""Taskiq adapter for asynchronous presentation generation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taskiq.depends.progress_tracker import TaskProgress

from app.broker import result_backend
from app.config import settings
from app.models.slides import JobStatus, SlidesTaskPayload, SlidesTaskResult
from app.services.slides.contracts import JobError

_SAFE_STAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SENSITIVE_TEXT = re.compile(r"sk-[a-zA-Z0-9_-]{8,}|api[_ -]?key|authorization|traceback", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_stage(value: object) -> str:
    stage = str(value or "processing").strip().lower()
    return stage if _SAFE_STAGE.fullmatch(stage) else "processing"


def _safe_message(value: object, *, fallback: str) -> str:
    message = str(value or "").strip()
    if not message or _SENSITIVE_TEXT.search(message) or "/" in message or "\\" in message:
        return fallback
    return message[:240]


async def _set_progress(
    job_id: str,
    *,
    status: JobStatus,
    stage: str,
    message: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    await result_backend.set_progress(
        job_id,
        TaskProgress(
            state=status.value,
            meta={
                "status": status.value,
                "stage": _safe_stage(stage),
                "message": _safe_message(
                    message,
                    fallback="Presentation generation is in progress.",
                ),
                "started_at": started_at.isoformat() if started_at else None,
                "finished_at": finished_at.isoformat() if finished_at else None,
            },
        ),
    )


def _relative_artifact_key(artifact_key: str | None) -> str | None:
    if not artifact_key:
        return None
    candidate = Path(artifact_key)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate.as_posix()


async def generate_slides_task(payload: SlidesTaskPayload) -> SlidesTaskResult:
    """Compatibility helper for direct callers; it is not a Taskiq task.

    New jobs use ``agents.run`` exclusively.  Keeping this unregistered helper
    lets older in-process integrations migrate without reintroducing a second
    queue entrypoint.
    """

    job_id = str(payload.job_id)
    started_at = _now()
    await _set_progress(
        job_id,
        status=JobStatus.RUNNING,
        stage="starting",
        message="Preparing source documents.",
        started_at=started_at,
    )

    async def report_progress(event: dict[str, Any]) -> None:
        await _set_progress(
            job_id,
            status=JobStatus.RUNNING,
            stage=_safe_stage(event.get("stage")),
            message=_safe_message(
                event.get("message"),
                fallback="Presentation generation is in progress.",
            ),
            started_at=started_at,
        )

    try:
        # Compatibility callers use the same registered, staged workflow as
        # the queue entrypoint. This prevents the old helper from bypassing
        # frozen extraction, deterministic validation, or semantic review.
        from app.services.agentic import AgentTaskPayload, WorkflowStatus
        from app.services.agentic.runner import CodexAgentRunner, CodexRunner, RunnerRegistry
        from app.services.agentic.service import execute_workflow
        from app.services.slides.adapter import slides_adapter  # noqa: F401

        codex = CodexRunner(
            model=settings.agent_default_model,
            reasoning_effort=settings.agent_default_reasoning_effort,
            api_key=settings.openai_api_key,
            timeout_seconds=settings.agent_timeout_minutes * 60,
            heartbeat_seconds=settings.agent_heartbeat_seconds,
            require_process_isolation=settings.agent_require_process_isolation,
        )
        workflow_result = await execute_workflow(
            AgentTaskPayload(job_id=payload.job_id, workflow="slides", input=payload),
            runner=RunnerRegistry({"codex": CodexAgentRunner(codex)}),
            workspace_root=settings.agent_jobs_root,
            progress_callback=report_progress,
            timeout_seconds=settings.agent_timeout_minutes * 60,
        )
        if workflow_result.status is not WorkflowStatus.COMPLETED or workflow_result.output is None:
            raise RuntimeError("presentation workflow did not complete")
        result = SlidesTaskResult.model_validate(workflow_result.output)
        artifact_key = _relative_artifact_key(result.artifact_key)
        if result.job_id != payload.job_id or result.status is not JobStatus.COMPLETED or not artifact_key:
            raise JobError("publish", "presentation could not be published")
        result = result.model_copy(
            update={
                "artifact_key": artifact_key,
                "started_at": result.started_at or started_at,
                "finished_at": result.finished_at or _now(),
                "error": None,
            },
        )
        await _set_progress(
            job_id,
            status=JobStatus.COMPLETED,
            stage="completed",
            message="Presentation is ready for download.",
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        return result
    except Exception as exc:
        # The exception is intentionally never returned or re-raised: Taskiq's
        # terminal result stays safe to display and no automatic retry occurs.
        _ = exc
        finished_at = _now()
        result = SlidesTaskResult(
            job_id=payload.job_id,
            status=JobStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
            error="Presentation generation failed.",
        )
        try:
            await _set_progress(
                job_id,
                status=JobStatus.FAILED,
                stage="failed",
                message="Presentation generation failed.",
                started_at=started_at,
                finished_at=finished_at,
            )
        except Exception:
            pass
        return result


# A few legacy in-process integrations accessed Taskiq's ``original_func``.
# Keep that callable alias during migration, but do not expose task metadata or
# register it with a broker: ``agents.run`` is the sole agent queue entrypoint.
generate_slides_task.original_func = generate_slides_task
