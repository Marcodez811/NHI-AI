"""config.yaml loading, the model catalog and the config API (Phase 1)."""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.routes.config import router
from app.config import DEFAULT_CONFIG_FILE, Settings, load_yaml_values
from app.services.chat import attachments as attachments_module
from app.services.chat.attachments import ChatAttachmentStorage, process_upload
from app.services.chat.models import list_models

BASE_ENV = {"REDIS_URL": "redis://localhost", "OPENAI_API_KEY": "test"}


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _model(**overrides) -> dict:
    entry = {"id": "gpt-6-luna", "label": "Luna", "provider": "openai", "use": ["chat"]}
    entry.update(overrides)
    return entry


@pytest.fixture
def env(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("CHAT_TIMEOUT_SECONDS", "CHAT_MAX_ATTACHMENTS", "CONFIG_FILE", "AGENT_DEFAULT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_shipped_yaml_loads_with_ten_models_in_order(env):
    settings = Settings(_env_file=None)
    ids = [m.id for m in settings.catalog_models]
    assert len(ids) == 10 and ids[0] == "litellm/anthropic/claude-fable-5-1" and ids[-1].endswith("gemini-3.1-pro-preview")
    assert DEFAULT_CONFIG_FILE.is_file()


def test_yaml_value_is_used_and_env_overrides_it(env, tmp_path):
    env.setenv("CONFIG_FILE", str(_write(tmp_path, {"chat": {"timeout_seconds": 42}})))
    assert Settings(_env_file=None).chat_timeout_seconds == 42
    env.setenv("CHAT_TIMEOUT_SECONDS", "7")
    assert Settings(_env_file=None).chat_timeout_seconds == 7


def test_aliased_env_var_still_overrides_yaml(env, tmp_path):
    env.setenv("CONFIG_FILE", str(_write(tmp_path, {"agents": {"default_model": "from-yaml"}})))
    assert Settings(_env_file=None).agent_default_model == "from-yaml"
    env.setenv("AGENT_DEFAULT_MODEL", "from-env")
    assert Settings(_env_file=None).agent_default_model == "from-env"


@pytest.mark.parametrize(
    ("models", "needle"),
    [
        ([_model(provider="mistral")], "models[0].provider"),
        ([_model(label="")], "models[0].label"),
        ([_model(use=[])], "models[0].use"),
        ([_model(), _model()], "duplicate model id"),
    ],
)
def test_bad_model_entry_fails_with_the_yaml_path(env, tmp_path, models, needle):
    path = _write(tmp_path, {"models": models})
    with pytest.raises(ValueError, match=needle.replace("[", r"\[").replace("]", r"\]")):
        load_yaml_values(path)


def test_unknown_yaml_key_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="chat.nope"):
        load_yaml_values(_write(tmp_path, {"chat": {"nope": 1}}))


def test_catalog_order_labels_and_use_filter_come_from_yaml(env, tmp_path):
    models = [
        _model(id="b-model", label="Bee", use=["chat", "codex"]),
        _model(id="a-model", label="Aye", use=["agents"]),
        _model(id="c-model", label="Sea", provider="gemini", use=["chat"], context_window=1000),
    ]
    env.setenv("CONFIG_FILE", str(_write(tmp_path, {"models": models})))
    settings = Settings(_env_file=None, gemini_api_key=SecretStr("g"))
    assert [(m.id, m.label) for m in list_models(settings)] == [("b-model", "Bee"), ("c-model", "Sea")]
    assert [m.id for m in list_models(settings, use="codex")] == ["b-model"]
    assert [m.id for m in list_models(settings, use="agents")] == ["a-model"]
    assert list_models(settings)[1].context_window == 1000


@pytest.fixture
def client(monkeypatch):
    import app.services.app_settings as routes

    custom = Settings(
        redis_url="redis://localhost",
        openai_api_key=SecretStr("k"),
        chat_max_attachments=3,
        chat_max_pdf_bytes=1234,
        max_upload_bytes=999,
        catalog_models=Settings(_env_file=None, redis_url="r", openai_api_key=SecretStr("k")).catalog_models,
    )
    monkeypatch.setattr(routes, "settings", custom)
    from app.services.chat import models as chat_models

    monkeypatch.setattr(chat_models, "settings", custom)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_models_endpoint_filters_by_use(client):
    codex = client.get("/models", params={"use": "codex"}).json()
    assert [m["id"] for m in codex] == ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"]
    assert set(codex[0]) == {"id", "label", "provider", "available"}
    agents = client.get("/models", params={"use": "agents"}).json()
    assert len(agents) == 10
    assert client.get("/models", params={"use": "bogus"}).status_code == 422


def test_client_config_reflects_yaml_values(client):
    body = client.get("/config/client").json()
    assert body["chat"]["max_attachments"] == 3
    assert body["chat"]["max_pdf_bytes"] == 1234
    assert body["max_upload_bytes"] == 999


class _Upload:
    def __init__(self, name, data):
        self.filename, self._data, self.content_type = name, data, "application/pdf"

    async def read(self):
        return self._data


@pytest.mark.asyncio
async def test_attachment_validation_uses_configured_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments_module.settings, "chat_max_pdf_bytes", 50)
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(attachments_module.AttachmentError, match="上限"):
        await process_upload(storage, uuid4(), _Upload("a.pdf", b"%PDF-" + b"0" * 100))
