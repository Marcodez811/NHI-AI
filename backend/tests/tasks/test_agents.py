from app.services.agentic.events import AgentEventType
from app.tasks.agents import _progress_telemetry_type


def test_progress_telemetry_classifies_provider_work_as_node_progress():
    assert _progress_telemetry_type({"event_type": "node_progress"}) is AgentEventType.NODE_PROGRESS
    assert _progress_telemetry_type({"heartbeat": True}) is AgentEventType.HEARTBEAT
    assert _progress_telemetry_type({"phase": "reviewing"}) is AgentEventType.PHASE_CHANGED
