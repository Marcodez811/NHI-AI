"""Taskiq adapter for asynchronous presentation generation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taskiq.depends.progress_tracker import TaskProgress

from app.broker import broker, result_backend
from app.config import settings
from app.models.slides import JobStatus, SlidesTaskPayload, SlidesTaskResult
from app.services.virtual_fs import DocumentResolutionError, SharedVolumeDocumentResolver

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


@broker.task(task_name="slides.generate")
async def generate_slides_task(payload: SlidesTaskPayload) -> SlidesTaskResult:
    """Resolve documents in the worker, then run the slide-generation service."""

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
        source_paths = await SharedVolumeDocumentResolver(
            settings.slides_documents_root,
        ).resolve_many(payload.document_ids)

        # Imported here so an API process can start before the optional slide
        # generation runtime is loaded; workers are the only callers.
        from app.services.slides.agent import JobError, generate_slides

        generated = await generate_slides(
            job_id=payload.job_id,
            source_paths=source_paths,
            request=payload,
            jobs_root=settings.slides_jobs_root,
            output_root=settings.slides_output_root,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_minutes=settings.slides_timeout_minutes,
            keep_workspace_on_failure=settings.slides_keep_workspace_on_failure,
            progress_callback=report_progress,
        )
        result = SlidesTaskResult.model_validate(generated)
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
