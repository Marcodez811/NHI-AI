"""Add chat skill mode, skill cards on messages, and the 立院QA workspace.

Revision ID: e5b82c4a9d17
Revises: c3a1f0e7d2b4
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5b82c4a9d17"
down_revision: Union[str, Sequence[str], None] = "c3a1f0e7d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("skill", sa.String(length=32), nullable=True))
    op.add_column(
        "chat_messages",
        sa.Column("cards", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_table(
        "chat_qa_workspaces",
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stage", sa.String(length=16), nullable=False, server_default="questions"),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("questions_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("outline", sa.JSON(), nullable=False),
        sa.Column("versions", sa.JSON(), nullable=False),
        sa.Column("base_version_id", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_qa_workspaces")
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("cards")
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("skill")
