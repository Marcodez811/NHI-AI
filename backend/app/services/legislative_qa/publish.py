"""Render a QaDocument and store it as a ``report`` artifact (file + structured JSON side by side)."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.models.artifacts import Artifact, ArtifactKind, ArtifactWorkflow
from app.models.chat import ChatQaWorkspace
from app.services.artifacts import DOCX_MIME_TYPE, ArtifactRepository, ArtifactStorage
from app.services.legislative_qa.document import QaDocument
from app.services.legislative_qa.render import render_docx


async def publish_document(
    document: QaDocument,
    *,
    session_id: UUID,
    repository: ArtifactRepository,
    storage: ArtifactStorage,
) -> Artifact:
    artifact_id = uuid4()
    storage_key, size = storage.write_bytes(render_docx(document), artifact_id, ".docx")
    storage.write_bytes(document.model_dump_json().encode("utf-8"), artifact_id, ".json")
    artifact = Artifact(
        id=artifact_id,
        kind=ArtifactKind.REPORT.value,
        title=document.title[:512],
        mime_type=DOCX_MIME_TYPE,
        size_bytes=size,
        storage_key=storage_key,
        source_workflow=ArtifactWorkflow.CHAT.value,
        session_id=session_id,
    )
    return await repository.create(artifact)


def record_version(
    workspace: ChatQaWorkspace, artifact: Artifact, kind: str = "outline", document: QaDocument | None = None
) -> None:
    """Newest first; ``versions`` is the workspace's history of produced files (``kind``: outline | ai)."""

    entry = {
        "kind": kind,
        "artifact_id": str(artifact.id),
        "title": artifact.title,
        "created_at": artifact.created_at.isoformat(),
        "download_url": f"/api/v1/artifacts/{artifact.id}/download",
    }
    if document is not None and document.checked:
        entry |= {"checked": True, "issues": [issue.model_dump() for issue in document.issues], "review": document.review}
    if document is not None and document.revised_from:
        entry |= {"revised_from": document.revised_from, "revision_note": document.revision_note}
    workspace.versions = [entry, *workspace.versions]
