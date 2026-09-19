from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from taskiq_redis.exceptions import ResultIsMissingError

from app.api.routes.news import create_news_job, get_news_job
from app.models.news import GenerateNewsRequest
from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.services.agentic.contracts import AgentPhase


class Backend:
    def __init__(self):
        self.progress = {}
        self.result = None

    async def set_progress(self, key, progress):
        self.progress[key] = progress

    async def get_progress(self, key):
        return self.progress.get(key)

    async def get_result(self, key):
        if self.result is None:
            raise ResultIsMissingError
        return self.result


class Task:
    def kicker(self):
        return self

    def with_task_id(self, value):
        self.task_id = value
        return self

    async def kiq(self, payload):
        self.payload = payload


class Repository:
    def __init__(self, document):
        self.document = document

    async def get_document(self, document_id):
        return self.document if self.document.id == document_id else None


@pytest.mark.asyncio
async def test_news_job_queues_generic_task_and_returns_article():
    document = Document(original_filename="source.txt", display_name="source.txt", extension=".txt", mime_type="text/plain", size_bytes=5, checksum="abc", category=DocumentCategory.BEI_CAN, status=DocumentStatus.READY)
    backend, task = Backend(), Task()
    created = await create_news_job(GenerateNewsRequest(document_ids=[document.id], guidance="Focus on access"), backend, task, Repository(document))
    assert task.task_id == str(created.job_id)
    assert task.payload.workflow == "news"
    assert task.payload.input["document_ids"] == [str(document.id)]
    assert (await get_news_job(created.job_id, backend)).phase == AgentPhase.QUEUED
    backend.result = SimpleNamespace(return_value={"workflow": "news", "status": "completed", "output": {"job_id": str(created.job_id), "article": "# 健保新制\n\n健保署表示，符合資格的民眾可依公告申請。"}})
    completed = await get_news_job(created.job_id, backend)
    assert completed.status == "completed"
    assert completed.article.startswith("# 健保新制")


@pytest.mark.asyncio
async def test_news_job_rejects_unready_source():
    document = Document(original_filename="source.txt", display_name="source.txt", extension=".txt", mime_type="text/plain", size_bytes=5, checksum="abc", category=DocumentCategory.BEI_CAN, status=DocumentStatus.INDEXING)
    with pytest.raises(HTTPException) as error:
        await create_news_job(GenerateNewsRequest(document_ids=[document.id]), Backend(), Task(), Repository(document))
    assert error.value.status_code == 409
