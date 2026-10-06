"""Artifact catalog and storage: generated outputs the user can find, download and delete.

Each artifact owns a stored copy under ``<documents_root>/artifacts/<id><ext>``;
deleting it never touches the original job output
(docs/9_29_files_and_artifacts_spec.md).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlmodel import Session, col, select

from app.config import settings
from app.models.artifacts import Artifact, ArtifactKind, ArtifactWorkflow

logger = logging.getLogger(__name__)

PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
ARTIFACTS_DIR = "artifacts"


class ArtifactStorageError(RuntimeError):
    pass


class ArtifactStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(settings.documents_root)

    def _base(self) -> Path:
        return (self.root / ARTIFACTS_DIR).resolve()

    def copy_in(self, source: Path, artifact_id: UUID, extension: str) -> tuple[str, int]:
        directory = self._base()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{artifact_id}{extension}"
        temporary = directory / f".{artifact_id}{extension}.tmp"
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ArtifactStorageError("artifact could not be stored") from exc
        return f"{ARTIFACTS_DIR}/{destination.name}", destination.stat().st_size

    def resolve(self, storage_key: str) -> Path:
        root = self.root.resolve()
        path = (root / storage_key).resolve()
        try:
            path.relative_to(self._base())
        except ValueError as exc:
            raise ArtifactStorageError("artifact path is invalid") from exc
        if not path.is_file():
            raise ArtifactStorageError("artifact file is missing")
        return path

    def delete(self, storage_key: str) -> None:
        try:
            self.resolve(storage_key).unlink(missing_ok=True)
        except ArtifactStorageError:
            return


@runtime_checkable
class ArtifactRepository(Protocol):
    async def create(self, artifact: Artifact) -> Artifact: ...

    async def list(self) -> list[Artifact]: ...

    async def get(self, artifact_id: UUID) -> Artifact | None: ...

    async def get_by_job(self, source_workflow: str, job_id: UUID) -> Artifact | None: ...

    async def delete(self, artifact_id: UUID) -> None: ...


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self.artifacts: dict[UUID, Artifact] = {}

    async def create(self, artifact: Artifact) -> Artifact:
        self.artifacts[artifact.id] = artifact
        return artifact

    async def list(self) -> list[Artifact]:
        return sorted(self.artifacts.values(), key=lambda item: item.created_at, reverse=True)

    async def get(self, artifact_id: UUID) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    async def get_by_job(self, source_workflow: str, job_id: UUID) -> Artifact | None:
        return next(
            (a for a in self.artifacts.values() if a.source_workflow == source_workflow and a.source_job_id == job_id),
            None,
        )

    async def delete(self, artifact_id: UUID) -> None:
        self.artifacts.pop(artifact_id, None)


class SQLModelArtifactRepository(InMemoryArtifactRepository):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    async def create(self, artifact: Artifact) -> Artifact:
        self.session.add(artifact)
        self.session.commit()
        self.session.refresh(artifact)
        return artifact

    async def list(self) -> list[Artifact]:
        return list(self.session.exec(select(Artifact).order_by(col(Artifact.created_at).desc())).all())

    async def get(self, artifact_id: UUID) -> Artifact | None:
        return self.session.get(Artifact, artifact_id)

    async def get_by_job(self, source_workflow: str, job_id: UUID) -> Artifact | None:
        return self.session.exec(
            select(Artifact).where(Artifact.source_workflow == source_workflow, Artifact.source_job_id == job_id)
        ).first()

    async def delete(self, artifact_id: UUID) -> None:
        artifact = self.session.get(Artifact, artifact_id)
        if artifact is not None:
            self.session.delete(artifact)
            self.session.commit()


def record_slide_artifact(
    session: Session,
    *,
    job_id: UUID,
    title: str,
    published: Path,
    storage: ArtifactStorage | None = None,
) -> Artifact | None:
    """Create the artifact record for a published deck (idempotent per job)."""

    existing = session.exec(
        select(Artifact).where(Artifact.source_workflow == ArtifactWorkflow.SLIDES.value, Artifact.source_job_id == job_id)
    ).first()
    if existing is not None:
        return existing
    storage = storage or ArtifactStorage()
    artifact_id = uuid4()
    storage_key, size = storage.copy_in(published, artifact_id, ".pptx")
    artifact = Artifact(
        id=artifact_id,
        kind=ArtifactKind.SLIDE_DECK.value,
        title=title[:512],
        mime_type=PPTX_MIME_TYPE,
        size_bytes=size,
        storage_key=storage_key,
        source_workflow=ArtifactWorkflow.SLIDES.value,
        source_job_id=job_id,
    )
    session.add(artifact)
    session.commit()
    return artifact


def artifact_download_name(artifact: Artifact) -> str:
    cleaned = "".join(ch for ch in artifact.title.strip() if ch.isalnum() or ch in " ._-")
    cleaned = " ".join(cleaned.split()).strip(" ._-")[:120] or "artifact"
    return f"{cleaned}{Path(artifact.storage_key).suffix}"
