"""Add the running AI-writing job to the 立院QA workspace.

Revision ID: b4c1e7d92a68
Revises: a7d3c91e5b20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4c1e7d92a68"
down_revision: Union[str, Sequence[str], None] = "a7d3c91e5b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_qa_workspaces",
        sa.Column("generation", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    with op.batch_alter_table("chat_qa_workspaces") as batch:
        batch.drop_column("generation")
