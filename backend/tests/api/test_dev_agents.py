from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes.dev_agents import get_agent_run, get_agent_run_events, list_agent_runs
from app.services.agentic.events import AgentTelemetryError, AgentTelemetryStore
from tests.services.agentic.test_events import FakeRedis


@pytest.mark.asyncio
async def test_dev_agent_routes_return_sanitized_run_and_incremental_events():
    store = AgentTelemetryStore(FakeRedis())
    await store.start_run(run_id="run-api", workflow="slides", runner="codex")
    await store.emit(run_id="run-api", event_type="phase_changed", phase="drafting", message="Drafting presentation")

    runs = await list_agent_runs(store, limit=50)
    detail = await get_agent_run("run-api", store)
    page = await get_agent_run_events("run-api", store, after=1, limit=200)

    assert runs.runs[0].run_id == "run-api"
    assert detail["workflow"] == "slides"
    assert [event.sequence for event in page.events] == [2]
    assert page.next_after == 2


@pytest.mark.asyncio
async def test_dev_agent_routes_use_404_for_unknown_run_and_503_for_redis_failure():
    store = AgentTelemetryStore(FakeRedis())
    with pytest.raises(HTTPException) as missing:
        await get_agent_run("missing", store)
    assert missing.value.status_code == 404

    class BrokenStore:
        async def get_runs(self, **_kwargs):
            raise AgentTelemetryError("redis offline")

    with pytest.raises(HTTPException) as unavailable:
        await list_agent_runs(BrokenStore(), limit=50)  # type: ignore[arg-type]
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == "Agent telemetry is temporarily unavailable."
