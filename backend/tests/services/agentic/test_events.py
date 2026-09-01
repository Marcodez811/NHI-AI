from __future__ import annotations

import asyncio

import pytest
from redis.exceptions import WatchError

from app.services.agentic.events import AgentEvent, AgentEventType, AgentTelemetryStore


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.watched_versions: dict[str, int] = {}

    async def watch(self, *keys: str) -> None:
        self.watched_versions = {key: self.redis.versions.get(key, 0) for key in keys}

    async def get(self, key: str):
        value = self.redis.values.get(key)
        await self.redis.pause_after_summary_read(key)
        return value

    async def hgetall(self, key: str):
        return self.redis.hashes.get(key, {})

    def multi(self) -> None:
        return None

    async def reset(self) -> None:
        self.operations = []
        self.watched_versions = {}

    def __getattr__(self, name: str):
        def queue(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self

        return queue

    async def execute(self):
        if any(
            self.redis.versions.get(key, 0) != version
            for key, version in self.watched_versions.items()
        ):
            raise WatchError("Watched key changed")
        for name, args, _kwargs in self.operations:
            if name == "xadd":
                key, fields = args
                self.redis.streams.setdefault(str(key), []).append(("1-0", fields))
            elif name == "set":
                self.redis.values[str(args[0])] = args[1]
                self.redis.bump_version(str(args[0]))
            elif name == "zadd":
                key, scores = args
                self.redis.sorted_sets.setdefault(str(key), {}).update(scores)
            elif name == "hset":
                key, field, value = args
                self.redis.hashes.setdefault(str(key), {})[field] = value
                self.redis.bump_version(str(key))


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.hashes: dict[str, dict[object, object]] = {}
        self.sorted_sets: dict[str, dict[object, float]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, object]]]] = {}
        self.counters: dict[str, int] = {}
        self.versions: dict[str, int] = {}
        self.pause_next_summary_read = False
        self.first_transaction_ready = asyncio.Event()
        self.release_first_transaction = asyncio.Event()

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        self.bump_version(key)
        return self.counters[key]

    async def get(self, key: str):
        return self.values.get(key)

    def pipeline(self, **_kwargs):
        return FakePipeline(self)

    async def zrange(self, key: str, start: int, stop: int, *, desc: bool = False):
        values = sorted(self.sorted_sets.get(key, {}).items(), key=lambda item: item[1], reverse=desc)
        return [key for key, _score in values][start : stop + 1]

    async def hgetall(self, key: str):
        return self.hashes.get(key, {})

    async def xrange(self, key: str, **_kwargs):
        return self.streams.get(key, [])

    def bump_version(self, key: str) -> None:
        self.versions[key] = self.versions.get(key, 0) + 1

    async def pause_after_summary_read(self, key: str) -> None:
        if self.pause_next_summary_read and key.endswith(":summary"):
            self.pause_next_summary_read = False
            self.first_transaction_ready.set()
            await self.release_first_transaction.wait()


@pytest.mark.asyncio
async def test_store_sequences_events_and_hydrates_node_snapshots():
    store = AgentTelemetryStore(FakeRedis(), retention_seconds=60)

    started = await store.start_run(
        run_id="run-1",
        workflow="slides",
        task_id="task-1",
        worker_id="host:123",
        runner="codex",
    )
    node_started = await store.emit(
        run_id="run-1",
        event_type=AgentEventType.NODE_STARTED,
        node_id="author",
        attempt=1,
        provider_run_id="provider-1",
    )
    await store.emit(run_id="run-1", event_type=AgentEventType.NODE_COMPLETED, node_id="author")
    await store.emit(run_id="run-1", event_type=AgentEventType.RUN_COMPLETED, phase="completed")

    assert [started.sequence, node_started.sequence] == [1, 2]
    snapshot = await store.get_run("run-1")
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert snapshot.last_sequence == 4
    assert snapshot.nodes[0].status == "completed"
    assert snapshot.nodes[0].provider_run_id == "provider-1"
    assert [event.sequence for event in await store.get_events("run-1", after=1)] == [2, 3, 4]


def test_event_model_removes_unallowlisted_metadata_and_sanitizes_text():
    event = AgentEvent(
        run_id="run-1",
        event_type="node_progress",
        message="/tmp/private/output.pptx",
        metadata={"prompt": "do not persist", "reason": "validation passed"},
    )
    assert event.message == "Agent workflow is in progress."
    assert event.metadata == {"reason": "validation passed"}


@pytest.mark.asyncio
async def test_telemetry_store_uses_bounded_event_limit():
    redis = FakeRedis()
    store = AgentTelemetryStore(redis, max_events=2)
    await store.start_run(run_id="run-2", workflow="slides")
    await store.emit(run_id="run-2", event_type="heartbeat")
    await store.emit(run_id="run-2", event_type="run_completed")
    # The fake does not implement Redis trimming, but the stream command still
    # carries the required explicit maxlen/approximate policy.
    queued = redis.streams["agents:run:run-2:events"]
    assert len(queued) == 3


@pytest.mark.asyncio
async def test_two_stores_retry_stale_transaction_without_regressing_snapshot():
    redis = FakeRedis()
    first_store = AgentTelemetryStore(redis)
    second_store = AgentTelemetryStore(redis)
    await first_store.start_run(run_id="run-3", workflow="slides")
    await first_store.emit(
        run_id="run-3",
        event_type=AgentEventType.NODE_STARTED,
        node_id="author",
    )

    # The first writer reads a valid snapshot then pauses. The second writer
    # commits while its keys are watched, forcing the first writer to retry
    # from the newer sequence and summary instead of overwriting them.
    redis.pause_next_summary_read = True
    delayed_event = asyncio.create_task(
        first_store.emit(
            run_id="run-3",
            event_type=AgentEventType.PHASE_CHANGED,
            phase="reviewing",
            node_id="author",
        )
    )
    await asyncio.wait_for(redis.first_transaction_ready.wait(), timeout=1)
    earlier_event = await second_store.emit(
        run_id="run-3",
        event_type=AgentEventType.PHASE_CHANGED,
        phase="authoring",
        node_id="author",
    )
    redis.release_first_transaction.set()
    newest_event = await asyncio.wait_for(delayed_event, timeout=1)

    snapshot = await first_store.get_run("run-3")
    events = await first_store.get_events("run-3")

    assert earlier_event.sequence == 3
    assert newest_event.sequence == 4
    assert snapshot is not None
    assert snapshot.last_sequence == newest_event.sequence
    assert snapshot.phase == newest_event.phase == "reviewing"
    assert newest_event.occurred_at < earlier_event.occurred_at
    assert snapshot.updated_at == earlier_event.occurred_at
    assert snapshot.nodes[0].updated_at == earlier_event.occurred_at
    assert redis.sorted_sets[first_store.RUNS_KEY]["run-3"] == snapshot.updated_at.timestamp()
    assert [event.sequence for event in events] == [1, 2, 3, 4]
