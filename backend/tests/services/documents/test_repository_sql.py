"""SQL document repository identity and unique-race tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlmodel import Session, create_engine

from app.models.documents import Document, DocumentStatus
from app.services.documents.repository import (
    DuplicateDocumentError,
    SQLModelDocumentRepository,
)


def _migrated_engine(tmp_path: Path):
    path = tmp_path / "repository.db"
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def _document(*, checksum: str = "same", status: str = DocumentStatus.QUEUED.value) -> Document:
    return Document(
        id=uuid4(),
        original_filename="brief.pdf",
        display_name="brief.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=1,
        checksum=checksum,
        category="legislative_qa",
        storage_key=f"documents/{uuid4()}",
        status=status,
    )


@pytest.mark.asyncio
async def test_sql_repository_allows_replacement_after_delete_failed(tmp_path):
    engine = _migrated_engine(tmp_path)
    first = _document()
    with Session(engine) as session:
        await SQLModelDocumentRepository(session).create_document(first)
        first.status = DocumentStatus.DELETE_FAILED.value
        await SQLModelDocumentRepository(session).update_document(first)
        await SQLModelDocumentRepository(session).create_document(_document())


@pytest.mark.asyncio
async def test_sql_repository_translates_unique_index_race(tmp_path, monkeypatch):
    engine = _migrated_engine(tmp_path)
    winner = _document(checksum="winner")
    with Session(engine) as session:
        await SQLModelDocumentRepository(session).create_document(winner)

    candidate = _document()
    with Session(engine) as session:
        original_exec = session.exec
        first_lookup = True

        def race_exec(statement, *args, **kwargs):
            nonlocal first_lookup
            if first_lookup:
                first_lookup = False
                # Simulate the repository's pre-check missing a row that was
                # committed by another request immediately beforehand.
                class EmptyResult:
                    def first(self):
                        return None
                return EmptyResult()
            return original_exec(statement, *args, **kwargs)

        # Seed the race winner in a separate session and make the candidate
        # match it instead.
        competing_doc = _document(checksum=candidate.checksum)
        with Session(engine) as competing:
            competing.add(competing_doc)
            competing.commit()
            competing_id = competing_doc.id

        monkeypatch.setattr(session, "exec", race_exec)
        with pytest.raises(DuplicateDocumentError) as caught:
            await SQLModelDocumentRepository(session).create_document(candidate)
        assert caught.value.existing_id == competing_id
