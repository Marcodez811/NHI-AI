"""Repository interfaces for chat sessions, messages, and attachments.

Mirrors ``app.services.documents.repository``'s shape: the API depends on
``ChatRepository`` rather than a global SQL session, ``SQLModelChatRepository``
is production wiring over a request-scoped ``Session``, and
``InMemoryChatRepository`` keeps route/engine tests deterministic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlmodel import Session, select

from app.models.chat import ChatAttachment, ChatMessage, ChatSession, utcnow


class ChatRepositoryError(RuntimeError):
    """Base class for expected chat catalog failures."""


class ChatSessionNotFoundError(ChatRepositoryError):
    pass


class ChatAttachmentNotFoundError(ChatRepositoryError):
    pass


@runtime_checkable
class ChatRepository(Protocol):
    async def create_session(self, session: ChatSession) -> ChatSession: ...

    async def list_sessions(self) -> list[ChatSession]: ...

    async def get_session(self, session_id: UUID) -> ChatSession | None: ...

    async def update_session(self, session: ChatSession) -> ChatSession: ...

    async def delete_session(self, session_id: UUID) -> None: ...

    async def create_message(self, message: ChatMessage) -> ChatMessage: ...

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]: ...

    async def create_attachment(self, attachment: ChatAttachment) -> ChatAttachment: ...

    async def get_attachment(self, attachment_id: UUID) -> ChatAttachment | None: ...

    async def list_attachments(self, session_id: UUID) -> list[ChatAttachment]: ...

    async def delete_attachment(self, attachment_id: UUID) -> None: ...


class InMemoryChatRepository:
    """Small async repository used by the default API dependency and tests."""

    def __init__(self) -> None:
        self.sessions: dict[UUID, ChatSession] = {}
        self.messages: dict[UUID, ChatMessage] = {}
        self.attachments: dict[UUID, ChatAttachment] = {}

    async def create_session(self, session: ChatSession) -> ChatSession:
        self.sessions[session.id] = session
        return session

    async def list_sessions(self) -> list[ChatSession]:
        return sorted(self.sessions.values(), key=lambda item: item.updated_at, reverse=True)

    async def get_session(self, session_id: UUID) -> ChatSession | None:
        return self.sessions.get(session_id)

    async def update_session(self, session: ChatSession) -> ChatSession:
        if session.id not in self.sessions:
            raise ChatSessionNotFoundError(str(session.id))
        session.updated_at = utcnow()
        self.sessions[session.id] = session
        return session

    async def delete_session(self, session_id: UUID) -> None:
        if session_id not in self.sessions:
            raise ChatSessionNotFoundError(str(session_id))
        del self.sessions[session_id]
        self.messages = {k: v for k, v in self.messages.items() if v.session_id != session_id}
        self.attachments = {k: v for k, v in self.attachments.items() if v.session_id != session_id}

    async def create_message(self, message: ChatMessage) -> ChatMessage:
        self.messages[message.id] = message
        return message

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        values = [m for m in self.messages.values() if m.session_id == session_id]
        return sorted(values, key=lambda item: item.created_at)

    async def create_attachment(self, attachment: ChatAttachment) -> ChatAttachment:
        self.attachments[attachment.id] = attachment
        return attachment

    async def get_attachment(self, attachment_id: UUID) -> ChatAttachment | None:
        return self.attachments.get(attachment_id)

    async def list_attachments(self, session_id: UUID) -> list[ChatAttachment]:
        values = [a for a in self.attachments.values() if a.session_id == session_id]
        return sorted(values, key=lambda item: item.created_at)

    async def delete_attachment(self, attachment_id: UUID) -> None:
        if attachment_id not in self.attachments:
            raise ChatAttachmentNotFoundError(str(attachment_id))
        del self.attachments[attachment_id]


class SQLModelChatRepository(InMemoryChatRepository):
    """SQLModel repository backed by an injected synchronous ``Session``."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    async def create_session(self, session: ChatSession) -> ChatSession:
        self.session.add(session)
        self.session.commit()
        self.session.refresh(session)
        return session

    async def list_sessions(self) -> list[ChatSession]:
        return list(
            self.session.exec(select(ChatSession).order_by(ChatSession.updated_at.desc())).all()
        )

    async def get_session(self, session_id: UUID) -> ChatSession | None:
        return self.session.get(ChatSession, session_id)

    async def update_session(self, session: ChatSession) -> ChatSession:
        if not self.session.get(ChatSession, session.id):
            raise ChatSessionNotFoundError(str(session.id))
        session.updated_at = utcnow()
        self.session.add(session)
        self.session.commit()
        self.session.refresh(session)
        return session

    async def delete_session(self, session_id: UUID) -> None:
        session = self.session.get(ChatSession, session_id)
        if not session:
            raise ChatSessionNotFoundError(str(session_id))
        for message in self.session.exec(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        ).all():
            self.session.delete(message)
        for attachment in self.session.exec(
            select(ChatAttachment).where(ChatAttachment.session_id == session_id)
        ).all():
            self.session.delete(attachment)
        self.session.delete(session)
        self.session.commit()

    async def create_message(self, message: ChatMessage) -> ChatMessage:
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        return message

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        return list(
            self.session.exec(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            ).all()
        )

    async def create_attachment(self, attachment: ChatAttachment) -> ChatAttachment:
        self.session.add(attachment)
        self.session.commit()
        self.session.refresh(attachment)
        return attachment

    async def get_attachment(self, attachment_id: UUID) -> ChatAttachment | None:
        return self.session.get(ChatAttachment, attachment_id)

    async def list_attachments(self, session_id: UUID) -> list[ChatAttachment]:
        return list(
            self.session.exec(
                select(ChatAttachment)
                .where(ChatAttachment.session_id == session_id)
                .order_by(ChatAttachment.created_at)
            ).all()
        )

    async def delete_attachment(self, attachment_id: UUID) -> None:
        attachment = self.session.get(ChatAttachment, attachment_id)
        if not attachment:
            raise ChatAttachmentNotFoundError(str(attachment_id))
        self.session.delete(attachment)
        self.session.commit()


__all__ = [
    "ChatRepository",
    "InMemoryChatRepository",
    "SQLModelChatRepository",
    "ChatRepositoryError",
    "ChatSessionNotFoundError",
    "ChatAttachmentNotFoundError",
]
