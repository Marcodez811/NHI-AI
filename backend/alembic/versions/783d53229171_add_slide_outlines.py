"""Add slide_outlines for the planning phase's immutable revision history.

Outline revisions are append-only: the planner writes a new row per turn
rather than updating one in place, and approval always names an exact
``(job_id, revision)`` pair. ``slide_jobs`` itself is unchanged by this
revision; its new ``awaiting_input`` status and ``planning``/
``awaiting_outline`` phase values are plain strings already accepted by the
existing ``status``/``phase`` columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "783d53229171"
down_revision: Union[str, Sequence[str], None] = "d91e3f4a5b6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "slide_outlines"
_JOB_INDEX = "ix_slide_outlines_job_id"
_UNIQUE_NAME = "uq_slide_outlines_job_revision"


def _inspector(connection: sa.engine.Connection) -> sa.Inspector:
    return sa.inspect(connection)


def _tables(connection: sa.engine.Connection) -> set[str]:
    return set(_inspector(connection).get_table_names())


def upgrade() -> None:
    connection = op.get_bind()
    if _TABLE_NAME in _tables(connection):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("outline", sa.JSON(), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["slide_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "revision", name=_UNIQUE_NAME),
    )
    op.create_index(_JOB_INDEX, _TABLE_NAME, ["job_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if _TABLE_NAME not in _tables(connection):
        return
    inspector = _inspector(connection)
    if any(item.get("name") == _JOB_INDEX for item in inspector.get_indexes(_TABLE_NAME)):
        op.drop_index(_JOB_INDEX, table_name=_TABLE_NAME)
    op.drop_table(_TABLE_NAME)
