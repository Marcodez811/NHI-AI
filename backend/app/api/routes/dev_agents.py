"""Gated development-only agent tracing API.

These endpoints are intentionally not registered unless
``ENABLE_AGENT_DEV_ROUTES`` is true.  There is no authentication layer on
this application, so deployments must keep the setting disabled unless the
service is on a trusted development network.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.services.agentic.events import (
    AgentEventPage,
    AgentRunListResponse,
    AgentTelemetryError,
    AgentTelemetryStore,
)

router = APIRouter(prefix="/dev", tags=["agent-development"])


def get_agent_telemetry_store() -> AgentTelemetryStore:
    """Resolve the shared Redis client lazily to avoid an app import cycle."""

    # ``main`` owns the application's long-lived client.  Importing lazily
    # keeps this route module usable in unit tests and avoids importing the
    # FastAPI application while it is constructing its router graph.
    from app.main import redis_client
    from app.services import app_settings

    return AgentTelemetryStore(
        redis_client,
        retention_seconds=app_settings.value("agents.limits.event_retention_seconds"),
    )


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Agent telemetry is temporarily unavailable.",
    )


@router.get("/agent-runs", response_model=AgentRunListResponse)
async def list_agent_runs(
    store: Annotated[AgentTelemetryStore, Depends(get_agent_telemetry_store)],
    limit: int = Query(default=50, ge=1, le=100),
) -> AgentRunListResponse:
    try:
        return AgentRunListResponse(runs=await store.get_runs(limit=limit))
    except AgentTelemetryError as exc:
        raise _unavailable(exc) from exc
    except Exception as exc:  # noqa: BLE001 - never expose Redis/client details
        raise _unavailable(exc) from exc


@router.get("/agent-runs/{run_id}")
async def get_agent_run(
    run_id: str,
    store: Annotated[AgentTelemetryStore, Depends(get_agent_telemetry_store)],
) -> dict[str, Any]:
    try:
        snapshot = await store.get_run(run_id)
    except AgentTelemetryError as exc:
        raise _unavailable(exc) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run was not found.") from exc
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run was not found.")
    return snapshot.model_dump(mode="json")


@router.get("/agent-runs/{run_id}/events", response_model=AgentEventPage)
async def get_agent_run_events(
    run_id: str,
    store: Annotated[AgentTelemetryStore, Depends(get_agent_telemetry_store)],
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> AgentEventPage:
    try:
        # A missing summary means the run expired or never existed.  Checking
        # it here gives the events endpoint the same 404 semantics as detail,
        # even when Redis has already trimmed/expired the stream itself.
        if await store.get_run(run_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run was not found.")
        events = await store.get_events(run_id, after=after, limit=limit)
        next_after = events[-1].sequence if events else after
        return AgentEventPage(events=events, after=after, next_after=next_after)
    except HTTPException:
        raise
    except AgentTelemetryError as exc:
        raise _unavailable(exc) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run was not found.") from exc
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


__all__ = ["get_agent_run", "get_agent_run_events", "get_agent_telemetry_store", "list_agent_runs", "router"]
