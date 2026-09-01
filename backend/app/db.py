"""Database engine and request-scoped SQLModel sessions."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# Import table models so SQLModel.metadata includes them before create_all.
from app.models.documents import Document, Folder, IngestionJob
from app.models.retrieval import RetrievalIndex


def _engine_kwargs(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine: Engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
