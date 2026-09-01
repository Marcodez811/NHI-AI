from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"expected patch context not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1))


# 1. Public chat requests cannot select the provider vector store.
replace(
    "backend/app/models/chat.py",
    "    # This is useful for isolated clients and tests. Production callers should\n    # leave it unset so the injected provider/configuration selects the index.\n    vector_store_id: str | None = Field(default=None, min_length=1)\n",
    "",
)
replace("frontend/lib/api.ts", "    vector_store_id?: string;\n", "")
replace(
    "backend/app/services/chat/responder.py",
    "    def resolve_vector_store_id(self, explicit_id: str | None = None) -> str:\n        value = explicit_id or self._vector_store_id or self._vector_store_id_provider()\n",
    "    def resolve_vector_store_id(self) -> str:\n        value = self._vector_store_id or self._vector_store_id_provider()\n",
)
replace(
    "backend/app/services/chat/responder.py",
    "        vector_store_id = self.resolve_vector_store_id(request.vector_store_id)\n",
    "        vector_store_id = self.resolve_vector_store_id()\n",
)

# 2. Category-wide chat is restricted to the exact DB-authorized documents.
replace(
    "backend/app/api/routes/chat.py",
    '''async def _validate_document_scope(request: ChatRequest, repository: DocumentRepository) -> None:
    # An empty catalog is a normal first-run state.  Return a stable conflict
    # contract so the UI can direct the user to upload a source instead of
    # sending a guaranteed-unproductive Responses API request.
    if not request.document_ids:
        ready_documents = await repository.list_documents(
            category=request.mode,
            status=DocumentStatus.READY,
            retrieval_enabled=True,
        )
        if not ready_documents:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "knowledge_base_empty",
                    "message": "No retrieval-ready documents are available for this category.",
                },
            )
    for document_id in request.document_ids:
        document = await repository.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="A selected document was not found.")
        if document.category != request.mode.value:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selected documents must match the chat category.")
        if document.status != DocumentStatus.READY.value or not document.retrieval_enabled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Selected documents are not ready for retrieval.")
''',
    '''async def _resolve_document_scope(request: ChatRequest, repository: DocumentRepository) -> ChatRequest:
    """Resolve every chat request to DB-authorized document IDs."""

    if not request.document_ids:
        ready_documents = await repository.list_documents(
            category=request.mode,
            status=DocumentStatus.READY,
            retrieval_enabled=True,
        )
        if not ready_documents:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "knowledge_base_empty",
                    "message": "No retrieval-ready documents are available for this category.",
                },
            )
        return request.model_copy(update={"document_ids": [document.id for document in ready_documents]})

    for document_id in request.document_ids:
        document = await repository.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="A selected document was not found.")
        if document.category != request.mode.value:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Selected documents must match the chat category.")
        if document.status != DocumentStatus.READY.value or not document.retrieval_enabled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Selected documents are not ready for retrieval.")
    return request
''',
)
replace(
    "backend/app/api/routes/chat.py",
    "    await _validate_document_scope(request, repository)\n    try:\n        return service.answer(request)\n",
    "    request = await _resolve_document_scope(request, repository)\n    try:\n        return service.answer(request)\n",
)
replace(
    "backend/app/api/routes/chat.py",
    "    await _validate_document_scope(request, repository)\n    events: Generator[str, None, None] = service.answer_stream(request)\n",
    "    request = await _resolve_document_scope(request, repository)\n    events: Generator[str, None, None] = service.answer_stream(request)\n",
)

# 3. A category change updates vector-store attributes in place.
replace(
    "backend/app/tasks/documents.py",
    "        def load_source_and_mark_indexing() -> tuple[Path, str]:\n",
    "        def load_source_and_mark_indexing() -> tuple[Path, str, str | None, str | None]:\n",
)
replace(
    "backend/app/tasks/documents.py",
    '''                source = LocalDocumentStorage(settings.documents_root).resolve(document.id)
                return source, document.original_filename

        source, filename = await asyncio.to_thread(load_source_and_mark_indexing)

        def upload_and_attach() -> tuple[str, str]:
''',
    '''                source = LocalDocumentStorage(settings.documents_root).resolve(document.id)
                return source, document.original_filename, document.remote_file_id, document.remote_vector_store_id

        source, filename, existing_remote_file_id, existing_vector_store_file_id = await asyncio.to_thread(
            load_source_and_mark_indexing
        )

        def upload_and_attach() -> tuple[str, str]:
''',
)
replace(
    "backend/app/tasks/documents.py",
    '''            client = OpenAI(api_key=settings.openai_api_key)
            with source.open("rb") as handle:
                uploaded = client.files.create(file=handle, purpose="assistants")
            attributes = {
                "filename": filename,
                "document_id": str(payload.document_id),
                "qa_set": payload.category.value,
                "content_type": "markdown_wiki" if payload.category is DocumentCategory.BEI_CAN else "source",
                "is_news_source": "false",
            }
            attached = client.vector_stores.files.create(
                vector_store_id=vector_store_id,
                file_id=uploaded.id,
                attributes=attributes,
            )
            return str(uploaded.id), str(getattr(attached, "id", uploaded.id))
''',
    '''            client = OpenAI(api_key=settings.openai_api_key)
            attributes = {
                "filename": filename,
                "document_id": str(payload.document_id),
                "qa_set": payload.category.value,
                "content_type": "markdown_wiki" if payload.category is DocumentCategory.BEI_CAN else "source",
                "is_news_source": "false",
            }
            if existing_remote_file_id and existing_vector_store_file_id:
                client.vector_stores.files.update(
                    vector_store_id=vector_store_id,
                    file_id=existing_vector_store_file_id,
                    attributes=attributes,
                )
                return existing_remote_file_id, existing_vector_store_file_id

            with source.open("rb") as handle:
                uploaded = client.files.create(file=handle, purpose="assistants")
            attached = client.vector_stores.files.create(
                vector_store_id=vector_store_id,
                file_id=uploaded.id,
                attributes=attributes,
            )
            return str(uploaded.id), str(getattr(attached, "id", uploaded.id))
''',
)

