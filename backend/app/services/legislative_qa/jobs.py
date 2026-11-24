"""Starting and reading 立院QA writer jobs; shared by the HTTP routes and the chat tool."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from taskiq.depends.progress_tracker import TaskProgress
from taskiq_redis.exceptions import ResultIsMissingError

from app.models.chat import ChatQaWorkspace
from app.models.legislative_qa import QaJobStatusResponse, QaRevision, QaTaskPayload, QaTaskResult, ReviseQaRequest
from app.models.slides import JobStatus
from app.services.agentic.contracts import AgentPhase, AgentTaskPayload
from app.services.artifacts import ArtifactRepository, ArtifactStorage, ArtifactStorageError
from app.services.chat.repository import ChatRepository
from app.services.chat.skills.legislative_qa import QaError, recompute_stage
from app.services.legislative_qa.document import QaDocument
from app.services.legislative_qa.revision import validate_scope
from app.services.legislative_qa.writer import build_writer_input

ACTIVE = {JobStatus.QUEUED, JobStatus.RUNNING}


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def finished(job_id: UUID, value: Any) -> QaJobStatusResponse | None:
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


def in_progress(job_id: UUID, progress: Any) -> QaJobStatusResponse:
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
        return finished(job_id, getattr(task_result, "return_value", task_result))
    try:
        progress = await backend.get_progress(str(job_id))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="工作狀態暫時無法取得。") from exc
    return None if progress is None else in_progress(job_id, progress)


async def running_job(workspace: ChatQaWorkspace, backend: Any) -> bool:
    raw = (workspace.generation or {}).get("job_id")
    if not raw:
        return False
    state = await read_job(backend, UUID(raw))
    return state is not None and state.status in ACTIVE


async def start_job(
    *, session_id: UUID, workspace: ChatQaWorkspace, repository: ChatRepository, backend: Any, task: Any,
    label: str | None, revision: QaRevision | None = None,
) -> QaJobStatusResponse:
    """Queue one writer job (a first draft, or a scoped revision when ``revision`` is given)."""

    if await running_job(workspace, backend):
        raise QaError("qa_generation_running", "已經有一份文件正在撰寫中。")
    job_id, now = uuid4(), datetime.now(timezone.utc)
    workspace.generation = {"job_id": str(job_id), "session_label": label, "started_at": now.isoformat()}
    if revision is not None:
        workspace.generation["revision"] = revision.model_dump(mode="json")
    recompute_stage(workspace)
    await repository.save_qa_workspace(workspace)
    try:
        await backend.set_progress(str(job_id), TaskProgress(state="queued", meta={
            "status": "queued", "phase": "queued", "message": "已排入佇列。", "started_at": now.isoformat(),
        }))
        job = QaTaskPayload(job_id=job_id, session_id=session_id, session_label=label, revision=revision)
        await task.kicker().with_task_id(str(job_id)).kiq(AgentTaskPayload(
            job_id=job_id, workflow="legislative_qa", input=job.model_dump(mode="json"),
        ))
    except Exception as exc:
        workspace.generation = {}
        recompute_stage(workspace)
        await repository.save_qa_workspace(workspace)
        raise HTTPException(status_code=503, detail="AI 撰寫暫時無法使用。") from exc
    return QaJobStatusResponse(job_id=job_id, status=JobStatus.QUEUED, phase=AgentPhase.QUEUED, started_at=now)


async def load_version(
    workspace: ChatQaWorkspace, artifact_id: UUID, artifacts: ArtifactRepository, storage: ArtifactStorage
) -> tuple[dict[str, Any], QaDocument]:
    """A version of this session and its structured content; ``qa_version_not_found`` for anything else."""

    missing = QaError("qa_version_not_found", "找不到這個版本。")
    entry = next((item for item in workspace.versions if item.get("artifact_id") == str(artifact_id)), None)
    artifact = await artifacts.get(artifact_id) if entry is not None else None
    if entry is None or artifact is None:
        raise missing
    try:
        sidecar = storage.resolve(artifact.storage_key).with_suffix(".json")
        return entry, QaDocument.model_validate_json(sidecar.read_text(encoding="utf-8"))
    except (ArtifactStorageError, OSError, ValueError) as exc:
        raise missing from exc


def latest_ai_version(workspace: ChatQaWorkspace) -> UUID:
    entry = next((item for item in workspace.versions if item.get("kind") == "ai"), None)
    if entry is None:
        raise QaError("qa_version_not_found", "還沒有 AI 撰寫的版本可以修訂。")
    return UUID(entry["artifact_id"])


async def start_revision(
    *, session_id: UUID, workspace: ChatQaWorkspace, request: ReviseQaRequest, repository: ChatRepository,
    backend: Any, task: Any, artifacts: ArtifactRepository, storage: ArtifactStorage,
) -> QaJobStatusResponse:
    base_id = request.base_artifact_id or latest_ai_version(workspace)
    entry, base = await load_version(workspace, base_id, artifacts, storage)
    if entry.get("kind") != "ai":
        raise QaError("qa_revision_base", "只能修訂 AI 撰寫的版本。")
    scope = QaRevision(
        base_artifact_id=base_id, question_no=request.question_no, part=request.part, section=request.section,
        instruction=request.instruction.strip(),
    )
    validate_scope(base, scope)
    build_writer_input(workspace, base.session_label)
    return await start_job(
        session_id=session_id, workspace=workspace, repository=repository, backend=backend, task=task,
        label=base.session_label, revision=scope,
    )
