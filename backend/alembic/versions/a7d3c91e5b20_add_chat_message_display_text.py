"""Add display_text to chat messages (automatic skill continuation turns).

Revision ID: a7d3c91e5b20
Revises: e5b82c4a9d17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3c91e5b20"
down_revision: Union[str, Sequence[str], None] = "e5b82c4a9d17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("display_text", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("display_text")
