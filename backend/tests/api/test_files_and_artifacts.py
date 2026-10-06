"""My files, knowledge-base attachments, promote and artifacts (docs/9_29_files_and_artifacts_spec.md)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes.artifacts import delete_artifact, download_artifact, list_artifacts
from app.api.routes.chat import (
    attach_chat_documents,
    create_chat_session,
    delete_chat_session,
    detach_chat_document,
    get_chat_session,
    link_chat_files,
    unlink_chat_file,
    upload_chat_attachment,
)
from app.api.routes.files import PromoteFileRequest, delete_file, get_file_content, list_files, promote_file
from app.models.artifacts import Artifact, ArtifactKind
from app.models.chat import LinkDocumentsRequest, LinkFilesRequest, ChatSessionCreate
from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.services.artifacts import (
    ArtifactStorage,
    InMemoryArtifactRepository,
    SQLModelArtifactRepository,
    record_slide_artifact,
)
from app.services.chat.attachments import ChatAttachmentStorage
from app.services.chat.repository import InMemoryChatRepository
from app.services.documents.repository import InMemoryDocumentRepository
from app.services.documents.storage import LocalDocumentStorage
from tests.api.test_chat_messages import FakeUpload


class FakeKicker:
    def __init__(self, task):
        self.task = task

    def with_task_id(self, task_id):
        return self

    async def kiq(self, payload):
        self.task.payloads.append(payload)


class FakeTask:
    def __init__(self):
        self.payloads = []

    def kicker(self):
        return FakeKicker(self)


def _document(*, status=DocumentStatus.READY, retrieval_enabled=True, extension=".pdf", size=10) -> Document:
    return Document(
        original_filename=f"kb{extension}", display_name=f"知識庫文件{extension}",
        mime_type="application/octet-stream", extension=extension, size_bytes=size, checksum=str(uuid4()),
        category=DocumentCategory.LEGISLATIVE_QA.value, storage_key="x/kb" + extension,
        status=status.value, retrieval_enabled=retrieval_enabled,
    )


@pytest.mark.asyncio
async def test_deleting_a_conversation_keeps_its_files_and_bytes(tmp_path):
    repository, storage = InMemoryChatRepository(), ChatAttachmentStorage(tmp_path)
    session = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(session.id, FakeUpload("note.txt", b"hello"), repository, storage)

    await delete_chat_session(session.id, repository)

    files = await list_files(repository)
    assert [item.display_name for item in files] == ["note.txt"]
    assert files[0].origin_session_id is None and files[0].origin_session_title is None
    assert storage.resolve((await repository.get_file(attachment.id)).storage_key).read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_deleting_a_file_removes_links_and_bytes(tmp_path):
    repository, storage = InMemoryChatRepository(), ChatAttachmentStorage(tmp_path)
    session = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(session.id, FakeUpload("note.txt", b"hello"), repository, storage)
    path = storage.resolve((await repository.get_file(attachment.id)).storage_key)

    await delete_file(attachment.id, repository, storage)

    assert not path.exists() and not storage.text_path(path).exists()
    assert (await get_chat_session(session.id, repository)).attachments == []
    with pytest.raises(HTTPException) as error:
        await delete_file(attachment.id, repository, storage)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_linking_a_file_into_a_second_conversation_makes_it_visible_there(tmp_path):
    repository, storage = InMemoryChatRepository(), ChatAttachmentStorage(tmp_path)
    first = await create_chat_session(ChatSessionCreate(), repository)
    second = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(first.id, FakeUpload("note.txt", b"hello"), repository, storage)

    linked = await link_chat_files(second.id, LinkFilesRequest(file_ids=[attachment.id]), repository)

    assert linked[0].source.value == "upload"
    detail = await get_chat_session(second.id, repository)
    assert [item.id for item in detail.attachments] == [attachment.id]
    response = await get_file_content(attachment.id, repository, storage)
    assert response.media_type == "text/plain"

    await unlink_chat_file(second.id, attachment.id, repository)
    assert (await get_chat_session(second.id, repository)).attachments == []
    assert await repository.get_file(attachment.id) is not None
    assert len((await get_chat_session(first.id, repository)).attachments) == 1


@pytest.mark.asyncio
async def test_attach_knowledge_base_documents_lists_them_with_source():
    repository, documents = InMemoryChatRepository(), InMemoryDocumentRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    ready = await documents.create_document(_document())

    attached = await attach_chat_documents(session.id, LinkDocumentsRequest(document_ids=[ready.id]), repository, documents)

    assert attached[0].source.value == "knowledge_base"
    assert attached[0].mime_type == "application/pdf"
    detail = await get_chat_session(session.id, repository, documents)
    assert [(item.display_name, item.source.value) for item in detail.attachments] == [("知識庫文件.pdf", "knowledge_base")]

    await detach_chat_document(session.id, ready.id, repository)
    assert (await get_chat_session(session.id, repository, documents)).attachments == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs", [{"status": DocumentStatus.QUEUED}, {"status": DocumentStatus.INDEXING}, {"retrieval_enabled": False}]
)
async def test_non_ready_or_disabled_documents_are_rejected(kwargs):
    repository, documents = InMemoryChatRepository(), InMemoryDocumentRepository()
    session = await create_chat_session(ChatSessionCreate(), repository)
    document = await documents.create_document(_document(**kwargs))

    with pytest.raises(HTTPException) as error:
        await attach_chat_documents(session.id, LinkDocumentsRequest(document_ids=[document.id]), repository, documents)

    assert error.value.status_code == 422
    assert await repository.list_session_document_ids(session.id) == []


@pytest.mark.asyncio
async def test_promote_creates_a_document_through_ingestion_and_conflicts_on_repeat(tmp_path):
    repository, storage = InMemoryChatRepository(), ChatAttachmentStorage(tmp_path / "chat")
    documents, task = InMemoryDocumentRepository(), FakeTask()
    document_storage = LocalDocumentStorage(tmp_path / "docs")
    session = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(session.id, FakeUpload("note.txt", "健保給付".encode()), repository, storage)
    payload = PromoteFileRequest(category=DocumentCategory.LEGISLATIVE_QA)

    result = await promote_file(attachment.id, payload, repository, storage, documents, document_storage, task)

    assert result.in_knowledge_base is True
    stored = await repository.get_file(attachment.id)
    document = await documents.get_document(stored.promoted_document_id)
    assert document.display_name == "note.txt" and document.status == DocumentStatus.QUEUED.value
    assert len(task.payloads) == 1 and task.payloads[0].document_id == document.id
    assert document_storage.resolve(document.id).read_bytes() == "健保給付".encode()

    with pytest.raises(HTTPException) as error:
        await promote_file(attachment.id, payload, repository, storage, documents, document_storage, task)
    assert error.value.status_code == 409 and "已加入知識庫" in error.value.detail


@pytest.mark.asyncio
async def test_promote_rejects_images(tmp_path):
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    repository, storage = InMemoryChatRepository(), ChatAttachmentStorage(tmp_path / "chat")
    session = await create_chat_session(ChatSessionCreate(), repository)
    attachment = await upload_chat_attachment(
        session.id, FakeUpload("pic.png", buffer.getvalue(), "image/png"), repository, storage
    )

    with pytest.raises(HTTPException) as error:
        await promote_file(
            attachment.id, PromoteFileRequest(category=DocumentCategory.LEGISLATIVE_QA), repository, storage,
            InMemoryDocumentRepository(), LocalDocumentStorage(tmp_path / "docs"), FakeTask(),
        )
    assert error.value.status_code == 422
    assert (await repository.get_file(attachment.id)).promoted_document_id is None


# ── Artifacts ───────────────────────────────────────────────────────────────


def _sqlite_session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'artifacts.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_slide_publish_creates_one_artifact_per_job(tmp_path):
    deck = tmp_path / "job.pptx"
    deck.write_bytes(b"deck")
    storage = ArtifactStorage(tmp_path / "documents")
    job_id = uuid4()
    with _sqlite_session(tmp_path) as session:
        artifact = record_slide_artifact(session, job_id=job_id, title="年度簡報", published=deck, storage=storage)
        again = record_slide_artifact(session, job_id=job_id, title="年度簡報", published=deck, storage=storage)

        assert again.id == artifact.id
        assert artifact.kind == ArtifactKind.SLIDE_DECK.value and artifact.source_job_id == job_id
        assert storage.resolve(artifact.storage_key).read_bytes() == b"deck"


@pytest.mark.asyncio
async def test_artifact_list_download_and_delete_keep_the_original(tmp_path):
    original = tmp_path / "published.pptx"
    original.write_bytes(b"deck")
    storage = ArtifactStorage(tmp_path / "documents")
    with _sqlite_session(tmp_path) as session:
        record_slide_artifact(session, job_id=uuid4(), title="年度/簡報", published=original, storage=storage)
        repository = SQLModelArtifactRepository(session)

        listed = await list_artifacts(repository)
        assert set(listed[0].model_dump()) == {
            "id", "kind", "title", "mime_type", "size_bytes", "source_workflow", "source_job_id", "created_at",
        }
        response = await download_artifact(listed[0].id, repository, storage)
        assert response.filename == "年度簡報.pptx"
        assert "documents" in str(response.path)
        stored_path = response.path

        await delete_artifact(listed[0].id, repository, storage)

        assert not __import__("pathlib").Path(stored_path).exists()
        assert original.read_bytes() == b"deck"
        assert await list_artifacts(repository) == []
        with pytest.raises(HTTPException) as error:
            await download_artifact(listed[0].id, repository, storage)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_artifact_file_404s(tmp_path):
    repository = InMemoryArtifactRepository()
    artifact = await repository.create(Artifact(
        kind="report", title="報告", mime_type="text/plain", size_bytes=1,
        storage_key="artifacts/none.txt", source_workflow="chat",
    ))
    with pytest.raises(HTTPException) as error:
        await download_artifact(artifact.id, repository, ArtifactStorage(tmp_path))
    assert error.value.status_code == 404
