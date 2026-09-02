"""Persist slide jobs and make document identity reusable after deletion.

The first reliability revision created an unconditional document uniqueness
index.  A soft-deleted document must not prevent a later upload of the same
content, so this revision replaces that index with a partial one.  The
preflight in ``downgrade`` is intentional: once deleted rows are allowed to
coexist, restoring the old unconditional constraint can be lossy or fail with
an opaque database error.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d91e3f4a5b6c"
down_revision: Union[str, Sequence[str], None] = "c8c8346f5cf8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UNIQUE_NAME = "uq_documents_checksum_category"
_SLIDE_STATUS_INDEX = "ix_slide_jobs_status"
_SLIDE_PHASE_INDEX = "ix_slide_jobs_phase"
_EXCLUDED_DOCUMENT_STATUSES = ("deleting", "delete_failed")


def _inspector(connection: sa.engine.Connection) -> sa.Inspector:
    return sa.inspect(connection)


def _tables(connection: sa.engine.Connection) -> set[str]:
    return set(_inspector(connection).get_table_names())


def _columns(connection: sa.engine.Connection, table: str) -> set[str]:
    return {column["name"] for column in _inspector(connection).get_columns(table)}


def _ensure_cleanup_columns(connection: sa.engine.Connection) -> None:
    """Keep cleanup IDs available on databases created before c8.

    c8 creates these columns on a clean schema, but deployments can have a
    table created by the old ``create_all`` path.  Making the additions
    idempotent keeps this follow-up revision safe for both layouts.
    """

    if "document_ingestion_jobs" not in _tables(connection):
        return
    existing = _columns(connection, "document_ingestion_jobs")
    additions = (
        sa.Column("cleanup_remote_file_id", sa.String(255), nullable=True),
        sa.Column("cleanup_remote_vector_store_file_id", sa.String(255), nullable=True),
        sa.Column("cleanup_remote_vector_store_id", sa.String(255), nullable=True),
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cleanup_next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("cleanup_lease_token", sa.String(128), nullable=True),
        sa.Column("cleanup_lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("cleanup_error", sa.String(512), nullable=True),
    )
    missing = [column for column in additions if column.name not in existing]
    if missing:
        with op.batch_alter_table("document_ingestion_jobs") as batch:
            for column in missing:
                batch.add_column(column)


def _create_slide_jobs(connection: sa.engine.Connection) -> None:
    if "slide_jobs" in _tables(connection):
        return
    op.create_table(
        "slide_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("slides_count", sa.Integer(), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("tone", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(64), nullable=True, server_default="queued"),
        sa.Column("message", sa.String(512), nullable=True),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("artifact_key", sa.String(1024), nullable=True),
        sa.Column("download_filename", sa.String(512), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(_SLIDE_STATUS_INDEX, "slide_jobs", ["status"])
    op.create_index(_SLIDE_PHASE_INDEX, "slide_jobs", ["phase"])


def _drop_old_unique(connection: sa.engine.Connection) -> None:
    if "documents" not in _tables(connection):
        return
    inspector = _inspector(connection)
    if any(item.get("name") == _UNIQUE_NAME for item in inspector.get_indexes("documents")):
        op.drop_index(_UNIQUE_NAME, table_name="documents")
        return
    # A hand-created legacy schema may have represented the same name as a
    # table constraint instead of an index.  SQLite requires batch mode here.
    if any(item.get("name") == _UNIQUE_NAME for item in inspector.get_unique_constraints("documents")):
        with op.batch_alter_table("documents") as batch:
            batch.drop_constraint(_UNIQUE_NAME, type_="unique")


def _active_duplicate_preflight(connection: sa.engine.Connection) -> None:
    if "documents" not in _tables(connection):
        return
    rows = connection.execute(
        sa.text(
            "SELECT checksum, category, COUNT(*) AS count "
            "FROM documents "
            "WHERE status NOT IN ('deleting', 'delete_failed') "
            "GROUP BY checksum, category HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if not rows:
        return
    conflicts: list[str] = []
    for checksum, category, count in rows:
        ids = connection.execute(
            sa.text(
                "SELECT id FROM documents "
                "WHERE checksum = :checksum AND category = :category "
                "AND status NOT IN ('deleting', 'delete_failed') ORDER BY id"
            ),
            {"checksum": checksum, "category": category},
        ).scalars().all()
        conflicts.append(
            f"  checksum={checksum!r}, category={category!r}, count={count}, "
            f"ids={[str(value) for value in ids]}"
        )
    raise RuntimeError(
        "Migration aborted: duplicate active (checksum, category) pairs found "
        "in documents. Resolve these document IDs before retrying:\n"
        + "\n".join(conflicts)
    )


def _downgrade_duplicate_preflight(connection: sa.engine.Connection) -> None:
    if "documents" not in _tables(connection):
        return
    rows = connection.execute(
        sa.text(
            "SELECT checksum, category, COUNT(*) AS count "
            "FROM documents GROUP BY checksum, category HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if rows:
        pairs = ", ".join(f"({checksum!r}, {category!r})" for checksum, category, _ in rows)
        raise RuntimeError(
            "Downgrade aborted: duplicate (checksum, category) pairs would "
            f"violate {_UNIQUE_NAME}: {pairs}. Resolve duplicate document rows first."
        )


def _create_active_unique(connection: sa.engine.Connection) -> None:
    if "documents" not in _tables(connection):
        return
    predicate = sa.text("status NOT IN ('deleting', 'delete_failed')")
    op.create_index(
        _UNIQUE_NAME,
        "documents",
        ["checksum", "category"],
        unique=True,
        sqlite_where=predicate,
        postgresql_where=predicate,
    )


def upgrade() -> None:
    connection = op.get_bind()
    _active_duplicate_preflight(connection)
    _ensure_cleanup_columns(connection)
    _create_slide_jobs(connection)
    _drop_old_unique(connection)
    _create_active_unique(connection)


def downgrade() -> None:
    connection = op.get_bind()
    _downgrade_duplicate_preflight(connection)
    if "documents" in _tables(connection):
        inspector = _inspector(connection)
        if any(item.get("name") == _UNIQUE_NAME for item in inspector.get_indexes("documents")):
            op.drop_index(_UNIQUE_NAME, table_name="documents")
        op.create_index(_UNIQUE_NAME, "documents", ["checksum", "category"], unique=True)
    if "slide_jobs" in _tables(connection):
        inspector = _inspector(connection)
        for name in (_SLIDE_STATUS_INDEX, _SLIDE_PHASE_INDEX):
            if any(item.get("name") == name for item in inspector.get_indexes("slide_jobs")):
                op.drop_index(name, table_name="slide_jobs")
        op.drop_table("slide_jobs")
    if "document_ingestion_jobs" in _tables(connection):
        columns = _columns(connection, "document_ingestion_jobs")
        with op.batch_alter_table("document_ingestion_jobs") as batch:
            for name in (
                "cleanup_error",
                "cleanup_lease_expires_at",
                "cleanup_lease_token",
                "cleanup_next_attempt_at",
                "cleanup_attempts",
            ):
                if name in columns:
                    batch.drop_column(name)
