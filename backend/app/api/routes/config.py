"""Read-only product configuration for the frontend (config.yaml)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import app_settings
from app.services.chat import models as chat_models

router = APIRouter(tags=["config"])


class ModelOption(BaseModel):
    id: str
    label: str
    provider: str
    available: bool


class ChatLimits(BaseModel):
    max_attachments: int
    max_message_chars: int
    max_document_bytes: int
    max_pdf_bytes: int
    max_image_bytes: int


class ClientConfig(BaseModel):
    chat: ChatLimits
    max_upload_bytes: int


@router.get("/models", response_model=list[ModelOption])
async def list_catalog_models(use: Literal["chat", "agents", "codex"] = "chat") -> list[ModelOption]:
    return [
        ModelOption(id=m.id, label=m.label, provider=m.provider, available=m.available)
        for m in chat_models.list_models(use=use)
    ]


@router.get("/config/client", response_model=ClientConfig)
async def get_client_config() -> ClientConfig:
    return ClientConfig(
        chat=ChatLimits(
            max_attachments=app_settings.value("uploads.chat.max_attachments"),
            max_message_chars=app_settings.value("uploads.chat.max_message_chars"),
            max_document_bytes=app_settings.value("uploads.chat.max_document_bytes"),
            max_pdf_bytes=app_settings.value("uploads.chat.max_pdf_bytes"),
            max_image_bytes=app_settings.value("uploads.chat.max_image_bytes"),
        ),
        max_upload_bytes=app_settings.value("uploads.max_upload_bytes"),
    )
