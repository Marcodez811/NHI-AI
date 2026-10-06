"""Generated outputs (「產出的文件」): list, download, delete."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.models.artifacts import ArtifactRead
from app.services.artifacts import (
    ArtifactRepository,
    ArtifactStorage,
    ArtifactStorageError,
    InMemoryArtifactRepository,
    artifact_download_name,
)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_repository = InMemoryArtifactRepository()


def get_artifact_repository() -> ArtifactRepository:
    """Default dependency; production overrides this with a SQLModel session repository."""

    return _repository


def get_artifact_storage() -> ArtifactStorage:
    return ArtifactStorage()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此文件。")


@router.get("", response_model=list[ArtifactRead])
async def list_artifacts(
    repository: Annotated[ArtifactRepository, Depends(get_artifact_repository)],
) -> list[ArtifactRead]:
    return [ArtifactRead.model_validate(item) for item in await repository.list()]


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: UUID,
    repository: Annotated[ArtifactRepository, Depends(get_artifact_repository)],
    storage: Annotated[ArtifactStorage, Depends(get_artifact_storage)],
) -> FileResponse:
    artifact = await repository.get(artifact_id)
    if artifact is None:
        raise _not_found()
    try:
        path = storage.resolve(artifact.storage_key)
    except ArtifactStorageError as exc:
        raise _not_found() from exc
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact_download_name(artifact))


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID,
    repository: Annotated[ArtifactRepository, Depends(get_artifact_repository)],
    storage: Annotated[ArtifactStorage, Depends(get_artifact_storage)],
) -> None:
    artifact = await repository.get(artifact_id)
    if artifact is None:
        raise _not_found()
    storage.delete(artifact.storage_key)
    await repository.delete(artifact_id)
