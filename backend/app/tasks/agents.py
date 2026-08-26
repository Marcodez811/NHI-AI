"""Taskiq entrypoint for allowlisted generic agentic workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taskiq.depends.progress_tracker import TaskProgress

from app.broker import result_backend, tasks_broker as broker
from app.config import settings
from app.services.agentic import AgentTaskPayload, AgentTaskResult, WorkflowStatus, workflow_registry
from app.services.agentic.service import execute_workflow
from app.services.agentic.runner import CodexRunner, safe_error
from app.services.slides.adapter import slides_adapter  # noqa: F401 - registers the built-in workflow
import re

_SAFE_STAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _set_progress(job_id: str, *, status: str, stage: str, message: str, started_at: datetime, finished_at: datetime | None = None) -> None:
    try:
        clean_stage = stage if _SAFE_STAGE.fullmatch(str(stage or "")) else "working"
        await result_backend.set_progress(job_id, TaskProgress(state=status, meta={"status": status, "stage": clean_stage, "message": safe_error(message, "Agent workflow is in progress."), "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat() if finished_at else None}))
    except Exception:
        # Progress must never turn a successful workflow into a failed task.
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
            await _set_progress("unknown", status=WorkflowStatus.FAILED, stage="failed", message="Agent workflow failed.", started_at=started, finished_at=result.finished_at)
            return result
    job_id = str(payload.job_id)
    await _set_progress(job_id, status=WorkflowStatus.RUNNING, stage="starting", message="Preparing agent workflow.", started_at=started)

    async def progress(event: dict[str, Any]) -> None:
        await _set_progress(job_id, status=WorkflowStatus.RUNNING, stage=str(event.get("stage", "working")), message=str(event.get("message", "Agent workflow is in progress.")), started_at=started)

    runner = CodexRunner(model=settings.openai_model, api_key=settings.openai_api_key, timeout_seconds=float(getattr(settings, "agent_timeout_minutes", 45)) * 60)
    result = await execute_workflow(payload, registry=workflow_registry, runner=runner, workspace_root=Path(getattr(settings, "agent_jobs_root", "/tmp/agentic/jobs")), skills_root=Path(__file__).resolve().parents[2] / ".agents" / "skills", progress_callback=progress, timeout_seconds=runner.timeout_seconds)
    await _set_progress(job_id, status=result.status, stage="completed" if result.status == WorkflowStatus.COMPLETED else "failed", message="Agent workflow completed." if result.status == WorkflowStatus.COMPLETED else "Agent workflow failed.", started_at=result.started_at, finished_at=result.finished_at)
    return result


# Readable aliases for callers and tests; Taskiq's decorated object remains
# ``run`` and exposes ``original_func`` as usual.
agents_run = run
run_agent_task = run


__all__ = ["run", "agents_run", "run_agent_task"]
