"""The chat model picker's catalog: which models are offered and usable.

An option is "available" only when its provider's API key is configured
(docs/9_29_chat_core_and_attachments_plan.md). Availability never depends on
a network call -- it is a pure function of ``Settings`` -- so listing models
never blocks on a provider.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, settings

@dataclass(frozen=True)
class ChatModel:
    id: str
    label: str
    provider: str
    available: bool
    context_window: int


def _is_available(provider: str, app_settings: Settings) -> bool:
    secret = {
        "openai": app_settings.openai_api_key,
        "gemini": app_settings.gemini_api_key,
        "anthropic": app_settings.anthropic_api_key,
    }.get(provider)
    return bool(secret and secret.get_secret_value())


def _entry_to_model(entry, app_settings: Settings) -> ChatModel:
    return ChatModel(
        id=entry.id,
        label=entry.label,
        provider=entry.provider,
        available=_is_available(entry.provider, app_settings),
        context_window=entry.context_window,
    )


def list_models(app_settings: Settings | None = None, use: str = "chat") -> list[ChatModel]:
    """The catalog from config.yaml, in file order, filtered by its ``use`` list."""

    app_settings = app_settings or settings
    return [_entry_to_model(entry, app_settings) for entry in app_settings.catalog_models if use in entry.use]


def list_visible_models(app_settings: Settings | None = None) -> list[ChatModel]:
    """Chat models minus those the settings page hides from the picker."""

    from app.services import app_settings as overrides

    hidden = set(overrides.value("chat.hidden_models", cfg=app_settings))
    return [m for m in list_models(app_settings) if m.id not in hidden]


def default_model_id(app_settings: Settings | None = None) -> str:
    """The configured default, unless it is hidden/unavailable/unknown.

    Falls back to the first visible available model, then the first available
    one, then the raw configured id (so callers still get a string).
    """

    from app.services import app_settings as overrides

    configured = overrides.value("chat.default_model", cfg=app_settings)
    visible = list_visible_models(app_settings)
    if any(m.id == configured and m.available for m in visible):
        return configured
    for pool in (visible, list_models(app_settings)):
        for model in pool:
            if model.available:
                return model.id
    return configured


def find_available_model(model_id: str, app_settings: Settings | None = None) -> ChatModel | None:
    """Return the catalog entry for ``model_id`` only if it is known and usable."""

    for model in list_models(app_settings):
        if model.id == model_id:
            return model if model.available else None
    return None


__all__ = ["ChatModel", "list_models", "list_visible_models", "default_model_id", "find_available_model"]
