"""Asynchronous news generation API."""

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError

from app.api.routes.documents import get_document_repository
from app.broker import result_backend
from app.models.documents import DocumentStatus
from app.models.news import CreateNewsJobResponse, GenerateNewsRequest, NewsJobStatusResponse, NewsTaskPayload, NewsTaskResult
from app.models.slides import JobStatus, SUPPORTED_SLIDE_SOURCE_EXTENSIONS
from app.services.agentic.contracts import AgentPhase, AgentTaskPayload
from app.services.documents.repository import DocumentRepository
from app.tasks.agents import run as agents_run

router = APIRouter(prefix="/news", tags=["news"])


def get_result_backend() -> Any:
    return result_backend


def get_generate_news_task() -> Any:
    return agents_run


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=CreateNewsJobResponse)
async def create_news_job(
    request: GenerateNewsRequest,
    backend: Annotated[Any, Depends(get_result_backend)],
    task: Annotated[Any, Depends(get_generate_news_task)],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> CreateNewsJobResponse:
    for document_id in request.document_ids:
        document = await repository.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document was not found.")
        if document.status != DocumentStatus.READY:
            raise HTTPException(status_code=409, detail="Document is not ready for news generation.")
        if str(document.extension or "").lower() not in SUPPORTED_SLIDE_SOURCE_EXTENSIONS:
            raise HTTPException(status_code=422, detail="Document format is not supported for news generation.")
    job_id = uuid4()
    now = datetime.now(timezone.utc)
    try:
        await backend.set_progress(str(job_id), TaskProgress(state="queued", meta={
            "status": "queued", "phase": "queued", "message": "News job is queued.", "started_at": now.isoformat(),
        }))
        payload = NewsTaskPayload(job_id=job_id, **request.model_dump())
        await task.kicker().with_task_id(str(job_id)).kiq(AgentTaskPayload(
            job_id=job_id, workflow="news", input=payload.model_dump(mode="json"),
        ))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="News jobs are temporarily unavailable.") from exc
    return CreateNewsJobResponse(job_id=job_id, status=JobStatus.QUEUED, phase=AgentPhase.QUEUED)


@router.get("/jobs/{job_id}", response_model=NewsJobStatusResponse)
async def get_news_job(job_id: UUID, backend: Annotated[Any, Depends(get_result_backend)]) -> NewsJobStatusResponse:
    try:
        task_result = await backend.get_result(str(job_id))
    except ResultIsMissingError:
        task_result = None
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Job status is temporarily unavailable.") from exc
    if task_result is not None:
        value = getattr(task_result, "return_value", task_result)
        if isinstance(value, dict):
            workflow, result_status, output = value.get("workflow"), value.get("status"), value.get("output")
        else:
            workflow, result_status, output = getattr(value, "workflow", None), getattr(value, "status", None), getattr(value, "output", None)
        if workflow != "news":
            raise HTTPException(status_code=404, detail="News job was not found.")
        if str(getattr(result_status, "value", result_status)) == "completed":
            try:
                news = NewsTaskResult.model_validate(output)
                if news.job_id != job_id:
                    raise ValueError("job mismatch")
            except Exception:
                raise HTTPException(status_code=500, detail="News result is unavailable.") from None
            return NewsJobStatusResponse(job_id=job_id, status=JobStatus.COMPLETED, phase=AgentPhase.COMPLETED, article=news.article)
        return NewsJobStatusResponse(job_id=job_id, status=JobStatus.FAILED, phase=AgentPhase.FAILED, error="News generation failed.")
    try:
        progress = await backend.get_progress(str(job_id))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Job status is temporarily unavailable.") from exc
    if progress is None:
        raise HTTPException(status_code=404, detail="News job was not found.")
    meta = progress.meta if isinstance(progress.meta, dict) else {}
    raw_status = str(meta.get("status") or progress.state)
    job_status = JobStatus(raw_status) if raw_status in JobStatus._value2member_map_ else JobStatus.RUNNING
    raw_phase = str(meta.get("phase") or "preparing")
    phase = AgentPhase(raw_phase) if raw_phase in AgentPhase._value2member_map_ else AgentPhase.PREPARING
    return NewsJobStatusResponse(
        job_id=job_id, status=job_status, phase=phase,
        message=str(meta.get("message") or "News generation is in progress.")[:512],
        started_at=meta.get("started_at"), finished_at=meta.get("finished_at"),
        error="News generation failed." if job_status == JobStatus.FAILED else None,
    )
