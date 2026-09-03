"""Sanitized, short-lived telemetry for generic agent workflows.

The telemetry store is deliberately independent from TaskIQ's result backend.
It gives the development console a small, queryable view of a run while
keeping prompts, provider responses, paths, and secrets out of Redis.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from redis.exceptions import WatchError


class AgentEventType(StrEnum):
    """Allowlisted event names written by the coordinator and runner."""

    RUN_STARTED = "run_started"
    PHASE_CHANGED = "phase_changed"
    NODE_STARTED = "node_started"
    NODE_PROGRESS = "node_progress"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    HEARTBEAT = "heartbeat"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class AgentTelemetryError(RuntimeError):
    """A Redis or telemetry serialization failure.

    Callers should treat this as advisory infrastructure failure.  In
    particular, a workflow must not be marked failed just because telemetry
    could not be written.
    """


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")
_SENSITIVE_TEXT = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|api[_ -]?key|authorization|traceback|password|secret|token=)",
    re.IGNORECASE,
)
_MESSAGE_FALLBACK = "Agent workflow is in progress."

# Metadata is intentionally narrow.  The event's typed fields carry the
# useful tracing dimensions; this allowlist only accommodates small, safe
# labels that are useful to a console.
_ALLOWED_METADATA = frozenset(
    {
        "status",
        "phase",
        "stage",
        "runner",
        "attempt",
        "duration_ms",
        "heartbeat",
        "error_code",
        "reason",
        "blocking_count",
        "advisory_count",
        "resolved_count",
        "new_count",
        "persistent_count",
        "stagnant_transitions",
        "decision",
    }
)


def _decode(value: Any) -> Any:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _safe_identifier(value: Any, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("identifier is required")
        return None
    text = str(value).strip()
    if not _SAFE_IDENTIFIER.fullmatch(text):
        if required:
            raise ValueError("identifier contains unsupported characters")
        return None
    return text


def _safe_message(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    # Slash detection keeps filesystem paths, URLs, and command-like content
    # out of the diagnostic surface.  Sensitive strings receive a stable
    # generic message rather than a partially redacted copy.
    if not text or "/" in text or "\\" in text or _SENSITIVE_TEXT.search(text):
        return _MESSAGE_FALLBACK if text else None
    return text[:240]


def _safe_metadata(value: Any) -> dict[str, str | int | float | bool | None]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str | int | float | bool | None] = {}
    for key, item in value.items():
        key_text = str(key).strip()
        if key_text not in _ALLOWED_METADATA:
            continue
        if isinstance(item, bool) or item is None:
            result[key_text] = item
        elif isinstance(item, (int, float)):
            result[key_text] = item
        else:
            clean = _safe_message(item)
            if clean is not None:
                result[key_text] = clean
    return result


class AgentEvent(BaseModel):
    """One sanitized event in a workflow trace."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=160)
    event_type: AgentEventType
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int = Field(default=0, ge=0)
    workflow: str | None = None
    phase: str | None = None
    status: str | None = None
    node_id: str | None = None
    # ``agent_role`` is the diagnostic/API spelling.  ``role`` is accepted as
    # an input alias for coordinator callers that already have an
    # AgentExecutionRequest, then removed before the extra-field check.
    agent_role: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    runner: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    task_id: str | None = None
    worker_id: str | None = None
    provider_run_id: str | None = None
    message: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("run_id", mode="before")
    @classmethod
    def validate_run_id(cls, value: Any) -> str:
        return _safe_identifier(value, required=True) or ""

    @field_validator(
        "workflow",
        "node_id",
        "agent_role",
        "runner",
        "model",
        "reasoning_effort",
        "task_id",
        "worker_id",
        "provider_run_id",
        mode="before",
    )
    @classmethod
    def validate_optional_identifiers(cls, value: Any) -> str | None:
        return _safe_identifier(value)

    @field_validator("message", mode="before")
    @classmethod
    def sanitize_event_message(cls, value: Any) -> str | None:
        return _safe_message(value)

    @field_validator("phase", "status", mode="before")
    @classmethod
    def sanitize_labels(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(getattr(value, "value", value)).strip().lower()
        return text[:80] if text else None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: Any) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError("occurred_at must be a datetime")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="before")
    @classmethod
    def sanitize_metadata(cls, values: Any) -> Any:
        if isinstance(values, Mapping):
            values = dict(values)
            if values.get("agent_role") is None and values.get("role") is not None:
                values["agent_role"] = values["role"]
            values.pop("role", None)
            values["metadata"] = _safe_metadata(values.get("metadata"))
        return values


