"""Persist per-stage agent model settings."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4e2f8b1a7c90"
down_revision: Union[str, Sequence[str], None] = "f46c2a84e1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_stage_settings",
        sa.Column("stage", sa.String(length=32), primary_key=True),
        sa.Column("runner", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
        sa.Column("planner_enabled", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_stage_settings")