# 4. Queueing failure keeps the source bytes because the failed row/job are kept.
replace(
    "backend/app/api/routes/documents.py",
    '''    except Exception as exc:
        try:
            await storage.delete(document_id)
        except Exception:
            pass
        if isinstance(exc, HTTPException):
            raise
        raise _map_repository_error(exc) from exc
''',
    '''    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        try:
            await storage.delete(document_id)
        except Exception:
            pass
        raise _map_repository_error(exc) from exc
''',
)

# 5. Chat client uses the Pydantic settings key, including values loaded from .env.
replace(
    "backend/app/main.py",
    "import redis.asyncio as redis\nfrom fastapi import FastAPI, HTTPException, status\n",
    "import redis.asyncio as redis\nfrom fastapi import FastAPI, HTTPException, status\nfrom openai import OpenAI\n",
)
replace(
    "backend/app/main.py",
    "app.dependency_overrides[get_chat_service] = lambda: ResponseService(\n    vector_store_id_provider=_runtime_vector_store_id,\n",
    "app.dependency_overrides[get_chat_service] = lambda: ResponseService(\n    client=OpenAI(api_key=settings.openai_api_key),\n    vector_store_id_provider=_runtime_vector_store_id,\n",
)

# Existing tests now inject the vector store through the service.
replace(
    "backend/tests/services/chat/test_chat.py",
    'def request(mode=QaMode.LEGISLATIVE_QA, **kwargs):\n    return ChatRequest(question="政策問題", mode=mode, vector_store_id="vs_test", **kwargs)\n',
    'def request(mode=QaMode.LEGISLATIVE_QA, **kwargs):\n    return ChatRequest(question="政策問題", mode=mode, **kwargs)\n',
)
p = Path("backend/tests/services/chat/test_chat.py")
text = p.read_text()
text = text.replace(
    'ResponseService(client=client, model="test-model")',
    'ResponseService(client=client, model="test-model", vector_store_id="vs_test")',
)
text = text.replace(
    'ResponseService(client=FakeClient(_response(citations=False)), model="test-model")',
    'ResponseService(client=FakeClient(_response(citations=False)), model="test-model", vector_store_id="vs_test")',
)
p.write_text(text)

Path("backend/tests/api/test_chat_scope_regressions.py").write_text('''from uuid import uuid4

import pytest

from app.api.routes.chat import _resolve_document_scope
from app.models.chat import ChatRequest, QaMode
from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.services.chat.responder import ResponseService
from app.services.documents.repository import InMemoryDocumentRepository


def make_document(*, retrieval_enabled: bool) -> Document:
    return Document(
        original_filename="brief.pdf",
        display_name="brief.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=1,
        checksum=str(uuid4()),
        category=DocumentCategory.LEGISLATIVE_QA.value,
        storage_key="ignored",
        status=DocumentStatus.READY.value,
        stage="ready",
        retrieval_enabled=retrieval_enabled,
    )


@pytest.mark.asyncio
async def test_category_scope_contains_only_retrieval_enabled_document_ids():
    repo = InMemoryDocumentRepository()
    enabled = make_document(retrieval_enabled=True)
    disabled = make_document(retrieval_enabled=False)
    await repo.create_document(enabled)
    await repo.create_document(disabled)

    request = ChatRequest(question="政策", mode=QaMode.LEGISLATIVE_QA)
    resolved = await _resolve_document_scope(request, repo)

    assert resolved.document_ids == [enabled.id]
    assert disabled.id not in resolved.document_ids


def test_client_vector_store_override_is_ignored_by_public_contract():
    request = ChatRequest.model_validate({
        "question": "政策",
        "mode": QaMode.LEGISLATIVE_QA,
        "vector_store_id": "vs_untrusted",
    })
    service = ResponseService(vector_store_id="vs_trusted", model="test-model")
    built = service.build_request(request)
    assert not hasattr(request, "vector_store_id")
    assert built["tools"][0]["vector_store_ids"] == ["vs_trusted"]
''')

Path("backend/tests/tasks/test_reindex_regressions.py").write_text('''from pathlib import Path


def test_recategorization_path_updates_existing_vector_store_file_in_place():
    source = Path("app/tasks/documents.py").read_text()
    assert "if existing_remote_file_id and existing_vector_store_file_id:" in source
    assert "client.vector_stores.files.update(" in source
    assert "return existing_remote_file_id, existing_vector_store_file_id" in source
''')
