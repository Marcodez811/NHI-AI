"""Key agent stage settings by (workflow, stage).

The prior revision keyed ``agent_stage_settings`` by stage alone, so setting
the slides author model also changed the news author model. This adds a
``workflow`` column and makes the primary key ``(workflow, stage)``. Existing
rows predate multi-workflow settings and are all assigned to "slides".

The primary key change is expressed as a full table recreate (rather than
``batch_alter_table``'s constraint helpers) so the same code path runs
identically on SQLite (tests) and PostgreSQL (production).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0d1e72ef7da3"
down_revision: Union[str, Sequence[str], None] = "4e2f8b1a7c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "agent_stage_settings"
_TEMP = "agent_stage_settings_new"


def upgrade() -> None:
    op.create_table(
        _TEMP,
        sa.Column("workflow", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("runner", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
        sa.Column("planner_enabled", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("workflow", "stage"),
    )
    op.execute(
        f"INSERT INTO {_TEMP} "
        "(workflow, stage, runner, model, reasoning_effort, planner_enabled, updated_at) "
        "SELECT 'slides', stage, runner, model, reasoning_effort, planner_enabled, updated_at "
        f"FROM {_TABLE}"
    )
    op.drop_table(_TABLE)
    op.rename_table(_TEMP, _TABLE)


def downgrade() -> None:
    op.create_table(
        _TEMP,
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("runner", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
        sa.Column("planner_enabled", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("stage"),
    )
    # A stage may have both a "slides" and a "news" row after upgrade; the
    # old single-key schema can only keep one, so "slides" wins and any
    # workflow-specific rows created since the upgrade are dropped.
    op.execute(
        f"INSERT INTO {_TEMP} (stage, runner, model, reasoning_effort, planner_enabled, updated_at) "
        "SELECT stage, runner, model, reasoning_effort, planner_enabled, updated_at "
        f"FROM {_TABLE} WHERE workflow = 'slides'"
    )
    op.drop_table(_TABLE)
    op.rename_table(_TEMP, _TABLE)
