from app.services.agentic.events import AgentEventType
from types import SimpleNamespace

import pytest

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
    monkeypatch.setattr(settings, "agent_heartbeat_seconds", 10.0)
    registry, timeout = _build_runner_registry()
    runner = registry.resolve("codex")

    assert runner.name == "codex"
    assert runner.codex_runner.model == "worker-default"
    assert runner.codex_runner.reasoning_effort.value == "high"
    assert runner.codex_runner.api_key == "worker-key"
    assert runner.codex_runner.heartbeat_seconds == 10.0
    assert timeout == runner.timeout_seconds


@pytest.mark.asyncio
async def test_node_progress_enrichment_keeps_heartbeats_attributed_to_the_active_attempt():
    from app.services.agentic.service import _node_progress_callback

    received = []

    async def progress(event):
        received.append(event)

    callback = _node_progress_callback(
        progress,
        SimpleNamespace(
            node_id="author",
            role="presentation_author",
            model="author-model",
            attempt=2,
        ),
        "codex",
    )
    await callback({"heartbeat": True, "stage": "drafting"})

    assert received == [{
        "heartbeat": True,
        "stage": "drafting",
        "node_id": "author",
        "role": "presentation_author",
        "runner": "codex",
        "model": "author-model",
        "attempt": 2,
    }]
