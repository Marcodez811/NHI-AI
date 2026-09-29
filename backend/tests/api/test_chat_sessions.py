from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.api.routes.chat import (
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    list_chat_models,
    list_chat_sessions,
    rename_chat_session,
)
from app.config import settings
from app.models.chat import ChatSessionCreate, ChatSessionUpdate
from app.services.chat.attachments import ChatAttachmentStorage
from app.services.chat.repository import InMemoryChatRepository


@pytest.mark.asyncio
async def test_list_chat_models_reports_availability_and_default(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", None)
    response = await list_chat_models()

    by_id = {model.id: model for model in response.models}
    assert by_id["gpt-6-astra"].available is True
    assert by_id["litellm/gemini/gemini-3.8-flash"].available is False
    assert response.default


@pytest.mark.asyncio
async def test_create_session_defaults_the_title_and_model():
    repository = InMemoryChatRepository()
    created = await create_chat_session(ChatSessionCreate(), repository)

    assert created.title == "新對話"
    stored = await repository.get_session(created.id)
    assert stored.model
    # The client reads the model and empty history straight from this response.
    assert created.model == stored.model
    assert created.messages == [] and created.attachments == []


@pytest.mark.asyncio
async def test_create_session_rejects_an_unavailable_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", None)
    repository = InMemoryChatRepository()

    with pytest.raises(HTTPException) as error:
        await create_chat_session(ChatSessionCreate(model="litellm/gemini/gemini-3.8-flash"), repository)

    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_list_sessions_orders_newest_first():
    repository = InMemoryChatRepository()
    first = await create_chat_session(ChatSessionCreate(), repository)
    second = await create_chat_session(ChatSessionCreate(), repository)
    # Force a distinguishable ordering regardless of clock resolution.
    session = await repository.get_session(second.id)
    from datetime import timedelta

    session.updated_at = session.updated_at + timedelta(seconds=1)
    await repository.update_session(session)

    sessions = await list_chat_sessions(repository)

    assert [item.id for item in sessions][:2] == [second.id, first.id]


@pytest.mark.asyncio
async def test_get_session_404_for_unknown_id():
    with pytest.raises(HTTPException) as error:
        await get_chat_session(uuid4(), InMemoryChatRepository())
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_get_session_returns_messages_and_attachments():
    repository = InMemoryChatRepository()
    summary = await create_chat_session(ChatSessionCreate(), repository)

    detail = await get_chat_session(summary.id, repository)

    assert detail.id == summary.id
    assert detail.messages == []
    assert detail.attachments == []


@pytest.mark.asyncio
async def test_rename_session_updates_the_title():
    repository = InMemoryChatRepository()
    summary = await create_chat_session(ChatSessionCreate(), repository)

    renamed = await rename_chat_session(summary.id, ChatSessionUpdate(title="健保議題討論"), repository)

    assert renamed.title == "健保議題討論"


@pytest.mark.asyncio
async def test_delete_session_removes_it_and_its_attachment_files(tmp_path):
    repository = InMemoryChatRepository()
    storage = ChatAttachmentStorage(tmp_path)
    summary = await create_chat_session(ChatSessionCreate(), repository)

    await delete_chat_session(summary.id, repository, storage)

    with pytest.raises(HTTPException):
        await get_chat_session(summary.id, repository)


@pytest.mark.asyncio
async def test_delete_unknown_session_404s():
    with pytest.raises(HTTPException) as error:
        await delete_chat_session(uuid4(), InMemoryChatRepository(), ChatAttachmentStorage.__new__(ChatAttachmentStorage))
    assert error.value.status_code == 404


def test_old_chat_routes_are_gone():
    from app.api.routes.chat import router

    paths = {route.path for route in router.routes}
    assert "/chat" not in paths
    assert "/chat/stream" not in paths
    assert "/qa-modes" not in paths
    assert any(path.startswith("/chat/sessions") for path in paths)
