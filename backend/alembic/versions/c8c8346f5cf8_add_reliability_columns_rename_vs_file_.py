"""Create the knowledge-base schema and make legacy vector IDs unambiguous.

This first released revision upgrades both an empty database and databases
created by the pre-Alembic ``create_all`` startup path. In the legacy schema
``documents.remote_vector_store_id`` was actually the OpenAI vector-store
attachment ID. It is copied into ``remote_vector_store_file_id`` and the
owning store is populated only when a durable registry row provides it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8c8346f5cf8"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UNIQUE_NAME = "uq_documents_checksum_category"


def _inspector(connection: sa.engine.Connection) -> sa.Inspector:
    return sa.inspect(connection)


def _tables(connection: sa.engine.Connection) -> set[str]:
    return set(_inspector(connection).get_table_names())


def _columns(connection: sa.engine.Connection, table: str) -> set[str]:
    return {column["name"] for column in _inspector(connection).get_columns(table)}


def _create_retrieval_indexes(connection: sa.engine.Connection) -> None:
    if "retrieval_indexes" in _tables(connection):
        return
    op.create_table(
        "retrieval_indexes",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("installation_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("vector_store_id", sa.String(255), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.String(512), nullable=True),
        sa.Column("warning_code", sa.String(64), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_retrieval_indexes_installation_id", "retrieval_indexes", ["installation_id"])
    op.create_index("ix_retrieval_indexes_state", "retrieval_indexes", ["state"])
    op.create_index("ix_retrieval_indexes_vector_store_id", "retrieval_indexes", ["vector_store_id"])


def _create_legacy_tables(connection: sa.engine.Connection) -> None:
    tables = _tables(connection)
    if "document_folders" not in tables:
        op.create_table(
            "document_folders",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_document_folders_name", "document_folders", ["name"])

    if "documents" not in tables:
        op.create_table(
            "documents",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("original_filename", sa.String(512), nullable=False),
            sa.Column("display_name", sa.String(512), nullable=False),
            sa.Column("mime_type", sa.String(255), nullable=False),
            sa.Column("extension", sa.String(32), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("category", sa.String(64), nullable=False),
            sa.Column("folder_id", sa.Uuid(), nullable=True),
            sa.Column("storage_key", sa.String(1024), nullable=False),
            sa.Column("retrieval_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("stage", sa.String(64), nullable=True),
            sa.Column("error", sa.String(512), nullable=True),
            sa.Column("source_priority", sa.Integer(), nullable=True),
            sa.Column("roles", sa.JSON(), nullable=False),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column("table_count", sa.Integer(), nullable=True),
            sa.Column("ingestion_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pending_category", sa.String(64), nullable=True),
            sa.Column("remote_file_id", sa.String(255), nullable=True),
            sa.Column("remote_vector_store_file_id", sa.String(255), nullable=True),
            sa.Column("remote_vector_store_id", sa.String(255), nullable=True),
            sa.Column("candidate_remote_file_id", sa.String(255), nullable=True),
            sa.Column("candidate_remote_vector_store_file_id", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["folder_id"], ["document_folders.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for name, column in (
            ("ix_documents_checksum", "checksum"),
            ("ix_documents_category", "category"),
            ("ix_documents_folder_id", "folder_id"),
            ("ix_documents_retrieval_enabled", "retrieval_enabled"),
            ("ix_documents_status", "status"),
        ):
            op.create_index(name, "documents", [column])

    if "document_ingestion_jobs" not in tables:
        op.create_table(
            "document_ingestion_jobs",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("document_id", sa.Uuid(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stage", sa.String(64), nullable=True),
            sa.Column("phase", sa.String(64), nullable=True),
            sa.Column("target_category", sa.String(64), nullable=True),
            sa.Column("error", sa.String(512), nullable=True),
            sa.Column("candidate_remote_file_id", sa.String(255), nullable=True),
            sa.Column("candidate_remote_vector_store_file_id", sa.String(255), nullable=True),
            sa.Column("candidate_remote_vector_store_id", sa.String(255), nullable=True),
            sa.Column("cleanup_remote_file_id", sa.String(255), nullable=True),
            sa.Column("cleanup_remote_vector_store_file_id", sa.String(255), nullable=True),
            sa.Column("cleanup_remote_vector_store_id", sa.String(255), nullable=True),
            sa.Column("lease_token", sa.String(128), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_document_ingestion_jobs_document_id", "document_ingestion_jobs", ["document_id"])


def _add_columns(connection: sa.engine.Connection) -> None:
    tables = _tables(connection)
    if "documents" in tables:
        columns = _columns(connection, "documents")
        additions = [
            ("ingestion_revision", sa.Column("ingestion_revision", sa.Integer(), nullable=False, server_default="0")),
            ("pending_category", sa.Column("pending_category", sa.String(64), nullable=True)),
            ("remote_vector_store_file_id", sa.Column("remote_vector_store_file_id", sa.String(255), nullable=True)),
            ("remote_vector_store_id", sa.Column("remote_vector_store_id", sa.String(255), nullable=True)),
            ("candidate_remote_file_id", sa.Column("candidate_remote_file_id", sa.String(255), nullable=True)),
            ("candidate_remote_vector_store_file_id", sa.Column("candidate_remote_vector_store_file_id", sa.String(255), nullable=True)),
        ]
        missing = [column for name, column in additions if name not in columns]
        if missing:
            with op.batch_alter_table("documents") as batch:
                for column in missing:
                    batch.add_column(column)

    if "document_ingestion_jobs" in tables:
        columns = _columns(connection, "document_ingestion_jobs")
        additions = [
            ("revision", sa.Column("revision", sa.Integer(), nullable=False, server_default="0")),
            ("phase", sa.Column("phase", sa.String(64), nullable=True)),
            ("target_category", sa.Column("target_category", sa.String(64), nullable=True)),
            ("candidate_remote_file_id", sa.Column("candidate_remote_file_id", sa.String(255), nullable=True)),
            ("candidate_remote_vector_store_file_id", sa.Column("candidate_remote_vector_store_file_id", sa.String(255), nullable=True)),
            ("candidate_remote_vector_store_id", sa.Column("candidate_remote_vector_store_id", sa.String(255), nullable=True)),
            ("cleanup_remote_file_id", sa.Column("cleanup_remote_file_id", sa.String(255), nullable=True)),
            ("cleanup_remote_vector_store_file_id", sa.Column("cleanup_remote_vector_store_file_id", sa.String(255), nullable=True)),
            ("cleanup_remote_vector_store_id", sa.Column("cleanup_remote_vector_store_id", sa.String(255), nullable=True)),
            ("lease_token", sa.Column("lease_token", sa.String(128), nullable=True)),
            ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(), nullable=True)),
        ]
        missing = [column for name, column in additions if name not in columns]
        if missing:
            with op.batch_alter_table("document_ingestion_jobs") as batch:
                for column in missing:
                    batch.add_column(column)


def _preflight_duplicates(connection: sa.engine.Connection) -> None:
    if "documents" not in _tables(connection):
        return
    result = connection.execute(
        sa.text(
            "SELECT checksum, category, COUNT(*) AS count "
            "FROM documents GROUP BY checksum, category HAVING COUNT(*) > 1"
        )
    )
    conflicts: list[str] = []
    for checksum, category, count in result.fetchall():
        ids = connection.execute(
            sa.text(
                "SELECT id FROM documents WHERE checksum = :checksum AND category = :category ORDER BY id"
            ),
            {"checksum": checksum, "category": category},
        ).scalars().all()
        conflicts.append(
            f"  checksum={checksum!r}, category={category!r}, count={count}, ids={[str(value) for value in ids]}"
        )
    if conflicts:
        raise RuntimeError(
            "Migration aborted: duplicate (checksum, category) pairs found in documents. "
            "Resolve these document IDs before retrying:\n" + "\n".join(conflicts)
        )


def _backfill_legacy_ids(connection: sa.engine.Connection, legacy_attachment_column: bool) -> None:
    if "documents" not in _tables(connection):
        return
    columns = _columns(connection, "documents")
    if "remote_vector_store_file_id" not in columns or "remote_vector_store_id" not in columns:
        return
    if legacy_attachment_column:
        connection.execute(
            sa.text(
                "UPDATE documents SET remote_vector_store_file_id = remote_vector_store_id "
                "WHERE remote_vector_store_file_id IS NULL AND remote_vector_store_id IS NOT NULL"
            )
        )
        # The old value was an attachment ID, never the owning store ID.
        connection.execute(
            sa.text(
                "UPDATE documents SET remote_vector_store_id = NULL "
                "WHERE remote_vector_store_file_id IS NOT NULL"
            )
        )
    if "retrieval_indexes" in _tables(connection):
        connection.execute(
            sa.text(
                "UPDATE documents SET remote_vector_store_id = "
                "(SELECT vector_store_id FROM retrieval_indexes WHERE key = 'primary') "
                "WHERE remote_vector_store_file_id IS NOT NULL "
                "AND remote_vector_store_id IS NULL"
            )
        )


def _ensure_unique_index(connection: sa.engine.Connection) -> None:
    inspector = _inspector(connection)
    if "documents" not in _tables(connection):
        return
    if any(item.get("name") == _UNIQUE_NAME for item in inspector.get_indexes("documents")):
        return
    if any(item.get("name") == _UNIQUE_NAME for item in inspector.get_unique_constraints("documents")):
        return
    op.create_index(_UNIQUE_NAME, "documents", ["checksum", "category"], unique=True)


def upgrade() -> None:
    connection = op.get_bind()
    tables = _tables(connection)
    existing_document_columns = _columns(connection, "documents") if "documents" in tables else set()
    legacy_attachment_column = "remote_vector_store_file_id" not in existing_document_columns
    _preflight_duplicates(connection)
    _create_legacy_tables(connection)
    _create_retrieval_indexes(connection)
    _add_columns(connection)
    _backfill_legacy_ids(connection, legacy_attachment_column)
    _ensure_unique_index(connection)


def downgrade() -> None:
    connection = op.get_bind()
    if "documents" in _tables(connection):
        if any(item.get("name") == _UNIQUE_NAME for item in _inspector(connection).get_indexes("documents")):
            op.drop_index(_UNIQUE_NAME, table_name="documents")
        columns = _columns(connection, "documents")
        with op.batch_alter_table("documents") as batch:
            for name in (
                "candidate_remote_vector_store_file_id",
                "candidate_remote_file_id",
                "remote_vector_store_file_id",
                "pending_category",
                "ingestion_revision",
            ):
                if name in columns:
                    batch.drop_column(name)
    if "document_ingestion_jobs" in _tables(connection):
        columns = _columns(connection, "document_ingestion_jobs")
        with op.batch_alter_table("document_ingestion_jobs") as batch:
            for name in (
                "candidate_remote_vector_store_file_id",
                "candidate_remote_vector_store_id",
                "candidate_remote_file_id",
                "cleanup_remote_vector_store_id",
                "cleanup_remote_vector_store_file_id",
                "cleanup_remote_file_id",
                "lease_expires_at",
                "lease_token",
                "target_category",
                "phase",
                "revision",
            ):
                if name in columns:
                    batch.drop_column(name)
