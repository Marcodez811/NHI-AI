"""立院QA AI writer: start a ``legislative_qa`` agentic job and report its progress."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.routes.artifacts import get_artifact_repository, get_artifact_storage
from app.api.routes.chat import _load_qa, _qa_http_error, _save_qa, get_chat_repository
from app.api.routes.news import get_generate_news_task, get_result_backend
from app.models.legislative_qa import GenerateQaRequest, QaJobStatusResponse, QaVersionContent, ReviseQaRequest
from app.services.artifacts import ArtifactRepository, ArtifactStorage
from app.services.chat.repository import ChatRepository
from app.services.chat.skills import legislative_qa as qa
from app.services.legislative_qa.jobs import ACTIVE, load_version, read_job, start_job, start_revision
from app.services.legislative_qa.writer import build_writer_input

router = APIRouter(tags=["legislative-qa"])

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
        return await start_job(
            session_id=session_id, workspace=workspace, repository=repository, backend=backend, task=task,
            label=(payload.session_label or "").strip() or None,
        )
    except qa.QaError as error:
        raise _qa_http_error(error) from error


@router.post("/chat/sessions/{session_id}/qa/revise", status_code=status.HTTP_202_ACCEPTED, response_model=QaJobStatusResponse)
async def revise_qa_document(
    session_id: UUID,
    payload: ReviseQaRequest,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    backend: Annotated[Any, Depends(get_result_backend)],
    task: Annotated[Any, Depends(get_generate_news_task)],
    artifacts: Annotated[ArtifactRepository, Depends(get_artifact_repository)],
    storage: Annotated[ArtifactStorage, Depends(get_artifact_storage)],
) -> QaJobStatusResponse:
    workspace = await _load_qa(session_id, repository)
    try:
        return await start_revision(
            session_id=session_id, workspace=workspace, request=payload, repository=repository, backend=backend,
            task=task, artifacts=artifacts, storage=storage,
        )
    except qa.QaError as error:
        raise _qa_http_error(error) from error


@router.get("/chat/sessions/{session_id}/qa/versions/{artifact_id}", response_model=QaVersionContent)
async def get_qa_version(
    session_id: UUID,
    artifact_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    artifacts: Annotated[ArtifactRepository, Depends(get_artifact_repository)],
    storage: Annotated[ArtifactStorage, Depends(get_artifact_storage)],
) -> QaVersionContent:
    workspace = await _load_qa(session_id, repository)
    try:
        entry, document = await load_version(workspace, artifact_id, artifacts, storage)
    except qa.QaError as error:
        raise _qa_http_error(error) from error
    return QaVersionContent(
        artifact_id=artifact_id, kind=entry.get("kind", "outline"), created_at=entry.get("created_at"),
        download_url=f"/api/v1/artifacts/{artifact_id}/download", document=document,
    )


@router.get("/chat/sessions/{session_id}/qa/generate/{job_id}", response_model=QaJobStatusResponse)
async def get_qa_generation(
    session_id: UUID,
    job_id: UUID,
    repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    backend: Annotated[Any, Depends(get_result_backend)],
) -> QaJobStatusResponse:
    workspace = await _load_qa(session_id, repository)
    state = await read_job(backend, job_id)
    if state is not None and state.status in ACTIVE:
        return state
    if (workspace.generation or {}).get("job_id") == str(job_id):
        # The job ended (or vanished): release the workspace so a new run can start.
        workspace.generation = {}
        await _save_qa(workspace, repository)
    if state is None:
        raise HTTPException(status_code=404, detail="找不到這個撰寫工作。")
    return state
