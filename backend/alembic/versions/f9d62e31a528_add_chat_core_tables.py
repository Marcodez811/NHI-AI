"""Add chat_sessions, chat_messages, chat_attachments for the new agentic chat.

Replaces the old single-question chat (docs/9_29_chat_core_and_attachments_plan.md):
conversations are now durable, multi-turn, and each session owns its own
attachments -- separate from the knowledge-base ``documents`` table and never
indexed into the vector store. Single-user prototype: no ownership columns yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9d62e31a528"
down_revision: Union[str, Sequence[str], None] = "0d1e72ef7da3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SESSIONS = "chat_sessions"
_MESSAGES = "chat_messages"
_ATTACHMENTS = "chat_attachments"
_MESSAGES_SESSION_INDEX = "ix_chat_messages_session_id"
_ATTACHMENTS_SESSION_INDEX = "ix_chat_attachments_session_id"


def _inspector(connection: sa.engine.Connection) -> sa.Inspector:
    return sa.inspect(connection)


def _tables(connection: sa.engine.Connection) -> set[str]:
    return set(_inspector(connection).get_table_names())


def upgrade() -> None:
    connection = op.get_bind()
    existing = _tables(connection)

    if _SESSIONS not in existing:
        op.create_table(
            _SESSIONS,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("model", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if _MESSAGES not in existing:
        op.create_table(
            _MESSAGES,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("attachment_ids", sa.JSON(), nullable=False),
            sa.Column("sources", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("model", sa.String(128), nullable=True),
            sa.Column("usage", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(_MESSAGES_SESSION_INDEX, _MESSAGES, ["session_id"])

    if _ATTACHMENTS not in existing:
        op.create_table(
            _ATTACHMENTS,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("display_name", sa.String(512), nullable=False),
            sa.Column("mime_type", sa.String(255), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("storage_key", sa.String(1024), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("error", sa.String(512), nullable=True),
            sa.Column("text_chars", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(_ATTACHMENTS_SESSION_INDEX, _ATTACHMENTS, ["session_id"])


def downgrade() -> None:
    connection = op.get_bind()
    existing = _tables(connection)

    if _ATTACHMENTS in existing:
        inspector = _inspector(connection)
        if any(item.get("name") == _ATTACHMENTS_SESSION_INDEX for item in inspector.get_indexes(_ATTACHMENTS)):
            op.drop_index(_ATTACHMENTS_SESSION_INDEX, table_name=_ATTACHMENTS)
        op.drop_table(_ATTACHMENTS)

    if _MESSAGES in existing:
        inspector = _inspector(connection)
        if any(item.get("name") == _MESSAGES_SESSION_INDEX for item in inspector.get_indexes(_MESSAGES)):
            op.drop_index(_MESSAGES_SESSION_INDEX, table_name=_MESSAGES)
        op.drop_table(_MESSAGES)

    if _SESSIONS in existing:
        op.drop_table(_SESSIONS)
