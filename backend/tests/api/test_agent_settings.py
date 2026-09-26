import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes.agent_settings import StoredSettings, get_agent_settings, put_agent_settings, router
from app.config import settings
from app.db import get_session
from app.models.agent_settings import AgentStageSettings


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def test_get_and_put_return_effective_policy_without_credentials(session):
    before = get_agent_settings("slides", session)
    assert set(before) == {"stages", "providers"}
    assert before["stages"]["author"]["stored"]["model"] is None
    assert "api_key" not in str(before).lower()

    response = put_agent_settings(
        "slides",
        "author",
        StoredSettings(
            runner="codex",
            model="custom-model",
            reasoning_effort="low",
            planner_enabled=None,
        ),
        session,
    )
    assert response["stages"]["author"]["stored"]["model"] == "custom-model"
    assert response["stages"]["author"]["effective"]["model"] == {
        "value": "custom-model",
        "source": "database",
    }
    assert session.get(AgentStageSettings, ("slides", "author")).reasoning_effort == "low"


def test_update_rejects_wrong_stage_runner_and_codex_litellm(session):
    values = StoredSettings(
        runner="agents",
        model="litellm/gemini/example",
        reasoning_effort=None,
        planner_enabled=None,
    )
    for stage in ("author", "extraction", "reviewer"):
        with pytest.raises(HTTPException) as non_planner:
            put_agent_settings("slides", stage, values, session)
        assert non_planner.value.status_code == 422
        assert "不允許" in non_planner.value.detail

    with pytest.raises(HTTPException) as incompatible:
        put_agent_settings(
            "slides",
            "planner",
            StoredSettings(
                runner="codex",
                model="litellm/gemini/example",
                reasoning_effort=None,
                planner_enabled=False,
            ),
            session,
        )
    assert incompatible.value.status_code == 422


def test_planner_requires_configured_provider_key(session, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", None)
    with pytest.raises(HTTPException) as missing_key:
        put_agent_settings("slides", "planner", StoredSettings(
            runner="agents", model="litellm/gemini/gemini-2.5-pro",
            reasoning_effort="low", planner_enabled=True,
        ), session)
    assert missing_key.value.status_code == 422
    assert "金鑰" in missing_key.value.detail
    assert session.get(AgentStageSettings, ("slides", "planner")) is None

    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("private-gemini-key"))
    saved = put_agent_settings("slides", "planner", StoredSettings(
        runner="agents", model="litellm/gemini/gemini-2.5-pro",
        reasoning_effort="low", planner_enabled=True,
    ), session)
    assert saved["stages"]["planner"]["effective"]["runner"]["value"] == "agents"
    assert saved["providers"]["gemini"] is True
    assert "private-gemini-key" not in str(saved)


def test_invalid_effort_returns_chinese_422(session):
    with pytest.raises(HTTPException) as invalid:
        put_agent_settings("slides", "author", {
            "runner": "codex", "model": "gpt-5",
            "reasoning_effort": "extreme", "planner_enabled": None,
        }, session)
    assert invalid.value.status_code == 422
    assert "推理程度" in invalid.value.detail


def test_http_settings_route_rejects_invalid_payload_in_chinese(session):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        response = client.get("/api/v1/agent-settings/slides")
        assert response.status_code == 200
        assert set(response.json()["stages"]) == {"extraction", "planner", "author", "reviewer"}
        invalid = client.put("/api/v1/agent-settings/slides/author", json={
            "runner": "agents", "model": "litellm/gemini/example",
            "reasoning_effort": None, "planner_enabled": None,
        })
        assert invalid.status_code == 422
        assert "不允許" in invalid.json()["detail"]
        malformed = client.put("/api/v1/agent-settings/slides/author", json={"reasoning_effort": "impossible"})
        assert malformed.status_code == 422
        assert "設定欄位" in malformed.json()["detail"]


def test_clearing_model_validates_against_environment_fallback_not_old_database_value(session, monkeypatch):
    monkeypatch.setattr(settings, "agent_planner_model", None)
    session.add(
        AgentStageSettings(
            workflow="slides",
            stage="planner",
            runner="agents",
            model="litellm/gemini/old-model",
        )
    )
    session.commit()
    with pytest.raises(HTTPException) as incompatible:
        put_agent_settings(
            "slides",
            "planner",
            StoredSettings(
                runner="agents",
                model=None,
                reasoning_effort=None,
                planner_enabled=None,
            ),
            session,
        )
    assert incompatible.value.status_code == 422


def test_slides_and_news_author_settings_are_independent(session):
    put_agent_settings("slides", "author", StoredSettings(
        runner="codex", model="slides-author-model", reasoning_effort=None, planner_enabled=None,
    ), session)
    put_agent_settings("news", "author", StoredSettings(
        runner="codex", model="news-author-model", reasoning_effort=None, planner_enabled=None,
    ), session)

    slides_settings = get_agent_settings("slides", session)
    news_settings = get_agent_settings("news", session)
    assert slides_settings["stages"]["author"]["stored"]["model"] == "slides-author-model"
    assert news_settings["stages"]["author"]["stored"]["model"] == "news-author-model"


def test_news_settings_have_no_planner_or_reviewer_stage(session):
    response = get_agent_settings("news", session)
    assert set(response["stages"]) == {"extraction", "author"}


def test_put_news_reviewer_stage_is_rejected(session):
    with pytest.raises(HTTPException) as invalid:
        put_agent_settings("news", "reviewer", StoredSettings(
            runner="codex", model="gpt-5", reasoning_effort=None, planner_enabled=None,
        ), session)
    assert invalid.value.status_code == 422


def test_unknown_workflow_returns_404(session):
    with pytest.raises(HTTPException) as unknown:
        get_agent_settings("unknown-workflow", session)
    assert unknown.value.status_code == 404
