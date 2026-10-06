"""Add app_setting_overrides for the settings page.

Revision ID: c3a1f0e7d2b4
Revises: b7c41d9e2f58
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a1f0e7d2b4"
down_revision: Union[str, Sequence[str], None] = "b7c41d9e2f58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_setting_overrides",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_setting_overrides")
