from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models.agent_settings import AgentStageSettings
from app.services.agentic.contracts import AgentReasoningEffort
from app.services.agentic.model_settings import (
    STAGES,
    StageModelSettings,
    load_snapshot,
    resolve_all_stage_settings,
    write_snapshot,
)

_SLIDES_STAGES = ("extraction", "planner", "author", "reviewer")
_NEWS_STAGES = ("extraction", "author")


def test_database_values_override_environment_and_nullable_fields_fall_back(monkeypatch):
    monkeypatch.setattr(settings, "agent_author_model", "environment-author")
    monkeypatch.setattr(settings, "agent_author_reasoning_effort", None)
    monkeypatch.setattr(settings, "agent_default_reasoning_effort", AgentReasoningEffort.MEDIUM)
    monkeypatch.setattr(settings, "agent_extraction_model", None)
    monkeypatch.setattr(settings, "agent_default_model", "environment-default")
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AgentStageSettings(
                workflow="slides",
                stage="author",
                runner="codex",
                model="custom-model",
                reasoning_effort="low",
            )
        )
        session.commit()
        values = resolve_all_stage_settings(session, "slides", _SLIDES_STAGES)
        assert values["author"].model == "custom-model"
        assert values["author"].reasoning_effort is AgentReasoningEffort.LOW
        assert values["author"].planner_enabled is None
        assert values["planner"].planner_enabled is not None
        assert values["extraction"].model == "environment-default"
        session.add(AgentStageSettings(workflow="slides", stage="extraction", model=None, reasoning_effort=None))
        session.add(AgentStageSettings(workflow="slides", stage="reviewer", model=None, reasoning_effort=None))
        session.commit()
        values = resolve_all_stage_settings(session, "slides", _SLIDES_STAGES)
        assert values["extraction"].model == "environment-default"
        assert values["author"].model == "custom-model"
        assert values["reviewer"].model == settings.agent_reviewer_model
        assert values["extraction"].reasoning_effort is AgentReasoningEffort.MEDIUM


def test_stage_settings_are_scoped_to_their_workflow(monkeypatch):
    """A slides-only row must not leak into news' resolution of the same stage name."""

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AgentStageSettings(workflow="slides", stage="author", model="slides-only-model")
        )
        session.commit()
        slides_values = resolve_all_stage_settings(session, "slides", _SLIDES_STAGES)
        news_values = resolve_all_stage_settings(session, "news", _NEWS_STAGES)
        assert slides_values["author"].model == "slides-only-model"
        assert news_values["author"].model != "slides-only-model"
        assert set(news_values) == {"extraction", "author"}


def test_settings_snapshot_round_trips_enum_and_is_absent_until_written(tmp_path):
    assert load_snapshot(tmp_path, STAGES) is None
    values = {
        stage: StageModelSettings(
            runner="codex",
            model="gpt-5.6-luna",
            reasoning_effort=AgentReasoningEffort.HIGH,
            planner_enabled=False if stage == "planner" else None,
        )
        for stage in ("extraction", "planner", "author", "reviewer")
    }
    write_snapshot(tmp_path, values)
    assert load_snapshot(tmp_path, STAGES) == values


def test_old_four_stage_snapshot_still_loads_for_a_two_stage_workflow(tmp_path):
    """A job parked at awaiting_outline before news had its own stage list wrote all 4."""

    values = {
        stage: StageModelSettings(
            runner="codex",
            model="gpt-5.6-luna",
            reasoning_effort=AgentReasoningEffort.HIGH,
            planner_enabled=False if stage == "planner" else None,
        )
        for stage in STAGES
    }
    write_snapshot(tmp_path, values)
    loaded = load_snapshot(tmp_path, _NEWS_STAGES)
    assert set(loaded) == {"extraction", "author"}
    assert loaded["extraction"] == values["extraction"]
    assert loaded["author"] == values["author"]


def test_malformed_settings_snapshot_fails_closed(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "model_settings.json").write_text('{"author": {}}', encoding="utf-8")
    try:
        load_snapshot(tmp_path, _SLIDES_STAGES)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed settings snapshots must not be accepted")
