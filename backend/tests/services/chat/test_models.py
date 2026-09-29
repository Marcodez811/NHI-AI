from pydantic import SecretStr

from app.config import Settings
from app.services.chat.models import default_model_id, find_available_model, list_models


def _settings(**overrides) -> Settings:
    fields = dict(
        redis_url="redis://localhost:6379/0",
        openai_api_key=SecretStr("openai-key"),
        gemini_api_key=None,
        anthropic_api_key=None,
    )
    fields.update(overrides)
    return Settings(**fields)


def test_openai_models_available_without_other_provider_keys():
    models = list_models(_settings())
    by_id = {model.id: model for model in models}
    assert by_id["gpt-6-astra"].available is True
    assert by_id["gpt-6-astra"].provider == "openai"
    assert by_id["litellm/gemini/gemini-3.8-flash"].available is False
    assert by_id["litellm/anthropic/claude-sonnet-5"].available is False


def test_configured_provider_keys_make_litellm_models_available():
    settings = _settings(gemini_api_key=SecretStr("gemini-key"), anthropic_api_key=SecretStr("anthropic-key"))
    by_id = {model.id: model for model in list_models(settings)}
    assert by_id["litellm/gemini/gemini-3.8-flash"].available is True
    assert by_id["litellm/anthropic/claude-sonnet-5"].available is True


def test_blank_secret_is_treated_as_not_configured():
    settings = _settings(gemini_api_key=SecretStr(""))
    by_id = {model.id: model for model in list_models(settings)}
    assert by_id["litellm/gemini/gemini-3.8-flash"].available is False


def test_chat_model_options_overrides_the_default_catalog():
    settings = _settings(chat_model_options="gpt-6-luna, litellm/gemini/gemini-3.8-flash")
    ids = [model.id for model in list_models(settings)]
    assert ids == ["gpt-6-luna", "litellm/gemini/gemini-3.8-flash"]


def test_default_model_id_falls_back_to_agent_default_model():
    settings = _settings(agent_default_model="gpt-6-luna")
    assert default_model_id(settings) == "gpt-6-luna"
    settings = _settings(chat_default_model="gpt-6-sol", agent_default_model="gpt-6-luna")
    assert default_model_id(settings) == "gpt-6-sol"


def test_find_available_model_rejects_unknown_and_unavailable_ids():
    settings = _settings()
    assert find_available_model("gpt-6-astra", settings) is not None
    assert find_available_model("litellm/gemini/gemini-3.8-flash", settings) is None
    assert find_available_model("not-a-real-model", settings) is None
