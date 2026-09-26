"""Alembic migration tests.

Covers:
- alembic upgrade head on a fresh SQLite database succeeds
- New columns exist after migration
- Data backfill: remote_vector_store_file_id populated from remote_vector_store_id
- Duplicate (checksum, category) preflight aborts migration with a clear error
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text


def _alembic_config(db_path: str):
    from alembic.config import Config
    from pathlib import Path

    # test file is at tests/infrastructure/test_migrations.py;
    # alembic.ini is at backend/ (two parents up from tests/infrastructure/).
    backend_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(backend_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return alembic_cfg


def _run_migrations(db_path: str) -> None:
    """Run alembic upgrade head against the given SQLite file."""

    from alembic import command

    command.upgrade(_alembic_config(db_path), "head")


@pytest.fixture()
def fresh_db(tmp_path):
    """Create an empty SQLite database with the legacy schema (pre-migration)."""

    db_path = tmp_path / "test_migration.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    # Simulate the legacy schema that predates the reliability columns.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE document_folders (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    category TEXT NOT NULL,
                    folder_id TEXT REFERENCES document_folders(id),
                    storage_key TEXT NOT NULL,
                    retrieval_enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'queued',
                    stage TEXT,
                    error TEXT,
                    source_priority INTEGER,
                    roles TEXT NOT NULL DEFAULT '[]',
                    page_count INTEGER,
                    table_count INTEGER,
                    remote_file_id TEXT,
                    remote_vector_store_id TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE document_ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    stage TEXT,
                    error TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        # Insert a legacy row with a remote_vector_store_id that should be backfilled.
        conn.execute(
            text(
                """
                INSERT INTO documents (
                    id, original_filename, display_name, mime_type, extension,
                    size_bytes, checksum, category, storage_key, roles,
                    remote_file_id, remote_vector_store_id, created_at, updated_at
                ) VALUES (
                    'doc-legacy-1', 'file.pdf', 'file.pdf', 'application/pdf', '.pdf',
                    100, 'sha256-abc', 'legislative_qa', 'storage/key.pdf', '[]',
                    'file-openai-123', 'vs-file-attach-456',
                    '2026-01-01T00:00:00', '2026-01-01T00:00:00'
                )
                """
            )
        )

    engine.dispose()
    return str(db_path), url


def test_migration_succeeds_on_fresh_db(fresh_db):
    """alembic upgrade head must complete without errors."""

    db_path, url = fresh_db
    _run_migrations(db_path)

    engine = create_engine(url, connect_args={"check_same_thread": False})
    inspector = sa.inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("documents")}

    assert "ingestion_revision" in columns
    assert "pending_category" in columns
    assert "remote_vector_store_file_id" in columns
    assert "candidate_remote_file_id" in columns
    assert "candidate_remote_vector_store_file_id" in columns

    job_columns = {col["name"] for col in inspector.get_columns("document_ingestion_jobs")}
    assert "revision" in job_columns
    assert "phase" in job_columns
    assert "target_category" in job_columns
    assert "candidate_remote_file_id" in job_columns
    assert "candidate_remote_vector_store_file_id" in job_columns
    assert "candidate_remote_vector_store_id" in job_columns
    assert "cleanup_remote_file_id" in job_columns
    assert "cleanup_remote_vector_store_file_id" in job_columns
    assert "cleanup_remote_vector_store_id" in job_columns
    assert "lease_token" in job_columns
    assert "lease_expires_at" in job_columns

    assert "retrieval_indexes" in inspector.get_table_names()
    assert "slide_jobs" in inspector.get_table_names()
    slide_columns = {col["name"] for col in inspector.get_columns("slide_jobs")}
    assert {
        "id", "title", "document_ids", "slides_count", "guidance", "tone",
        "status", "phase", "stage", "message", "error", "artifact_key",
        "download_filename", "attempts", "lease_token", "lease_expires_at",
        "started_at", "finished_at", "created_at", "updated_at",
    } <= slide_columns
    assert {
        "cleanup_attempts", "cleanup_next_attempt_at", "cleanup_lease_token",
        "cleanup_lease_expires_at", "cleanup_error",
    } <= job_columns
    engine.dispose()


def test_partial_document_identity_index_allows_deleted_replacement(fresh_db):
    """Deleting states do not block a new active document with the same hash."""

    db_path, url = fresh_db
    _run_migrations(db_path)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text("UPDATE documents SET status = 'delete_failed' WHERE id = 'doc-legacy-1'"))
        conn.execute(
            text(
                "INSERT INTO documents ("
                "id, original_filename, display_name, mime_type, extension, size_bytes, "
                "checksum, category, storage_key, roles, created_at, updated_at"
                ") VALUES ("
                "'doc-replacement', 'file.pdf', 'file.pdf', 'application/pdf', '.pdf', 100, "
                "'sha256-abc', 'legislative_qa', 'storage/replacement.pdf', '[]', "
                "'2026-01-02T00:00:00', '2026-01-02T00:00:00')"
            )
        )
    engine.dispose()


def test_downgrade_preflight_rejects_deleted_duplicates(tmp_path):
    """Downgrade refuses to restore unconditional uniqueness over deleted rows."""

    db_path = str(tmp_path / "downgrade_dup.db")
    url = f"sqlite:///{db_path}"
    _run_migrations(db_path)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents ("
                "id, original_filename, display_name, mime_type, extension, size_bytes, "
                "checksum, category, storage_key, roles, status, created_at, updated_at"
                ") VALUES ("
                "'doc-delete-failed', 'file.pdf', 'file.pdf', 'application/pdf', '.pdf', 100, "
                "'same', 'legislative_qa', 'storage/replacement.pdf', '[]', 'delete_failed', "
                "'2026-01-02T00:00:00', '2026-01-02T00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO documents ("
                "id, original_filename, display_name, mime_type, extension, size_bytes, "
                "checksum, category, storage_key, roles, status, created_at, updated_at"
                ") VALUES ("
                "'doc-active', 'file.pdf', 'file.pdf', 'application/pdf', '.pdf', 100, "
                "'same', 'legislative_qa', 'storage/active.pdf', '[]', 'ready', "
                "'2026-01-03T00:00:00', '2026-01-03T00:00:00')"
            )
        )
    engine.dispose()
    from alembic.config import Config
    from alembic import command
    from pathlib import Path
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    with pytest.raises(RuntimeError, match="Downgrade aborted"):
        command.downgrade(cfg, "c8c8346f5cf8")


def test_data_backfill_populates_vs_file_id(fresh_db):
    """remote_vector_store_file_id must be backfilled from remote_vector_store_id after migration."""

    db_path, url = fresh_db
    _run_migrations(db_path)

    engine = create_engine(url, connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT remote_vector_store_file_id, remote_vector_store_id "
                "FROM documents WHERE id = 'doc-legacy-1'"
            )
        ).fetchone()

    assert row is not None
    # The attachment ID that was in remote_vector_store_id should now also be in remote_vector_store_file_id.
    assert row[0] == "vs-file-attach-456"
    engine.dispose()


def test_duplicate_preflight_aborts_migration(tmp_path):
    """Migration must abort with a clear error when duplicate (checksum, category) rows exist."""

    db_path = str(tmp_path / "test_dup.db")
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE document_folders (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    category TEXT NOT NULL,
                    folder_id TEXT,
                    storage_key TEXT NOT NULL,
                    retrieval_enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'queued',
                    stage TEXT,
                    error TEXT,
                    source_priority INTEGER,
                    roles TEXT NOT NULL DEFAULT '[]',
                    page_count INTEGER,
                    table_count INTEGER,
                    remote_file_id TEXT,
                    remote_vector_store_id TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE document_ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    stage TEXT,
                    error TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        # Two rows with identical (checksum, category) — preflight must catch this.
        for i in range(2):
            conn.execute(
                text(
                    f"""
                    INSERT INTO documents (
                        id, original_filename, display_name, mime_type, extension,
                        size_bytes, checksum, category, storage_key, roles, created_at, updated_at
                    ) VALUES (
                        'doc-dup-{i}', 'file.pdf', 'file.pdf', 'application/pdf', '.pdf',
                        100, 'duplicate-checksum', 'legislative_qa', 'k', '[]',
                        '2026-01-01T00:00:00', '2026-01-01T00:00:00'
                    )
                    """
                )
            )

    engine.dispose()

    with pytest.raises(RuntimeError, match="duplicate .checksum, category. pairs"):
        _run_migrations(db_path)


def test_agent_stage_settings_migration_assigns_existing_rows_to_slides(tmp_path):
    """Rows written before workflow-scoping existed must become slides rows."""

    from alembic import command

    db_path = str(tmp_path / "test_agent_stage_settings.db")
    url = f"sqlite:///{db_path}"
    cfg = _alembic_config(db_path)

    # Stop at the stage-only schema and insert a legacy row, then continue
    # to head so the new revision's backfill runs against it.
    command.upgrade(cfg, "4e2f8b1a7c90")
    engine = create_engine(url, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agent_stage_settings "
                "(stage, runner, model, reasoning_effort, planner_enabled, updated_at) "
                "VALUES ('author', 'codex', 'legacy-model', 'low', NULL, '2026-01-01T00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url, connect_args={"check_same_thread": False})
    inspector = sa.inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("agent_stage_settings")}
    assert "workflow" in columns
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT workflow, stage, model FROM agent_stage_settings WHERE stage = 'author'")
        ).fetchone()
    assert row is not None
    assert row[0] == "slides"
    assert row[2] == "legacy-model"
    engine.dispose()
