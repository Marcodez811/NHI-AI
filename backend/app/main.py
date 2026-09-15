from contextlib import asynccontextmanager
import asyncio
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlmodel import Session, select

from app.api.routes.chat import get_chat_document_repository, get_chat_service, router as chat_router
from app.api.routes.documents import get_document_repository, router as documents_router
from app.api.routes.dev_agents import router as dev_agents_router
from app.api.routes.retrieval import (
    get_retrieval_document_repository,
    get_retrieval_registry,
    router as retrieval_router,
)
from app.api.routes.slides import get_slide_job_repository, router as slides_router
from app.broker import documents_broker, tasks_broker
from app.config import settings
from app.db import engine, get_session, init_db
from app.models.documents import Document
from app.services.documents.repository import SQLModelDocumentRepository
from app.services.slides.repository import SQLModelSlideJobRepository
from app.services.chat.responder import ResponseService
from app.services.retrieval.registry import RetrievalIndexRegistry
from app.tasks.documents import set_retrieval_index_registry

redis_client = redis.from_url(settings.redis_url)
retrieval_registry = RetrievalIndexRegistry()
set_retrieval_index_registry(retrieval_registry)
_chat_client: Any | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(init_db)
    # Vector-store bootstrap is deliberately best-effort.  The process can
    # serve document catalog/status requests while OpenAI is unavailable; the
    # ingestion worker retries through the same durable registry later.
    try:
        # Startup performs one remote validation for a persisted store.  The
        # normal worker/request paths use ``ensure_ready`` only when they need
        # to adopt or create an unready store, avoiding an OpenAI round-trip
        # on every request.
        await asyncio.to_thread(retrieval_registry.validate_ready)
    except Exception:
        # The registry persists a safe error state for expected provider
        # failures.  Keep this boundary defensive so a provider SDK change
        # never prevents the API from starting.
        pass
    brokers = (documents_broker, tasks_broker)
    started: list[object] = []
    try:
        for broker in brokers:
            if getattr(broker, "is_worker_process", False):
                continue
            # Record before startup: Taskiq startup can allocate resources and
            # then raise, so its shutdown still belongs in partial-startup
            # cleanup.
            started.append(broker)
            await broker.startup()
        yield
    finally:
        try:
            try:
                await redis_client.aclose()
            finally:
                if _chat_client is not None:
                    await _chat_client.close()
        finally:
            for broker in reversed(started):
                try:
                    await broker.shutdown()
                except Exception:
                    # Shutdown must not mask the original startup/request
                    # exception, and one broker failing must not leak the other.
                    pass

app = FastAPI(lifespan=lifespan)


def _document_repository_from_database():
    yield from (SQLModelDocumentRepository(session) for session in get_session())


app.dependency_overrides[get_document_repository] = _document_repository_from_database


def _slide_job_repository_from_database():
    yield from (SQLModelSlideJobRepository(session) for session in get_session())


app.dependency_overrides[get_slide_job_repository] = _slide_job_repository_from_database
app.dependency_overrides[get_chat_document_repository] = _document_repository_from_database
app.dependency_overrides[get_retrieval_document_repository] = _document_repository_from_database
app.dependency_overrides[get_retrieval_registry] = lambda: retrieval_registry


def _document_id_for_remote_file(file_id: str):
    with Session(engine) as session:
        document = session.exec(select(Document).where(Document.remote_file_id == file_id)).first()
    return document.id if document else None


def _runtime_vector_store_id() -> str | None:
    """Resolve the store from durable state, with a legacy env fallback.

    The fallback keeps existing deployments and unit tests working before the
    registry table is initialized.  Once bootstrap has persisted a row, the
    database value is the source of truth.
    """

    try:
        # Once the registry table exists, its state is authoritative even if
        # it is currently provisioning/error.  The environment fallback is
        # only for legacy databases that predate the table.
        if retrieval_registry.get_record() is not None:
            return retrieval_registry.get_ready_id()
        return settings.openai_vector_store_id
    except Exception:
        return settings.openai_vector_store_id


def _make_chat_service() -> ResponseService:
    from openai import AsyncOpenAI

    global _chat_client
    if _chat_client is None:
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "chat_unavailable", "message": "對話服務尚未完成設定。"},
            )
        try:
            _chat_client = AsyncOpenAI(api_key=api_key)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"},
            ) from exc
    return ResponseService(
        client=_chat_client,
        vector_store_id_provider=_runtime_vector_store_id,
        model=settings.openai_chat_model,
        document_id_for_file=_document_id_for_remote_file,
    )


app.dependency_overrides[get_chat_service] = _make_chat_service

app.include_router(chat_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(retrieval_router, prefix="/api/v1")
app.include_router(slides_router, prefix="/api/v1")
if settings.enable_agent_dev_routes:
    # This console has no authentication of its own; only enable it on a
    # trusted development network through explicit configuration.
    app.include_router(dev_agents_router, prefix="/api/v1")

@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health")
async def health():
    redis_ok = False
    database_ok = False
    try:
        pong = await redis_client.ping()
        redis_ok = bool(pong)
    except Exception:
        pass
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
        database_ok = True
    except Exception:
        pass
    if not (redis_ok and database_ok):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "redis": redis_ok, "database": database_ok},
        )
    return {"status": "ok", "redis": redis_ok, "database": database_ok}