class AgentNodeSnapshot(BaseModel):
    """Current sanitized state for one logical workflow node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    agent_role: str | None = None
    status: str = "pending"
    runner: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    attempt: int | None = None
    task_id: str | None = None
    worker_id: str | None = None
    provider_run_id: str | None = None
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    message: str | None = None
    last_heartbeat_at: datetime | None = None


class AgentRunSnapshot(BaseModel):
    """Current run summary returned by the development API."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow: str = "unknown"
    status: str = "running"
    phase: str | None = None
    runner: str | None = None
    task_id: str | None = None
    worker_id: str | None = None
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    message: str | None = None
    last_heartbeat_at: datetime | None = None
    last_sequence: int = 0
    nodes: list[AgentNodeSnapshot] = Field(default_factory=list)


class AgentRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[AgentRunSnapshot]


class AgentEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[AgentEvent]
    after: int = 0
    next_after: int = 0


class AgentTelemetryStore:
    """Redis-backed bounded event stream and snapshots.

    Public integration surface:

    ``await store.emit(run_id=..., event_type=..., ...)`` accepts event
    fields, sanitizes them, assigns a per-run sequence, and updates the event
    stream/current snapshots.  ``record_event`` accepts an ``AgentEvent`` (or
    mapping) and is useful when the coordinator already has a typed event.
    ``get_runs``, ``get_run``, and ``get_events`` back the diagnostic API.
    """

    RUNS_KEY = "agents:runs"
    RUN_KEY = "agents:run:{run_id}:summary"
    NODES_KEY = "agents:run:{run_id}:nodes"
    EVENTS_KEY = "agents:run:{run_id}:events"
    SEQUENCE_KEY = "agents:run:{run_id}:sequence"
    DEFAULT_RETENTION_SECONDS = 86_400
    DEFAULT_MAX_EVENTS = 2_000
    MAX_TRANSACTION_RETRIES = 8

    def __init__(
        self,
        redis_client: Any,
        *,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        self.redis = redis_client
        self.retention_seconds = max(1, int(retention_seconds))
        self.max_events = max(1, int(max_events))
        # This avoids needless optimistic-transaction retries when callbacks
        # share one store instance. Cross-worker correctness comes from the
        # Redis WATCH/MULTI transaction in ``record_event``.
        self._lock = asyncio.Lock()

    def _key(self, template: str, run_id: str) -> str:
        return template.format(run_id=run_id)

    async def _call(self, value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    async def _pipeline_call(self, pipeline: Any, method: str, *args: Any, **kwargs: Any) -> None:
        result = getattr(pipeline, method)(*args, **kwargs)
        if inspect.isawaitable(result):
            await result

    async def record_event(
        self,
        event: AgentEvent | Mapping[str, Any],
        **overrides: Any,
    ) -> AgentEvent:
        """Sanitize, sequence, and persist one event."""

        if isinstance(event, AgentEvent):
            payload = event.model_dump(mode="python")
        elif isinstance(event, Mapping):
            payload = dict(event)
        else:
            raise TypeError("event must be an AgentEvent or mapping")
        payload.update(overrides)
        # Sequence is assigned by Redis below, never accepted from callers.
        payload.pop("sequence", None)
        event = AgentEvent.model_validate(payload)
        try:
            async with self._lock:
                return await self._record_event_transactionally(event)
        except AgentTelemetryError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize infrastructure details
            raise AgentTelemetryError("agent telemetry is unavailable") from exc

    async def _record_event_transactionally(self, event: AgentEvent) -> AgentEvent:
        """Commit one event and all derived state as one optimistic transaction.

        The sequence, summary, and node hash are watched together.  A writer
        that observes stale run state retries before assigning its sequence,
        keeping the stream and snapshots ordered by the transaction that
        committed them rather than by an instance-local lock.
        """

        run_id = event.run_id
        sequence_key = self._key(self.SEQUENCE_KEY, run_id)
        summary_key = self._key(self.RUN_KEY, run_id)
        nodes_key = self._key(self.NODES_KEY, run_id)

        for _attempt in range(self.MAX_TRANSACTION_RETRIES):
            pipeline: Any | None = None
            try:
                pipeline = await self._call(self.redis.pipeline(transaction=True))
                await self._call(pipeline.watch(sequence_key, summary_key, nodes_key))

                raw_sequence = await self._call(pipeline.get(sequence_key))
                raw_summary = await self._call(pipeline.get(summary_key))
                raw_nodes = await self._call(pipeline.hgetall(nodes_key))
                previous = self._snapshot_from_raw(raw_summary, raw_nodes)
                # Heartbeats only update the liveness snapshot.  They do not
                # consume a lifecycle cursor or grow the bounded event stream.
                previous_sequence = int(_decode(raw_sequence) or 0)
                sequence = previous_sequence if event.event_type is AgentEventType.HEARTBEAT else previous_sequence + 1
                sequenced_event = AgentEvent.model_validate(
                    {**event.model_dump(mode="python"), "sequence": sequence}
                )
                snapshot, node = self._apply_event(previous, sequenced_event)

                await self._call(pipeline.multi())
                await self._persist(
                    pipeline,
                    sequenced_event,
                    snapshot,
                    node,
                    persist_lifecycle_event=event.event_type is not AgentEventType.HEARTBEAT,
                )
                await self._call(pipeline.execute())
                return sequenced_event
            except WatchError:
                # Another process committed a newer run state after our read.
                # Redis has discarded this transaction, so retry from the
                # latest sequence and snapshots.
                continue
            finally:
                if pipeline is not None:
                    await self._reset_pipeline(pipeline)

        raise AgentTelemetryError("agent telemetry is unavailable")

    async def emit(self, event: AgentEvent | Mapping[str, Any] | None = None, **fields: Any) -> AgentEvent:
        """Coordinator-friendly event publisher.

        Either pass an ``AgentEvent``/mapping as the first argument or pass
        event fields as keywords.  Keyword fields are intentionally validated
        by :class:`AgentEvent` before touching Redis.
        """

        if event is None:
            event = fields
        elif fields:
            if isinstance(event, AgentEvent):
                event = event.model_dump(mode="python")
            event = {**dict(event), **fields}  # type: ignore[arg-type]
        return await self.record_event(event)

    async def start_run(self, *, run_id: str, workflow: str, task_id: str | None = None, worker_id: str | None = None, runner: str | None = None, message: str | None = None) -> AgentEvent:
        return await self.emit(
            run_id=run_id,
            event_type=AgentEventType.RUN_STARTED,
            workflow=workflow,
            task_id=task_id,
            worker_id=worker_id,
            runner=runner,
            message=message or "Agent workflow started.",
        )

    async def _load_snapshot(self, run_id: str) -> AgentRunSnapshot | None:
        key = self._key(self.RUN_KEY, run_id)
        try:
            raw = await self._call(self.redis.get(key))
        except Exception as exc:  # noqa: BLE001
            raise AgentTelemetryError("agent telemetry is unavailable") from exc
        if raw is None:
            return None
        try:
            snapshot = AgentRunSnapshot.model_validate(json.loads(_decode(raw)))
            # The summary deliberately omits the node list to keep updates
            # small; hydrate the node hash before applying the next event so
            # a later node_progress/completed event preserves prior state.
            return await self._with_nodes(snapshot)
        except Exception as exc:  # noqa: BLE001
            raise AgentTelemetryError("agent telemetry is unavailable") from exc

    @staticmethod
    def _snapshot_from_raw(raw_summary: Any, raw_nodes: Any) -> AgentRunSnapshot | None:
        """Build a run snapshot from values read while Redis keys are watched."""

        if raw_summary is None:
            return None
        snapshot = AgentRunSnapshot.model_validate(json.loads(_decode(raw_summary)))
        nodes: list[AgentNodeSnapshot] = []
        for raw_node in (raw_nodes or {}).values():
            nodes.append(AgentNodeSnapshot.model_validate(json.loads(_decode(raw_node))))
        nodes.sort(key=lambda item: item.node_id)
        return snapshot.model_copy(update={"nodes": nodes})

    @staticmethod
    def _apply_event(previous: AgentRunSnapshot | None, event: AgentEvent) -> tuple[AgentRunSnapshot, AgentNodeSnapshot | None]:
        now = event.occurred_at
        if previous is None:
            snapshot = AgentRunSnapshot(
                run_id=event.run_id,
                workflow=event.workflow or "unknown",
                updated_at=now,
            )
        else:
            snapshot = previous.model_copy(deep=True)
        # A retry can commit an event that was created before a concurrent
        # writer. Preserve that event's original ``occurred_at`` while keeping
        # snapshots (and the run-index score derived from this field)
        # monotonic by commit order.
        snapshot.updated_at = max(snapshot.updated_at, now)
        snapshot.last_sequence = event.sequence
        if event.event_type is AgentEventType.HEARTBEAT:
            snapshot.last_heartbeat_at = max(snapshot.last_heartbeat_at or now, now)
            node: AgentNodeSnapshot | None = None
            if event.node_id:
                node = next((item for item in snapshot.nodes if item.node_id == event.node_id), None)
                if node is None:
                    node = AgentNodeSnapshot(node_id=event.node_id, updated_at=now)
                    snapshot.nodes.append(node)
                node.updated_at = max(node.updated_at, now)
                node.last_heartbeat_at = max(node.last_heartbeat_at or now, now)
            return snapshot, node
        if event.workflow:
            snapshot.workflow = event.workflow
        if event.task_id:
            snapshot.task_id = event.task_id
        if event.worker_id:
            snapshot.worker_id = event.worker_id
        if event.runner:
            snapshot.runner = event.runner
        if event.phase:
            snapshot.phase = event.phase
        if event.message:
            snapshot.message = event.message
        # Node status belongs exclusively to the latest node snapshot.  A
        # completed author/reviewer must not make the whole workflow terminal
        # before the coordinator emits its run-completed event.
        if event.status and event.event_type in {
            AgentEventType.RUN_STARTED,
            AgentEventType.PHASE_CHANGED,
            AgentEventType.RUN_COMPLETED,
            AgentEventType.RUN_FAILED,
        }:
            snapshot.status = event.status

        if event.event_type is AgentEventType.RUN_STARTED:
            snapshot.status = "running"
            snapshot.started_at = snapshot.started_at or now
        elif event.event_type is AgentEventType.RUN_COMPLETED:
            snapshot.status = "completed"
            snapshot.finished_at = now
            snapshot.phase = event.phase or "completed"
            snapshot.duration_ms = event.duration_ms if event.duration_ms is not None else (
                max(0, round((now - snapshot.started_at).total_seconds() * 1000))
                if snapshot.started_at else None
            )
        elif event.event_type is AgentEventType.RUN_FAILED:
            snapshot.status = "failed"
            snapshot.finished_at = now
            snapshot.phase = event.phase or "failed"
            snapshot.duration_ms = event.duration_ms if event.duration_ms is not None else (
                max(0, round((now - snapshot.started_at).total_seconds() * 1000))
                if snapshot.started_at else None
            )
        elif event.event_type is AgentEventType.PHASE_CHANGED and snapshot.status not in {"completed", "failed"}:
            snapshot.status = "running"

        node: AgentNodeSnapshot | None = None
        if event.node_id:
            existing = next((item for item in snapshot.nodes if item.node_id == event.node_id), None)
            if existing is None:
                node = AgentNodeSnapshot(node_id=event.node_id, updated_at=now)
                snapshot.nodes.append(node)
            else:
                node = existing
            node.updated_at = max(node.updated_at, now)
            is_new_attempt = (
                event.event_type is AgentEventType.NODE_STARTED
                and event.attempt is not None
                and event.attempt != node.attempt
            )
            if is_new_attempt:
                # A node snapshot represents its latest activation. Clear
                # activation-scoped fields so a retry cannot display the
                # previous attempt's timing or provider identity while it is
                # still running.
                node.started_at = now
                node.finished_at = None
                node.duration_ms = None
                node.provider_run_id = None
                node.model = None
                node.reasoning_effort = None
                node.last_heartbeat_at = None
            if event.runner:
                node.runner = event.runner
            if event.model:
                node.model = event.model
            if event.reasoning_effort:
                node.reasoning_effort = event.reasoning_effort
            if event.agent_role:
                node.agent_role = event.agent_role
            if event.task_id:
                node.task_id = event.task_id
            if event.attempt is not None:
                node.attempt = event.attempt
            if event.task_id:
                node.task_id = event.task_id
            if event.worker_id:
                node.worker_id = event.worker_id
            if event.provider_run_id:
                node.provider_run_id = event.provider_run_id
            if event.message:
                node.message = event.message
            if event.duration_ms is not None:
                node.duration_ms = event.duration_ms
            if event.event_type is AgentEventType.NODE_STARTED:
                node.status = "running"
                node.started_at = node.started_at or now
            elif event.event_type is AgentEventType.NODE_COMPLETED:
                node.status = "completed"
                node.finished_at = now
            elif event.event_type is AgentEventType.NODE_FAILED:
                node.status = "failed"
                node.finished_at = now
            elif event.status:
                node.status = event.status
            elif node.status == "pending":
                node.status = "running"
        return snapshot, node

    async def _persist(
        self,
        pipeline: Any,
        event: AgentEvent,
        snapshot: AgentRunSnapshot,
        node: AgentNodeSnapshot | None,
        *,
        persist_lifecycle_event: bool,
    ) -> None:
        """Queue one run update in the already-open Redis transaction."""

        run_id = event.run_id
        summary_key = self._key(self.RUN_KEY, run_id)
        nodes_key = self._key(self.NODES_KEY, run_id)
        events_key = self._key(self.EVENTS_KEY, run_id)
        sequence_key = self._key(self.SEQUENCE_KEY, run_id)
        summary = snapshot.model_copy(update={"nodes": []}).model_dump(mode="json")
        # Refresh the sequence TTL even for heartbeats.  Otherwise a long
        # active run could retain its summary through liveness updates while
        # losing its cursor and reusing old sequence numbers later.
        await self._pipeline_call(
            pipeline,
            "set",
            sequence_key,
            event.sequence,
            ex=self.retention_seconds,
        )
        if persist_lifecycle_event:
            event_json = json.dumps(event.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
            await self._pipeline_call(
                pipeline,
                "xadd",
                events_key,
                {"data": event_json},
                maxlen=self.max_events,
                approximate=False,
            )
        await self._pipeline_call(
            pipeline,
            "set",
            summary_key,
            json.dumps(summary, separators=(",", ":"), ensure_ascii=False),
            ex=self.retention_seconds,
        )
        timestamp = snapshot.updated_at.timestamp()
        await self._pipeline_call(pipeline, "zadd", self.RUNS_KEY, {run_id: timestamp})
        await self._pipeline_call(
            pipeline,
            "zremrangebyscore",
            self.RUNS_KEY,
            "-inf",
            timestamp - self.retention_seconds,
        )
        if persist_lifecycle_event:
            await self._pipeline_call(pipeline, "expire", events_key, self.retention_seconds)
        if node is not None:
            await self._pipeline_call(
                pipeline,
                "hset",
                nodes_key,
                node.node_id,
                json.dumps(node.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False),
            )
            await self._pipeline_call(pipeline, "expire", nodes_key, self.retention_seconds)

    async def _reset_pipeline(self, pipeline: Any) -> None:
        """Release a watched pipeline on retry or an early infrastructure error."""

        reset = getattr(pipeline, "reset", None)
        if reset is not None:
            await self._call(reset())

    async def get_runs(self, *, limit: int = 50) -> list[AgentRunSnapshot]:
        limit = max(1, min(int(limit), 100))
        try:
            try:
                members = await self._call(self.redis.zrange(self.RUNS_KEY, 0, limit - 1, desc=True))
            except (AttributeError, TypeError):
                members = await self._call(self.redis.zrevrange(self.RUNS_KEY, 0, limit - 1))
            result: list[AgentRunSnapshot] = []
            for member in members or []:
                run_id = str(_decode(member))
                snapshot = await self._load_snapshot(run_id)
                if snapshot is not None:
                    result.append(await self._with_nodes(snapshot))
            return result
        except AgentTelemetryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AgentTelemetryError("agent telemetry is unavailable") from exc

    async def _with_nodes(self, snapshot: AgentRunSnapshot) -> AgentRunSnapshot:
        nodes_key = self._key(self.NODES_KEY, snapshot.run_id)
        try:
            raw_nodes = await self._call(self.redis.hgetall(nodes_key))
            nodes: list[AgentNodeSnapshot] = []
            for raw in (raw_nodes or {}).values():
                nodes.append(AgentNodeSnapshot.model_validate(json.loads(_decode(raw))))
            nodes.sort(key=lambda item: item.node_id)
            return snapshot.model_copy(update={"nodes": nodes})
        except Exception as exc:  # noqa: BLE001
            raise AgentTelemetryError("agent telemetry is unavailable") from exc

    async def get_run(self, run_id: str) -> AgentRunSnapshot | None:
        clean_id = _safe_identifier(run_id, required=True)
        snapshot = await self._load_snapshot(clean_id or "")
        return None if snapshot is None else await self._with_nodes(snapshot)

    async def get_events(self, run_id: str, *, after: int = 0, limit: int = 200) -> list[AgentEvent]:
        clean_id = _safe_identifier(run_id, required=True)
        after = max(0, int(after))
        limit = max(1, min(int(limit), 200))
        key = self._key(self.EVENTS_KEY, clean_id or "")
        try:
            try:
                rows = await self._call(self.redis.xrange(key, min="-", max="+", count=self.max_events))
            except TypeError:
                rows = await self._call(self.redis.xrange(key, min="-", max="+"))
            events: list[AgentEvent] = []
            for _stream_id, fields in rows or []:
                fields = {_decode(k): _decode(v) for k, v in (fields or {}).items()}
                raw = fields.get("data")
                if raw is None:
                    continue
                event = AgentEvent.model_validate(json.loads(raw))
                if event.sequence > after:
                    events.append(event)
                    if len(events) >= limit:
                        break
            return events
        except AgentTelemetryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AgentTelemetryError("agent telemetry is unavailable") from exc


__all__ = [
    "AgentEvent",
    "AgentEventPage",
    "AgentEventType",
    "AgentNodeSnapshot",
    "AgentRunListResponse",
    "AgentRunSnapshot",
    "AgentTelemetryError",
    "AgentTelemetryStore",
]
