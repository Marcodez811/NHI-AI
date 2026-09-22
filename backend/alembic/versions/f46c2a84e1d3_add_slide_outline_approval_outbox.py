"""Add the transactional outbox for approved slide-outline resumes."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f46c2a84e1d3"
down_revision: Union[str, Sequence[str], None] = "783d53229171"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "slide_outline_approval_outbox"
_JOB_INDEX = "ix_slide_outline_approval_outbox_job_id"
_DISPATCHED_INDEX = "ix_slide_outline_approval_outbox_dispatched_at"
_UNIQUE_NAME = "uq_slide_outline_approval_outbox_job_revision"


def _tables(connection: sa.engine.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def upgrade() -> None:
    connection = op.get_bind()
    if _TABLE_NAME in _tables(connection):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("task_name", sa.String(128), nullable=False, server_default="agents.run"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["slide_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "revision", name=_UNIQUE_NAME),
    )
    op.create_index(_JOB_INDEX, _TABLE_NAME, ["job_id"])
    op.create_index(_DISPATCHED_INDEX, _TABLE_NAME, ["dispatched_at"])


def downgrade() -> None:
    connection = op.get_bind()
    if _TABLE_NAME not in _tables(connection):
        return
    inspector = sa.inspect(connection)
    for index_name in (_DISPATCHED_INDEX, _JOB_INDEX):
        if any(item.get("name") == index_name for item in inspector.get_indexes(_TABLE_NAME)):
            op.drop_index(index_name, table_name=_TABLE_NAME)
    op.drop_table(_TABLE_NAME)
