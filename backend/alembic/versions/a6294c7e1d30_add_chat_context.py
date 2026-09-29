"""Persist chat compaction state and assistant reasoning summaries."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6294c7e1d30"
down_revision: Union[str, Sequence[str], None] = "f9d62e31a528"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("summary_through_message_id", sa.Uuid(), nullable=True))
    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(sa.Column("reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("reasoning")
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("summary_through_message_id")
        batch.drop_column("summary")
