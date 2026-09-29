"""The chat model picker's catalog: which models are offered and usable.

An option is "available" only when its provider's API key is configured
(docs/9_29_chat_core_and_attachments_plan.md). Availability never depends on
a network call -- it is a pure function of ``Settings`` -- so listing models
never blocks on a provider.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, settings

# OpenAI models are sent as plain names; Gemini/Anthropic go through LiteLLM,
# matching how app.services.agentic.sdk_runner routes "litellm/<provider>/..."
# model names for the rest of the app.
_DEFAULT_MODEL_IDS: tuple[str, ...] = (
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "litellm/gemini/gemini-3.8-flash",
    "litellm/anthropic/claude-sonnet-5",
)

_LABELS: dict[str, str] = {
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-6-sol": "GPT-6 Sol",
    "gpt-6-luna": "GPT-6 Luna",
    "litellm/gemini/gemini-3.8-flash": "Gemini 3.8 Flash",
    "litellm/anthropic/claude-sonnet-5": "Claude Sonnet 5",
}


@dataclass(frozen=True)
class ChatModel:
    id: str
    label: str
    provider: str
    available: bool


def _provider_for(model_id: str) -> str:
    if model_id.startswith("litellm/"):
        return model_id.removeprefix("litellm/").partition("/")[0]
    return "openai"


def _configured_ids(app_settings: Settings) -> list[str]:
    raw = app_settings.chat_model_options
    if not raw:
        return list(_DEFAULT_MODEL_IDS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _is_available(provider: str, app_settings: Settings) -> bool:
    secret = {
        "openai": app_settings.openai_api_key,
        "gemini": app_settings.gemini_api_key,
        "anthropic": app_settings.anthropic_api_key,
    }.get(provider)
    return bool(secret and secret.get_secret_value())


def list_models(app_settings: Settings | None = None) -> list[ChatModel]:
    app_settings = app_settings or settings
    models: list[ChatModel] = []
    for model_id in _configured_ids(app_settings):
        provider = _provider_for(model_id)
        models.append(
            ChatModel(
                id=model_id,
                label=_LABELS.get(model_id, model_id),
                provider=provider,
                available=_is_available(provider, app_settings),
            )
        )
    return models


def default_model_id(app_settings: Settings | None = None) -> str:
    app_settings = app_settings or settings
    return app_settings.chat_default_model or app_settings.agent_default_model


def find_available_model(model_id: str, app_settings: Settings | None = None) -> ChatModel | None:
    """Return the catalog entry for ``model_id`` only if it is known and usable."""

    for model in list_models(app_settings):
        if model.id == model_id:
            return model if model.available else None
    return None


__all__ = ["ChatModel", "list_models", "default_model_id", "find_available_model"]
