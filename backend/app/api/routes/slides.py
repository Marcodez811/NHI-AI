"""Public asynchronous slide-job API."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError

from app.api.routes.chat import _stream_with_heartbeat
from app.broker import result_backend
from app.config import settings
from app.models.slides import (
    ApproveOutlineRequest,
    ApproveOutlineResponse,
    CreateSlidesJobResponse,
    GenerateSlidesRequest,
    JobStatus,
    SlideJobSummary,
    OutlineMessageRequest,
    OutlineRevisionResponse,
    SlideJobBrief,
    SUPPORTED_SLIDE_SOURCE_EXTENSIONS,
    SlidesJobStatusResponse,
    SlidesTaskPayload,
    SlidesTaskResult,
)
from app.services.agentic import AgentPhase, AgentTaskPayload
from app.services.documents.repository import DocumentRepository
from app.services.slides.outline_repository import (
    InMemorySlideOutlineRepository,
    OutlineAlreadyApprovedError,
    OutlineRevisionNotFoundError,
    SlideOutlineRepository,
    StaleOutlineRevisionError,
)
from app.services.slides.approval_outbox import dispatch_approval_outbox
from app.services.slides.planner import PlannerConversationService
from app.services.slides.repository import InMemorySlideJobRepository, SlideJobRepository
from app.models.documents import DocumentStatus
from app.api.routes.documents import get_document_repository
from app.tasks.agents import run as agents_run

router = APIRouter(prefix="/slides", tags=["slides"])

_slide_repository = InMemorySlideJobRepository()
_outline_repository = InMemorySlideOutlineRepository()
_planner_conversation_service = PlannerConversationService()


def get_slide_job_repository() -> SlideJobRepository:
    """Default dependency for direct use; production overrides this in main."""

    return _slide_repository


def get_slide_outline_repository() -> SlideOutlineRepository:
    """Default dependency for direct use; production overrides this in main."""

    return _outline_repository


def get_planner_conversation_service() -> PlannerConversationService:
    """Default dependency; tests override this with a fake, network-free runner."""

    return _planner_conversation_service

_PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_SAFE_STAGE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SENSITIVE_TEXT = re.compile(r"sk-[a-zA-Z0-9_-]{8,}|api[_ -]?key|authorization|traceback", re.I)
_FILENAME_UNSAFE = re.compile(r"[^\w._ -]+", re.UNICODE)


def get_result_backend() -> Any:
    return result_backend


def get_generate_slides_task() -> Any:
    # All agentic work, including slides, crosses the generic task boundary.
    return agents_run


def _safe_stage(value: object, *, fallback: str = "processing") -> str:
    stage = str(value or "").strip().lower()
    return stage if _SAFE_STAGE.fullmatch(stage) else fallback


_PHASE_BY_STAGE = {
    "starting": AgentPhase.PREPARING,
    "preparing": AgentPhase.PREPARING,
    "extracting": AgentPhase.EXTRACTING,
    "initial": AgentPhase.DRAFTING,
    "drafting": AgentPhase.DRAFTING,
    "validation": AgentPhase.VALIDATING,
    "validating": AgentPhase.VALIDATING,
    "review": AgentPhase.REVIEWING,
    "reviewing": AgentPhase.REVIEWING,
    "correction": AgentPhase.REVISING,
    "revision": AgentPhase.REVISING,
    "revising": AgentPhase.REVISING,
    "publishing": AgentPhase.PUBLISHING,
    "completed": AgentPhase.COMPLETED,
    "failed": AgentPhase.FAILED,
}


def _phase_for_status(job_status: JobStatus) -> AgentPhase:
    return {
        JobStatus.QUEUED: AgentPhase.QUEUED,
        JobStatus.RUNNING: AgentPhase.PREPARING,
        JobStatus.COMPLETED: AgentPhase.COMPLETED,
        JobStatus.FAILED: AgentPhase.FAILED,
    }[job_status]


def _safe_phase(value: object, *, status: JobStatus, stage: object = None) -> AgentPhase:
    candidate = getattr(value, "value", value)
    try:
        return AgentPhase(str(candidate or "").strip().lower())
    except ValueError:
        stage_value = str(stage or "").strip().lower()
        return _PHASE_BY_STAGE.get(stage_value, _phase_for_status(status))


def _safe_message(value: object, *, fallback: str) -> str:
    message = str(value or "").strip()
    if not message or _SENSITIVE_TEXT.search(message) or "/" in message or "\\" in message:
        return fallback
    return message[:240]


def _safe_filename(value: str | None, job_id: UUID) -> str:
    name = Path(value or f"{job_id}.pptx").name
    name = _FILENAME_UNSAFE.sub("_", name).strip(" .") or f"{job_id}.pptx"
    return name if name.lower().endswith(".pptx") else f"{name}.pptx"


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _brief_from_durable(job: Any) -> SlideJobBrief:
    return SlideJobBrief(
        title=job.title,
        document_ids=[UUID(str(value)) for value in job.document_ids],
        slides_count=job.slides_count,
        guidance=job.guidance,
        tone=job.tone,
    )


async def _write_progress(
    backend: Any,
    job_id: UUID,
    *,
    status_value: JobStatus,
    stage: str,
    message: str,
    phase: AgentPhase | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    await backend.set_progress(
        str(job_id),
        TaskProgress(
            state=status_value.value,
            meta={
                "status": status_value.value,
                "phase": (phase or _phase_for_status(status_value)).value,
                "stage": _safe_stage(stage),
                "message": _safe_message(message, fallback="Presentation job is queued."),
                "started_at": started_at.isoformat() if started_at else None,
                "finished_at": finished_at.isoformat() if finished_at else None,
            },
        ),
    )


async def _read_result(backend: Any, job_id: UUID) -> SlidesTaskResult | None:
    try:
        task_result = await backend.get_result(str(job_id))
    except ResultIsMissingError:
        return None
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job status is temporarily unavailable.",
        ) from exc
    if task_result is None:
        return None
    if getattr(task_result, "is_err", False):
        return SlidesTaskResult(job_id=job_id, status=JobStatus.FAILED, error="Presentation generation failed.")
    value = getattr(task_result, "return_value", task_result)
    # ``agents.run`` wraps workflow output in AgentTaskResult.  Unwrap only a
    # successful slides result and preserve the historical SlidesTaskResult
    # API returned to poll/download callers.
    wrapped_workflow = value.get("workflow") if isinstance(value, dict) else getattr(value, "workflow", None)
    wrapped_status = value.get("status") if isinstance(value, dict) else getattr(value, "status", None)
    wrapped_output = value.get("output") if isinstance(value, dict) else getattr(value, "output", None)
    wrapped_phase = value.get("phase") if isinstance(value, dict) else getattr(value, "phase", None)
    if wrapped_workflow is not None and wrapped_output is not None:
        wrapped_status_value = getattr(wrapped_status, "value", wrapped_status)
        if wrapped_workflow != "slides" or str(wrapped_status_value).lower() != "completed":
            return SlidesTaskResult(job_id=job_id, status=JobStatus.FAILED, error="Presentation generation failed.")
        value = wrapped_output
        if wrapped_phase is not None and isinstance(value, dict) and "phase" not in value:
            value = {**value, "phase": wrapped_phase}
    try:
        result = SlidesTaskResult.model_validate(value)
    except Exception:
        return SlidesTaskResult(job_id=job_id, status=JobStatus.FAILED, error="Presentation generation failed.")
    if result.job_id != job_id:
        return SlidesTaskResult(job_id=job_id, status=JobStatus.FAILED, error="Presentation generation failed.")
    return result


async def _read_progress(backend: Any, job_id: UUID) -> TaskProgress[Any] | None:
    try:
        return await backend.get_progress(str(job_id))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job status is temporarily unavailable.",
        ) from exc


def _progress_response(job_id: UUID, progress: TaskProgress[Any]) -> SlidesJobStatusResponse:
    meta = progress.meta if isinstance(progress.meta, dict) else {}
    status_value = str(meta.get("status") or progress.state).lower()
    if status_value in {JobStatus.QUEUED.value}:
        job_status = JobStatus.QUEUED
        fallback = "Presentation job is queued."
    elif status_value in {JobStatus.COMPLETED.value}:
        job_status = JobStatus.COMPLETED
        fallback = "Presentation is ready for download."
    elif status_value in {JobStatus.FAILED.value, "failure"}:
        job_status = JobStatus.FAILED
        fallback = "Presentation generation failed."
    else:
        job_status = JobStatus.RUNNING
        fallback = "Presentation generation is in progress."
    return SlidesJobStatusResponse(
        job_id=job_id,
        status=job_status,
        phase=_safe_phase(meta.get("phase"), status=job_status, stage=meta.get("stage")),
        stage=_safe_stage(
            meta.get("stage"),
            fallback={
                JobStatus.QUEUED: "queued",
                JobStatus.COMPLETED: "completed",
                JobStatus.FAILED: "failed",
            }.get(job_status, "processing"),
        ),
        message=_safe_message(meta.get("message"), fallback=fallback),
        started_at=_parse_datetime(meta.get("started_at")),
        finished_at=_parse_datetime(meta.get("finished_at")),
        error="Presentation generation failed." if job_status is JobStatus.FAILED else None,
    )


def _terminal_response(result: SlidesTaskResult) -> SlidesJobStatusResponse:
    is_completed = result.status is JobStatus.COMPLETED
    return SlidesJobStatusResponse(
        job_id=result.job_id,
        status=JobStatus.COMPLETED if is_completed else JobStatus.FAILED,
        phase=result.phase or (AgentPhase.COMPLETED if is_completed else AgentPhase.FAILED),
        stage="completed" if is_completed else "failed",
        message="Presentation is ready for download." if is_completed else "Presentation generation failed.",
        started_at=result.started_at,
        finished_at=result.finished_at,
        error=None if is_completed else "Presentation generation failed.",
        download_url=f"/api/v1/slides/jobs/{result.job_id}/download" if is_completed else None,
    )


async def _lookup_job(backend: Any, job_id: UUID) -> tuple[SlidesTaskResult | None, TaskProgress[Any] | None]:
    result, progress = await asyncio.gather(
        _read_result(backend, job_id),
        _read_progress(backend, job_id),
    )
    if result is None and progress is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slide job was not found.")
    return result, progress


async def _validate_slide_documents(
    document_ids: list[UUID],
    repository: DocumentRepository | None,
) -> None:
    """Validate document-backed slide inputs before creating a queued job.

    ``repository`` is optional only to preserve direct, dependency-free calls
    to this route helper. FastAPI always supplies it through the existing
    document repository dependency, so HTTP requests are fail-closed before
    any progress record or task is created.
    """

    if repository is None:
        return
    for document_id in document_ids:
        document = await repository.get_document(document_id)
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document was not found.",
            )
        document_status = getattr(document.status, "value", document.status)
        if document_status != DocumentStatus.READY.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document is not ready for slide generation.",
            )
        extension = str(document.extension or "").strip().lower()
        if extension not in SUPPORTED_SLIDE_SOURCE_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_SLIDE_SOURCE_EXTENSIONS))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Document source type is not supported for slide generation. Supported extensions: {supported}.",
            )


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=CreateSlidesJobResponse)
async def create_slides_job(
    request: GenerateSlidesRequest,
    backend: Annotated[Any, Depends(get_result_backend)],
    task: Annotated[Any, Depends(get_generate_slides_task)],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)] = None,
    slide_repository: Annotated[SlideJobRepository, Depends(get_slide_job_repository)] = None,
) -> CreateSlidesJobResponse:
    await _validate_slide_documents(request.document_ids, repository)
    job_id = uuid4()
    queued_at = datetime.now(timezone.utc)
    if slide_repository is not None:
        from app.models.slides import SlideJob
        durable_job = SlideJob(
            id=job_id,
            title=request.title,
            document_ids=[str(value) for value in request.document_ids],
            slides_count=request.slides_count,
            guidance=request.guidance,
            tone=request.tone,
            status=JobStatus.QUEUED.value,
            phase=AgentPhase.QUEUED.value,
            stage="queued",
            message="Presentation job is queued.",
        )
        try:
            await slide_repository.create(durable_job)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Slide jobs are temporarily unavailable.") from exc
    try:
        await _write_progress(
            backend,
            job_id,
            status_value=JobStatus.QUEUED,
            stage="queued",
            message="Presentation job is queued.",
            started_at=queued_at,
        )
    except Exception:
        # PostgreSQL records job existence and state. Redis progress is only a
        # low-latency overlay, so its failure must not discard a durable job.
        pass

    payload = SlidesTaskPayload(job_id=job_id, **request.model_dump())
    agent_payload = AgentTaskPayload(job_id=job_id, workflow="slides", input=payload.model_dump(mode="json"))
    try:
        await task.kicker().with_task_id(str(job_id)).kiq(agent_payload)
    except Exception as exc:
        if slide_repository is not None:
            try:
                await slide_repository.mark_terminal(
                    job_id,
                    status=JobStatus.FAILED.value,
                    phase=AgentPhase.FAILED.value,
                    error="Slide jobs are temporarily unavailable.",
                    finished_at=datetime.now(timezone.utc),
                )
            except Exception:
                pass
        try:
            await _write_progress(
                backend,
                job_id,
                status_value=JobStatus.FAILED,
                stage="failed",
                message="Presentation generation failed.",
                started_at=queued_at,
                finished_at=datetime.now(timezone.utc),
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slide jobs are temporarily unavailable.",
        ) from exc
    return CreateSlidesJobResponse(job_id=job_id, status=JobStatus.QUEUED, phase=AgentPhase.QUEUED)


@router.get("/jobs", response_model=list[SlideJobSummary])
async def list_slides_jobs(
    slide_repository: Annotated[SlideJobRepository, Depends(get_slide_job_repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SlideJobSummary]:
    """List recent durable jobs, including those parked for outline approval."""

    try:
        jobs = await slide_repository.list_recent(limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slide jobs are temporarily unavailable.",
        ) from exc
    return [
        SlideJobSummary(
            job_id=job.id,
            title=job.title,
            status=JobStatus(job.status),
            phase=_safe_phase(job.phase, status=JobStatus(job.status), stage=job.stage),
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        for job in jobs
    ]


@router.get("/jobs/{job_id}", response_model=SlidesJobStatusResponse)
async def get_slides_job(
    job_id: UUID,
    backend: Annotated[Any, Depends(get_result_backend)],
    slide_repository: Annotated[SlideJobRepository, Depends(get_slide_job_repository)] = None,
) -> SlidesJobStatusResponse:
    if slide_repository is not None:
        durable = await slide_repository.get(job_id)
        if durable is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slide job was not found.")
        # Redis is an advisory low-latency overlay for active jobs only.  A
        # terminal database row can never be replaced by stale Redis data.
        if durable.status not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            try:
                progress = await _read_progress(backend, job_id)
            except HTTPException:
                # Database state is still sufficient for a status response;
                # Redis is explicitly advisory and may be unavailable.
                progress = None
            if progress is not None:
                overlay = _progress_response(job_id, progress)
                # A Redis terminal value can be stale (for example after a
                # lease handoff), so only active status/phase fields may
                # overlay the durable row.
                if overlay.status in {JobStatus.QUEUED, JobStatus.RUNNING} and overlay.phase not in {AgentPhase.COMPLETED, AgentPhase.FAILED}:
                    return overlay.model_copy(update={
                        "status": JobStatus(durable.status),
                        "job_id": durable.id,
                        "error": None,
                        "download_url": None,
                        "brief": _brief_from_durable(durable),
                    })
        return SlidesJobStatusResponse(
            job_id=durable.id,
            status=JobStatus(durable.status),
            phase=_safe_phase(durable.phase, status=JobStatus(durable.status), stage=durable.stage),
            stage=durable.stage,
            message=durable.message,
            started_at=durable.started_at,
            finished_at=durable.finished_at,
            error=durable.error,
            download_url=f"/api/v1/slides/jobs/{durable.id}/download" if durable.status == JobStatus.COMPLETED.value else None,
            brief=_brief_from_durable(durable),
        )
    result, progress = await _lookup_job(backend, job_id)
    return _terminal_response(result) if result is not None else _progress_response(job_id, progress)


@router.get("/jobs/{job_id}/download")
async def download_slides_job(
    job_id: UUID,
    backend: Annotated[Any, Depends(get_result_backend)],
    slide_repository: Annotated[SlideJobRepository, Depends(get_slide_job_repository)] = None,
) -> FileResponse:
    if slide_repository is not None:
        durable = await slide_repository.get(job_id)
        if durable is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slide job was not found.")
        if durable.status != JobStatus.COMPLETED.value:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Presentation is not ready for download.")
        result = SlidesTaskResult(
            job_id=durable.id,
            status=JobStatus.COMPLETED,
            phase=AgentPhase.COMPLETED,
            artifact_key=durable.artifact_key,
            download_filename=durable.download_filename,
            started_at=durable.started_at,
            finished_at=durable.finished_at,
        )
    else:
        result, _ = await _lookup_job(backend, job_id)
    if result is None or result.status is not JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Presentation is not ready for download.",
        )
    key = Path(result.artifact_key or "")
    output_root = settings.agent_output_root.resolve(strict=False)
    if not result.artifact_key or key.is_absolute() or ".." in key.parts:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation file was not found.")
    artifact = (output_root / key).resolve(strict=False)
    try:
        artifact.relative_to(output_root)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation file was not found.") from None
    if artifact.is_symlink() or not artifact.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation file was not found.")
    return FileResponse(
        artifact,
        media_type=_PPTX_MEDIA_TYPE,
        filename=_safe_filename(result.download_filename, job_id),
    )


@router.get("/jobs/{job_id}/outline", response_model=OutlineRevisionResponse)
async def get_slide_job_outline(
    job_id: UUID,
    outline_repository: Annotated[SlideOutlineRepository, Depends(get_slide_outline_repository)],
) -> OutlineRevisionResponse:
    """Return the latest planner proposal for one job (Stage 5)."""

    revision = await outline_repository.get_latest(job_id)
    if revision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outline was not found.")
    return OutlineRevisionResponse(
        job_id=job_id,
        revision=revision.revision,
        outline=revision.to_outline(),
        session_id=revision.session_id,
        created_at=revision.created_at,
        approved_at=revision.approved_at,
    )


@router.post("/jobs/{job_id}/outline/messages")
async def send_slide_job_outline_message(
    job_id: UUID,
    request: OutlineMessageRequest,
    outline_repository: Annotated[SlideOutlineRepository, Depends(get_slide_outline_repository)],
    planner: Annotated[PlannerConversationService, Depends(get_planner_conversation_service)],
) -> StreamingResponse:
    """Continue the outline conversation and persist the resulting revision.

    Mirrors ``app.api.routes.chat.stream_chat``'s SSE shape exactly --
    ``_stream_with_heartbeat`` is the same helper, not a reimplementation --
    so the frontend parses both endpoints' events identically.
    """

    latest = await outline_repository.get_latest(job_id)
    if latest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outline was not found.")
    if latest.approved_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Outline has already been approved.")

    async def _generate() -> AsyncGenerator[str, None]:
        source = planner.continue_conversation(
            job_id=job_id,
            message=request.message,
            latest=latest,
            outline_repository=outline_repository,
        )
        async for chunk in _stream_with_heartbeat(
            source,
            heartbeat_seconds=settings.chat_stream_heartbeat_seconds,
            timeout_seconds=settings.chat_timeout_seconds,
        ):
            yield chunk

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/outline/approve", response_model=ApproveOutlineResponse)
async def approve_slide_job_outline(
    job_id: UUID,
    request: ApproveOutlineRequest,
    outline_repository: Annotated[SlideOutlineRepository, Depends(get_slide_outline_repository)],
    task: Annotated[Any, Depends(get_generate_slides_task)],
    slide_repository: Annotated[SlideJobRepository, Depends(get_slide_job_repository)] = None,
) -> ApproveOutlineResponse:
    """Approve one outline revision and resume authoring from it.

    The SQL repository commits the approval and one unique outbox row in the
    same transaction.  The dispatcher writes the existing author input file
    and publishes that row to TaskIQ afterwards.  It marks delivery only
    after publication, giving intentional at-least-once behavior across a
    crash; ``agents.run`` then uses its durable lease claim to make a repeat
    delivery harmless.
    """

    if slide_repository is not None:
        durable = await slide_repository.get(job_id)
        if durable is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slide job was not found.")

    try:
        approved = await outline_repository.approve(job_id, expected_revision=request.expected_revision)
    except OutlineAlreadyApprovedError:
        approved = await outline_repository.get_revision(job_id, request.expected_revision)
        if approved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outline revision was not found.")
    except OutlineRevisionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outline was not found.") from exc
    except StaleOutlineRevisionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Revision {exc.expected_revision} is no longer current; the latest revision is {exc.latest_revision}.",
        ) from exc

    event = await outline_repository.get_approval_outbox(job_id, approved.revision)
    if event is None:
        # This is an invariant violation (not a normal client error): a SQL
        # approval commits with the event, while the in-memory implementation
        # mirrors it for local/default behavior and tests.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Presentation authoring is awaiting dispatch.",
        )
    try:
        await dispatch_approval_outbox(
            event=event,
            revision=approved,
            repository=outline_repository,
            task=task,
            jobs_root=Path(settings.agent_jobs_root),
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Approved outline could not be written.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Presentation authoring could not be resumed.",
        ) from exc

    durable = await slide_repository.get(job_id) if slide_repository is not None else None
    return ApproveOutlineResponse(
        job_id=job_id,
        status=JobStatus(durable.status) if durable is not None else JobStatus.AWAITING_INPUT,
        phase=(
            _safe_phase(durable.phase, status=JobStatus(durable.status), stage=durable.stage)
            if durable is not None
            else AgentPhase.AWAITING_OUTLINE
        ),
        approved_revision=approved.revision,
    )
