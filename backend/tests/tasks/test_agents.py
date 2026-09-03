from app.services.agentic.events import AgentEventType
from app.tasks.agents import _progress_telemetry_type


def test_progress_telemetry_classifies_provider_work_as_node_progress():
    assert _progress_telemetry_type({"event_type": "node_progress"}) is AgentEventType.NODE_PROGRESS
    assert _progress_telemetry_type({"heartbeat": True}) is AgentEventType.HEARTBEAT
    assert _progress_telemetry_type({"phase": "reviewing"}) is AgentEventType.PHASE_CHANGED


def test_worker_builds_allowlisted_codex_runner_with_default_model(monkeypatch):
    from app.config import settings
    from app.tasks.agents import _build_runner_registry

    monkeypatch.setattr(settings, "agent_default_model", "worker-default")
    monkeypatch.setattr(settings, "openai_api_key", "worker-key")
    registry, timeout = _build_runner_registry()
    runner = registry.resolve("codex")

    assert runner.name == "codex"
    assert runner.codex_runner.model == "worker-default"
    assert runner.codex_runner.reasoning_effort.value == "high"
    assert runner.codex_runner.api_key == "worker-key"
    assert timeout == runner.timeout_seconds
