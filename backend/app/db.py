"""Database engine and request-scoped SQLModel sessions."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from app.config import settings

# Import table models so SQLModel.metadata includes them before create_all.
from app.models.documents import Document, Folder, IngestionJob
from app.models.agent_settings import AgentStageSettings
from app.models.artifacts import Artifact
from app.models.chat import ChatMessage, ChatSession, ChatSessionDocument, ChatSessionFile, UserFile
from app.models.retrieval import RetrievalIndex
from app.models.slides import SlideJob


def _engine_kwargs(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine: Engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))


def init_db() -> None:
    """Verify that migrations have run.

    Schema creation is intentionally owned by the deployment migration job.
    The name remains for worker compatibility; callers should use
    ``check_schema_current`` when they want an explicit startup check.
    """

    check_schema_current()


def check_schema_current(db_engine: Engine | None = None) -> None:
    """Fail fast when the running process is pointed at an old schema.

    This check is read-only and compares the database's Alembic revision with
    the repository head. Unit tests that use ``SQLModel.metadata.create_all``
    should call their task functions with ``init_db`` patched, as before.
    """

    db_engine = db_engine or engine
    inspector = inspect(db_engine)
    if "alembic_version" not in inspector.get_table_names():
        raise RuntimeError("Database schema is not migrated: alembic_version is missing")

    with db_engine.connect() as connection:
        versions = {
            str(row[0])
            for row in connection.execute(text("SELECT version_num FROM alembic_version"))
        }
    if not versions:
        raise RuntimeError("Database schema is not migrated: Alembic revision is missing")

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None or versions != {head}:
        raise RuntimeError(
            f"Database schema revision mismatch: current={sorted(versions)}, expected={head}"
        )


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
