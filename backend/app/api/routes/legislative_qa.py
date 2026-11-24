"""立院QA AI writer: start a ``legislative_qa`` agentic job and report its progress."""

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError

from app.api.routes.chat import _load_qa, _qa_http_error, _save_qa, get_chat_repository
from app.api.routes.news import get_generate_news_task, get_result_backend
from app.models.legislative_qa import GenerateQaRequest, QaJobStatusResponse, QaTaskPayload, QaTaskResult
from app.models.slides import JobStatus
from app.services.agentic.contracts import AgentPhase, AgentTaskPayload
from app.services.chat.repository import ChatRepository
from app.services.chat.skills import legislative_qa as qa
from app.services.legislative_qa.writer import build_writer_input

router = APIRouter(tags=["legislative-qa"])

_ACTIVE = {JobStatus.QUEUED, JobStatus.RUNNING}


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _finished(job_id: UUID, value: Any) -> QaJobStatusResponse | None:
    if _field(value, "workflow") != "legislative_qa":
        return None
    if str(getattr(_field(value, "status"), "value", _field(value, "status"))) != "completed":
        message = str(_field(value, "error") or "AI 撰寫失敗。")[:512]
        return QaJobStatusResponse(job_id=job_id, status=JobStatus.FAILED, phase=AgentPhase.FAILED, error=message)
    try:
        result = QaTaskResult.model_validate(_field(value, "output"))
    except Exception:
        return QaJobStatusResponse(job_id=job_id, status=JobStatus.FAILED, phase=AgentPhase.FAILED, error="結果無法讀取。")
    return QaJobStatusResponse(
        job_id=job_id, status=JobStatus.COMPLETED, phase=AgentPhase.COMPLETED, artifact_id=result.artifact_id,
        download_url=f"/api/v1/artifacts/{result.artifact_id}/download",
        checked=result.checked, issues=result.issues, review=result.review,
    )


def _in_progress(job_id: UUID, progress: Any) -> QaJobStatusResponse:
    meta = progress.meta if isinstance(progress.meta, dict) else {}
    raw_status = str(meta.get("status") or progress.state)
    raw_phase = str(meta.get("phase") or "preparing")
    return QaJobStatusResponse(
        job_id=job_id,
        status=JobStatus(raw_status) if raw_status in JobStatus._value2member_map_ else JobStatus.RUNNING,
        phase=AgentPhase(raw_phase) if raw_phase in AgentPhase._value2member_map_ else AgentPhase.PREPARING,
        message=str(meta.get("message") or "")[:512] or None,
        started_at=meta.get("started_at"), finished_at=meta.get("finished_at"),
    )


async def read_job(backend: Any, job_id: UUID) -> QaJobStatusResponse | None:
    """The job's current state, or ``None`` when it is unknown (never started, expired or another workflow)."""

    try:
        task_result = await backend.get_result(str(job_id))
    except ResultIsMissingError:
        task_result = None
    except Exception as exc:
        raise HTTPException(status_code=503, detail="工作狀態暫時無法取得。") from exc
    if task_result is not None:
        return _finished(job_id, getattr(task_result, "return_value", task_result))
    try:
        progress = await backend.get_progress(str(job_id))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="工作狀態暫時無法取得。") from exc
    return None if progress is None else _in_progress(job_id, progress)


async def _running_job(workspace: Any, backend: Any) -> bool:
    raw = (workspace.generation or {}).get("job_id")
    if not raw:
        return False
    state = await read_job(backend, UUID(raw))
    return state is not None and state.status in _ACTIVE


@router.post(
    "/chat/sessions/{session_id}/qa/generate", status_code=status.HTTP_202_ACCEPTED, response_model=QaJobStatusResponse
)
async def generate_qa_document(
    session_id: UUID,
    payload: GenerateQaRequest,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    backend: Annotated[Any, Depends(get_result_backend)],
    task: Annotated[Any, Depends(get_generate_news_task)],
) -> QaJobStatusResponse:
    workspace = await _load_qa(session_id, repository)
    try:
        build_writer_input(workspace, payload.session_label)
    except qa.QaError as error:
        raise _qa_http_error(error) from error
    if await _running_job(workspace, backend):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "qa_generation_running", "message": "已經有一份文件正在撰寫中。"},
        )
    job_id, now = uuid4(), datetime.now(timezone.utc)
    label = (payload.session_label or "").strip() or None
    workspace.generation = {"job_id": str(job_id), "session_label": label, "started_at": now.isoformat()}
    await _save_qa(workspace, repository)
    try:
        await backend.set_progress(str(job_id), TaskProgress(state="queued", meta={
            "status": "queued", "phase": "queued", "message": "已排入佇列。", "started_at": now.isoformat(),
        }))
        job = QaTaskPayload(job_id=job_id, session_id=session_id, session_label=label)
        await task.kicker().with_task_id(str(job_id)).kiq(AgentTaskPayload(
            job_id=job_id, workflow="legislative_qa", input=job.model_dump(mode="json"),
        ))
    except Exception as exc:
        workspace.generation = {}
        await _save_qa(workspace, repository)
        raise HTTPException(status_code=503, detail="AI 撰寫暫時無法使用。") from exc
    return QaJobStatusResponse(job_id=job_id, status=JobStatus.QUEUED, phase=AgentPhase.QUEUED, started_at=now)


@router.get("/chat/sessions/{session_id}/qa/generate/{job_id}", response_model=QaJobStatusResponse)
async def get_qa_generation(
    session_id: UUID,
    job_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    backend: Annotated[Any, Depends(get_result_backend)],
) -> QaJobStatusResponse:
    workspace = await _load_qa(session_id, repository)
    state = await read_job(backend, job_id)
    if state is not None and state.status in _ACTIVE:
        return state
    if (workspace.generation or {}).get("job_id") == str(job_id):
        # The job ended (or vanished): release the workspace so a new run can start.
        workspace.generation = {}
        await _save_qa(workspace, repository)
    if state is None:
        raise HTTPException(status_code=404, detail="找不到這個撰寫工作。")
    return state
