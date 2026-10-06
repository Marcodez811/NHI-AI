from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.db as db
from app.api.routes.app_settings import router
from app.api.routes.chat import list_chat_models
from app.config import Settings, settings
from app.db import get_session
from app.models.chat import ChatMessageCreate
from app.services import app_settings as svc
from app.services.agentic.model_settings import resolve_all_stage_settings
from app.services.chat import models as chat_models


@pytest.fixture
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("k"))
    return eng


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_settings", True)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def test_migration_creates_table(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'm.db'}")
    command.upgrade(config, "head")
    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    from sqlalchemy import inspect

    assert {c["name"] for c in inspect(eng).get_columns("app_setting_overrides")} == {"key", "value", "updated_at"}


def test_every_registry_key_maps_to_a_setting_or_is_hidden_models():
    for d in svc.REGISTRY:
        assert d.field_name or d.key == "chat.hidden_models"


def test_resolver_order_database_env_config_default(engine, monkeypatch, tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("uploads:\n  chat:\n    max_attachments: 7\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(cfg))
    monkeypatch.delenv("CHAT_MAX_ATTACHMENTS", raising=False)
    monkeypatch.setattr(settings, "chat_max_attachments", 7)
    assert svc.effective("uploads.chat.max_attachments") == (7, "config")
    assert svc.effective("uploads.chat.max_pdf_pages")[1] in ("default", "config")
    monkeypatch.setenv("CHAT_MAX_ATTACHMENTS", "8")
    monkeypatch.setattr(settings, "chat_max_attachments", 8)
    assert svc.effective("uploads.chat.max_attachments") == (8, "env")
    with Session(engine) as s:
        svc.set_override(s, svc.get_def("uploads.chat.max_attachments"), 4)
    assert svc.effective("uploads.chat.max_attachments") == (4, "database")


def test_put_validation_types_range_and_unknown(client):
    url = "/api/v1/app-settings/"
    assert client.put(url + "uploads.chat.max_attachments", json={"value": "x"}).status_code == 422
    assert client.put(url + "uploads.chat.max_attachments", json={"value": True}).status_code == 422
    assert client.put(url + "uploads.chat.max_attachments", json={"value": 1.5}).status_code == 422
    bad = client.put(url + "uploads.chat.max_attachments", json={"value": 0})
    assert bad.status_code == 422 and "之間" in bad.json()["detail"]
    assert client.put(url + "agents.limits.keep_workspace_on_failure", json={"value": 1}).status_code == 422
    assert client.put(url + "uploads.chat.max_attachments", json={}).status_code == 422
    assert client.put(url + "nope", json={"value": 1}).status_code == 404
    ok = client.put(url + "uploads.chat.max_attachments", json={"value": 3})
    assert ok.status_code == 200 and ok.json()["value"] == 3 and ok.json()["source"] == "database"


def test_dev_keys_are_403_and_hidden_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_dev_settings", False)
    assert client.put("/api/v1/app-settings/chat.timeout_seconds", json={"value": 30}).status_code == 403
    assert client.delete("/api/v1/app-settings/chat.timeout_seconds").status_code == 403
    body = client.get("/api/v1/app-settings").json()
    assert body["dev_enabled"] is False
    keys = {s["key"] for g in body["groups"] for s in g["settings"]}
    assert keys == {"chat.default_model", "chat.hidden_models"}
    # user keys still work
    assert client.put("/api/v1/app-settings/chat.default_model", json={"value": "gpt-6.1-sol"}).status_code == 200


def test_delete_resets_to_default(client):
    url = "/api/v1/app-settings/chat.compaction_threshold"
    assert client.put(url, json={"value": 0.5}).json()["source"] == "database"
    reset = client.delete(url).json()
    assert reset["source"] != "database" and reset["value"] == reset["default_value"]


def test_override_applies_live_without_restart(client):
    def build():
        return ChatMessageCreate(content="hi", attachment_ids=[__import__("uuid").uuid4() for _ in range(4)], model="m")

    build()  # default limit is 10
    client.put("/api/v1/app-settings/uploads.chat.max_attachments", json={"value": 3})
    with pytest.raises(ValidationError):
        build()
    client.delete("/api/v1/app-settings/uploads.chat.max_attachments")
    build()


def test_hidden_models_filter_picker_and_default_falls_back(client):
    default = chat_models.default_model_id()
    other = next(m.id for m in chat_models.list_models() if m.available and m.id != default)
    # cannot hide the default
    assert client.put("/api/v1/app-settings/chat.hidden_models", json={"value": [default]}).status_code == 422
    assert client.put("/api/v1/app-settings/chat.hidden_models", json={"value": ["nope"]}).status_code == 422
    assert client.put("/api/v1/app-settings/chat.hidden_models", json={"value": [other]}).status_code == 200
    import asyncio

    listed = asyncio.run(list_chat_models())
    assert other not in [m.id for m in listed.models] and listed.default == default
    # cannot choose a hidden model as default
    assert client.put("/api/v1/app-settings/chat.default_model", json={"value": other}).status_code == 422
    # an unavailable model cannot be the default
    unavailable = next((m.id for m in chat_models.list_models() if not m.available), None)
    if unavailable:
        assert client.put("/api/v1/app-settings/chat.default_model", json={"value": unavailable}).status_code == 422


def test_default_falls_back_when_configured_default_is_hidden(engine):
    default = chat_models.default_model_id()
    with Session(engine) as s:  # simulate a hide that bypassed validation
        svc.set_override(s, svc.get_def("chat.hidden_models"), [default])
    fallback = chat_models.default_model_id()
    assert fallback != default
    assert chat_models.find_available_model(fallback) is not None


def test_planner_generic_setting_is_base_layer_for_per_workflow_rows(engine, monkeypatch):
    monkeypatch.setattr(settings, "agent_planner_enabled", False)
    with Session(engine) as s:
        assert resolve_all_stage_settings(s, "slides", ["planner"])["planner"].planner_enabled is False
        svc.set_override(s, svc.get_def("agents.stages.planner.enabled"), True)
        assert resolve_all_stage_settings(s, "slides", ["planner"])["planner"].planner_enabled is True
        from app.models.agent_settings import AgentStageSettings

        s.add(AgentStageSettings(workflow="slides", stage="planner", planner_enabled=False))
        s.commit()
        assert resolve_all_stage_settings(s, "slides", ["planner"])["planner"].planner_enabled is False


def test_restart_required_flags_are_only_startup_consumed_values():
    assert {d.key for d in svc.REGISTRY if d.restart_required} == {
        "agents.limits.awaiting_outline_sweep_interval_seconds",
        "agents.limits.event_retention_seconds",
        "cleanup.reconcile_interval_seconds",
    }
