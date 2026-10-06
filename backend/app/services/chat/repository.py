"""Repository interfaces for chat sessions, messages, user files, and their links.

Mirrors ``app.services.documents.repository``'s shape: the API depends on
``ChatRepository`` rather than a global SQL session, ``SQLModelChatRepository``
is production wiring over a request-scoped ``Session``, and
``InMemoryChatRepository`` keeps route/engine tests deterministic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlmodel import Session, col, select

from app.models.artifacts import Artifact
from app.models.chat import (
    ChatMessage,
    ChatSession,
    ChatSessionDocument,
    ChatSessionFile,
    UserFile,
    utcnow,
)


class ChatRepositoryError(RuntimeError):
    """Base class for expected chat catalog failures."""


class ChatSessionNotFoundError(ChatRepositoryError):
    pass


class ChatFileNotFoundError(ChatRepositoryError):
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

    async def create_file(self, file: UserFile) -> UserFile: ...

    async def get_file(self, file_id: UUID) -> UserFile | None: ...

    async def list_files(self) -> list[UserFile]: ...

    async def update_file(self, file: UserFile) -> UserFile: ...

    async def delete_file(self, file_id: UUID) -> None: ...

    async def link_file(self, session_id: UUID, file_id: UUID) -> None: ...

    async def unlink_file(self, session_id: UUID, file_id: UUID) -> None: ...

    async def list_session_files(self, session_id: UUID) -> list[UserFile]: ...

    async def link_document(self, session_id: UUID, document_id: UUID) -> None: ...

    async def unlink_document(self, session_id: UUID, document_id: UUID) -> None: ...

    async def list_session_document_ids(self, session_id: UUID) -> list[UUID]: ...


class InMemoryChatRepository:
    """Small async repository used by the default API dependency and tests."""

    def __init__(self) -> None:
        self.sessions: dict[UUID, ChatSession] = {}
        self.messages: dict[UUID, ChatMessage] = {}
        self.files: dict[UUID, UserFile] = {}
        self.file_links: dict[tuple[UUID, UUID], object] = {}
        self.document_links: dict[tuple[UUID, UUID], object] = {}

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
        self.file_links = {k: v for k, v in self.file_links.items() if k[0] != session_id}
        self.document_links = {k: v for k, v in self.document_links.items() if k[0] != session_id}
        for file in self.files.values():
            if file.origin_session_id == session_id:
                file.origin_session_id = None

    async def create_message(self, message: ChatMessage) -> ChatMessage:
        self.messages[message.id] = message
        return message

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        values = [m for m in self.messages.values() if m.session_id == session_id]
        return sorted(values, key=lambda item: item.created_at)

    async def create_file(self, file: UserFile) -> UserFile:
        self.files[file.id] = file
        return file

    async def get_file(self, file_id: UUID) -> UserFile | None:
        return self.files.get(file_id)

    async def list_files(self) -> list[UserFile]:
        return sorted(self.files.values(), key=lambda item: item.created_at, reverse=True)

    async def update_file(self, file: UserFile) -> UserFile:
        if file.id not in self.files:
            raise ChatFileNotFoundError(str(file.id))
        self.files[file.id] = file
        return file

    async def delete_file(self, file_id: UUID) -> None:
        if file_id not in self.files:
            raise ChatFileNotFoundError(str(file_id))
        del self.files[file_id]
        self.file_links = {k: v for k, v in self.file_links.items() if k[1] != file_id}

    async def link_file(self, session_id: UUID, file_id: UUID) -> None:
        self.file_links.setdefault((session_id, file_id), utcnow())

    async def unlink_file(self, session_id: UUID, file_id: UUID) -> None:
        self.file_links.pop((session_id, file_id), None)

    async def list_session_files(self, session_id: UUID) -> list[UserFile]:
        linked = [(created, self.files[fid]) for (sid, fid), created in self.file_links.items() if sid == session_id and fid in self.files]
        return [file for _created, file in sorted(linked, key=lambda pair: pair[0])]

    async def link_document(self, session_id: UUID, document_id: UUID) -> None:
        self.document_links.setdefault((session_id, document_id), utcnow())

    async def unlink_document(self, session_id: UUID, document_id: UUID) -> None:
        self.document_links.pop((session_id, document_id), None)

    async def list_session_document_ids(self, session_id: UUID) -> list[UUID]:
        linked = [(created, did) for (sid, did), created in self.document_links.items() if sid == session_id]
        return [did for _created, did in sorted(linked, key=lambda pair: pair[0])]


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
        # Files and artifacts outlive the conversation: drop only the links
        # and clear the origin pointers (the FK would do the latter in PostgreSQL).
        for link in self.session.exec(
            select(ChatSessionFile).where(ChatSessionFile.session_id == session_id)
        ).all():
            self.session.delete(link)
        for doc_link in self.session.exec(
            select(ChatSessionDocument).where(ChatSessionDocument.session_id == session_id)
        ).all():
            self.session.delete(doc_link)
        for file in self.session.exec(select(UserFile).where(UserFile.origin_session_id == session_id)).all():
            file.origin_session_id = None
            self.session.add(file)
        for artifact in self.session.exec(select(Artifact).where(Artifact.session_id == session_id)).all():
            artifact.session_id = None
            self.session.add(artifact)
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

    async def create_file(self, file: UserFile) -> UserFile:
        self.session.add(file)
        self.session.commit()
        self.session.refresh(file)
        return file

    async def get_file(self, file_id: UUID) -> UserFile | None:
        return self.session.get(UserFile, file_id)

    async def list_files(self) -> list[UserFile]:
        return list(self.session.exec(select(UserFile).order_by(col(UserFile.created_at).desc())).all())

    async def update_file(self, file: UserFile) -> UserFile:
        if not self.session.get(UserFile, file.id):
            raise ChatFileNotFoundError(str(file.id))
        self.session.add(file)
        self.session.commit()
        self.session.refresh(file)
        return file

    async def delete_file(self, file_id: UUID) -> None:
        file = self.session.get(UserFile, file_id)
        if not file:
            raise ChatFileNotFoundError(str(file_id))
        for link in self.session.exec(select(ChatSessionFile).where(ChatSessionFile.file_id == file_id)).all():
            self.session.delete(link)
        self.session.delete(file)
        self.session.commit()

    async def link_file(self, session_id: UUID, file_id: UUID) -> None:
        if self.session.get(ChatSessionFile, (session_id, file_id)) is None:
            self.session.add(ChatSessionFile(session_id=session_id, file_id=file_id))
            self.session.commit()

    async def unlink_file(self, session_id: UUID, file_id: UUID) -> None:
        link = self.session.get(ChatSessionFile, (session_id, file_id))
        if link is not None:
            self.session.delete(link)
            self.session.commit()

    async def list_session_files(self, session_id: UUID) -> list[UserFile]:
        return list(
            self.session.exec(
                select(UserFile)
                .join(ChatSessionFile, col(ChatSessionFile.file_id) == col(UserFile.id))
                .where(ChatSessionFile.session_id == session_id)
                .order_by(col(ChatSessionFile.created_at), col(UserFile.id))
            ).all()
        )

    async def link_document(self, session_id: UUID, document_id: UUID) -> None:
        if self.session.get(ChatSessionDocument, (session_id, document_id)) is None:
            self.session.add(ChatSessionDocument(session_id=session_id, document_id=document_id))
            self.session.commit()

    async def unlink_document(self, session_id: UUID, document_id: UUID) -> None:
        link = self.session.get(ChatSessionDocument, (session_id, document_id))
        if link is not None:
            self.session.delete(link)
            self.session.commit()

    async def list_session_document_ids(self, session_id: UUID) -> list[UUID]:
        return list(
            self.session.exec(
                select(ChatSessionDocument.document_id)
                .where(ChatSessionDocument.session_id == session_id)
                .order_by(col(ChatSessionDocument.created_at))
            ).all()
        )


__all__ = [
    "ChatRepository",
    "InMemoryChatRepository",
    "SQLModelChatRepository",
    "ChatRepositoryError",
    "ChatSessionNotFoundError",
    "ChatFileNotFoundError",
]
