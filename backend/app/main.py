from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlmodel import Session, select

from app.api.routes.chat import get_chat_document_repository, get_chat_service, router as chat_router
from app.api.routes.documents import get_document_repository, router as documents_router
from app.api.routes.slides import router as slides_router
from app.broker import broker
from app.config import settings
from app.db import engine, get_session, init_db
from app.models.documents import Document
from app.services.documents.repository import SQLModelDocumentRepository
from app.services.chat.responder import ResponseService

redis_client = redis.from_url(settings.redis_url)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    owns_broker_lifecycle = not getattr(broker, "is_worker_process", False)
    if owns_broker_lifecycle:
        await broker.startup()
    try:
        yield
    finally:
        await redis_client.aclose()
        if owns_broker_lifecycle:
            await broker.shutdown()

app = FastAPI(lifespan=lifespan)


def _document_repository_from_database():
    yield from (SQLModelDocumentRepository(session) for session in get_session())


app.dependency_overrides[get_document_repository] = _document_repository_from_database
app.dependency_overrides[get_chat_document_repository] = _document_repository_from_database


def _document_id_for_remote_file(file_id: str):
    with Session(engine) as session:
        document = session.exec(select(Document).where(Document.remote_file_id == file_id)).first()
    return document.id if document else None


app.dependency_overrides[get_chat_service] = lambda: ResponseService(
    vector_store_id=settings.openai_vector_store_id,
    model=settings.openai_chat_model,
    document_id_for_file=_document_id_for_remote_file,
)

app.include_router(chat_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(slides_router, prefix="/api/v1")

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
