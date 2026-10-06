"""Add user_files, chat_session_files, chat_session_documents and artifacts.

Uploads now belong to the user rather than one conversation
(docs/9_29_files_and_artifacts_spec.md). ``chat_attachments`` rows move into
``user_files`` keeping the SAME id (``chat_messages.attachment_ids`` reference
them), with ``storage_key`` prefixed by ``chat_attachments/`` so the stored
bytes still resolve from the documents root, plus one ``chat_session_files``
link per row. Completed slide jobs whose deck still exists get an artifact
with its own stored copy.

Downgrade restores ``chat_attachments`` from files that still use the legacy
``chat_attachments/<session>/<file>`` layout, one row per origin conversation;
files under ``user_files/`` cannot be represented in the old schema and their
rows are dropped (the bytes stay on disk).
"""

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c41d9e2f58"
down_revision: Union[str, Sequence[str], None] = "a6294c7e1d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_PREFIX = "chat_attachments/"
PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def upgrade() -> None:
    connection = op.get_bind()
    existing = set(sa.inspect(connection).get_table_names())

    op.create_table(
        "user_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("text_chars", sa.Integer(), nullable=True),
        sa.Column("origin_session_id", sa.Uuid(), nullable=True),
        sa.Column("promoted_document_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["origin_session_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_files_origin_session_id", "user_files", ["origin_session_id"])

    op.create_table(
        "chat_session_files",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["user_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "file_id"),
    )

    op.create_table(
        "chat_session_documents",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "document_id"),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("source_workflow", sa.String(32), nullable=False),
        sa.Column("source_job_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_source_job_id", "artifacts", ["source_job_id"])

    if "chat_attachments" in existing:
        op.execute(
            "INSERT INTO user_files (id, display_name, mime_type, kind, size_bytes, storage_key, status, "
            "error, text_chars, origin_session_id, promoted_document_id, created_at) "
            "SELECT id, display_name, mime_type, kind, size_bytes, '" + _LEGACY_PREFIX + "' || storage_key, "
            "status, error, text_chars, session_id, NULL, created_at FROM chat_attachments"
        )
        op.execute(
            "INSERT INTO chat_session_files (session_id, file_id, created_at) "
            "SELECT session_id, id, created_at FROM chat_attachments"
        )
        op.drop_index("ix_chat_attachments_session_id", table_name="chat_attachments")
        op.drop_table("chat_attachments")

    if "slide_jobs" in existing:
        _backfill_slide_artifacts(connection)


def _backfill_slide_artifacts(connection: sa.engine.Connection) -> None:
    """Give each completed slide job whose deck still exists an artifact with its own copy."""

    from app.config import settings

    output_root = Path(settings.agent_output_root).expanduser().resolve()
    artifacts_dir = Path(settings.documents_root).expanduser().resolve() / "artifacts"
    artifacts = sa.table(
        "artifacts",
        sa.column("id", sa.Uuid()),
        sa.column("kind", sa.String()),
        sa.column("title", sa.String()),
        sa.column("mime_type", sa.String()),
        sa.column("size_bytes", sa.Integer()),
        sa.column("storage_key", sa.String()),
        sa.column("source_workflow", sa.String()),
        sa.column("source_job_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime()),
    )
    rows = connection.execute(
        sa.text(
            "SELECT id, title, artifact_key, finished_at, created_at FROM slide_jobs "
            "WHERE status = 'completed' AND artifact_key IS NOT NULL"
        )
    ).all()
    for job_id, title, artifact_key, finished_at, created_at in rows:
        key = Path(artifact_key)
        if key.is_absolute() or ".." in key.parts:
            continue
        source = (output_root / key).resolve()
        try:
            source.relative_to(output_root)
        except ValueError:
            continue
        if source.is_symlink() or not source.is_file():
            continue
        artifact_id = uuid.uuid4()
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            destination = artifacts_dir / f"{artifact_id}.pptx"
            shutil.copyfile(source, destination)
        except OSError:
            continue
        stamp = finished_at or created_at or datetime.now(timezone.utc)
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp)
        connection.execute(
            sa.insert(artifacts).values(
                id=artifact_id,
                kind="slide_deck",
                title=title,
                mime_type=PPTX_MIME_TYPE,
                size_bytes=destination.stat().st_size,
                storage_key=f"artifacts/{destination.name}",
                source_workflow="slides",
                source_job_id=uuid.UUID(str(job_id)),
                created_at=stamp,
            )
        )


def downgrade() -> None:
    op.create_table(
        "chat_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("text_chars", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_attachments_session_id", "chat_attachments", ["session_id"])
    op.execute(
        "INSERT INTO chat_attachments (id, session_id, display_name, mime_type, kind, size_bytes, "
        "storage_key, status, error, text_chars, created_at) "
        "SELECT f.id, f.origin_session_id, f.display_name, f.mime_type, f.kind, f.size_bytes, "
        "SUBSTR(f.storage_key, " + str(len(_LEGACY_PREFIX) + 1) + "), f.status, f.error, f.text_chars, f.created_at "
        "FROM user_files f JOIN chat_session_files l ON l.file_id = f.id AND l.session_id = f.origin_session_id "
        "WHERE SUBSTR(f.storage_key, 1, " + str(len(_LEGACY_PREFIX)) + ") = '" + _LEGACY_PREFIX + "'"
    )
    op.drop_index("ix_artifacts_source_job_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("chat_session_documents")
    op.drop_table("chat_session_files")
    op.drop_index("ix_user_files_origin_session_id", table_name="user_files")
    op.drop_table("user_files")
