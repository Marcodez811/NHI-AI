from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings
from app.models.retrieval import RetrievalIndex, RetrievalIndexState
from app.services.retrieval.registry import RetrievalIndexRegistry


class FakeVectorStores:
    def __init__(self, *, retrieved=None, created_id="vs-created", list_items=None):
        self.retrieved = retrieved
        self.created_id = created_id
        self.list_items = list_items or []
        self.retrieve_calls: list[tuple[str, dict]] = []
        self.create_calls: list[dict] = []
        self.list_calls: list[dict] = []

    def retrieve(self, vector_store_id, **kwargs):
        self.retrieve_calls.append((vector_store_id, kwargs))
        if isinstance(self.retrieved, Exception):
            raise self.retrieved
        return self.retrieved or SimpleNamespace(id=vector_store_id, status="completed")

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(id=self.created_id, status="completed")

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return SimpleNamespace(data=self.list_items)


def _settings(**kwargs):
    values = {
        "redis_url": "redis://localhost",
        "openai_api_key": "test-key",
        "openai_vector_store_id": None,
        "openai_vector_store_name": "NHI-AI Knowledge Base",
        "openai_vector_store_bootstrap_timeout_seconds": 10.0,
        "database_url": "sqlite://",
    }
    values.update(kwargs)
    return Settings(_env_file=None, **values)


@pytest.fixture
def database():
    db = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(db)
    return db


def _factory(db):
    return lambda: Session(db)


def test_bootstrap_creates_empty_store_and_persists_singleton(database):
    vector_stores = FakeVectorStores()
    client = SimpleNamespace(vector_stores=vector_stores)
    registry = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: client,
        app_settings=_settings(),
    )

    record = registry.ensure_ready()

    assert record.state == RetrievalIndexState.READY.value
    assert record.vector_store_id == "vs-created"
    assert len(vector_stores.create_calls) == 1
    kwargs = vector_stores.create_calls[0]
    assert kwargs["name"] == "NHI-AI Knowledge Base"
    assert "expires_after" not in kwargs
    assert kwargs["metadata"]["application"] == "nhi-ai"
    with Session(database) as session:
        persisted = session.get(RetrievalIndex, RetrievalIndex.PRIMARY_KEY)
    assert persisted is not None
    assert persisted.vector_store_id == "vs-created"
    assert registry.get_ready_id() == "vs-created"


def test_valid_env_store_is_adopted_without_creating(database):
    vector_stores = FakeVectorStores()
    registry = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=vector_stores),
        app_settings=_settings(openai_vector_store_id="vs-existing"),
    )

    record = registry.ensure_ready()

    assert record.state == RetrievalIndexState.READY.value
    assert record.vector_store_id == "vs-existing"
    assert vector_stores.retrieve_calls[0][0] == "vs-existing"
    assert vector_stores.create_calls == []


def test_invalid_env_store_is_safe_error(database):
    class NotFoundError(Exception):
        status_code = 404

    vector_stores = FakeVectorStores(retrieved=NotFoundError("private provider details"))
    registry = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=vector_stores),
        app_settings=_settings(openai_vector_store_id="vs-foreign"),
    )

    record = registry.ensure_ready()

    assert record.state == RetrievalIndexState.ERROR.value
    assert record.error_code == "invalid_seed"
    assert record.error_detail is None
    assert registry.status() == {
        "state": "error",
        "can_retrieve": False,
        "error_code": "invalid_seed",
        "warning_code": None,
    }


def test_existing_store_is_not_switched_when_env_changes(database):
    vector_stores = FakeVectorStores()
    first = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=vector_stores),
        app_settings=_settings(),
    )
    assert first.ensure_ready().vector_store_id == "vs-created"

    second_stores = FakeVectorStores()
    second = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=second_stores),
        app_settings=_settings(openai_vector_store_id="vs-new-seed"),
    )
    record = second.ensure_ready()

    # The persisted database ID remains authoritative.  Environment changes
    # are surfaced as a warning by the integration layer before an explicit
    # migration; this registry does not silently orphan existing documents.
    assert record.vector_store_id == "vs-created"
    assert record.warning_code == "environment_id_mismatch"
    assert second_stores.create_calls == []
    assert second_stores.retrieve_calls == []


def test_validate_ready_rechecks_persisted_id_without_switching(database):
    first_stores = FakeVectorStores()
    first = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=first_stores),
        app_settings=_settings(),
    )
    assert first.ensure_ready().vector_store_id == "vs-created"

    second_stores = FakeVectorStores()
    second = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=second_stores),
        app_settings=_settings(openai_vector_store_id="vs-new-seed"),
    )
    record = second.validate_ready()

    assert record.state == RetrievalIndexState.READY.value
    assert record.vector_store_id == "vs-created"
    assert record.warning_code == "environment_id_mismatch"
    assert [call[0] for call in second_stores.retrieve_calls] == ["vs-created"]
    assert second_stores.create_calls == []


def test_expired_env_store_is_rejected(database):
    vector_stores = FakeVectorStores(retrieved=SimpleNamespace(status="expired"))
    registry = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=vector_stores),
        app_settings=_settings(openai_vector_store_id="vs-expired"),
    )

    record = registry.ensure_ready()

    assert record.state == RetrievalIndexState.ERROR.value
    assert record.error_code == "invalid_seed"


def test_stale_lease_recovers_remote_store_by_metadata(database):
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        session.add(
            RetrievalIndex(
                state=RetrievalIndexState.PROVISIONING.value,
                installation_id="install-1",
                lease_token="dead",
                lease_expires_at=now - timedelta(minutes=1),
            )
        )
        session.commit()
    vector_stores = FakeVectorStores(
        list_items=[
            SimpleNamespace(
                id="vs-recovered",
                status="completed",
                metadata={"application": "nhi-ai", "installation_id": "install-1"},
            )
        ]
    )
    registry = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=vector_stores),
        app_settings=_settings(),
    )

    record = registry.ensure_ready()

    assert record.vector_store_id == "vs-recovered"
    assert vector_stores.create_calls == []


def test_live_provisioning_lease_is_not_claimed_again(database):
    vector_stores = FakeVectorStores()
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        session.add(
            RetrievalIndex(
                state=RetrievalIndexState.PROVISIONING.value,
                installation_id="install-1",
                lease_token="live",
                lease_expires_at=now + timedelta(minutes=1),
            )
        )
        session.commit()
    registry = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=vector_stores),
        app_settings=_settings(),
    )

    record = registry.ensure_ready()

    assert record.state == RetrievalIndexState.PROVISIONING.value
    assert vector_stores.create_calls == []


def test_concurrent_registries_create_only_one_remote_store(database):
    vector_stores = FakeVectorStores()
    client = SimpleNamespace(vector_stores=vector_stores)
    registries = [
        RetrievalIndexRegistry(
            session_factory=_factory(database),
            client_factory=lambda: client,
            app_settings=_settings(),
        )
        for _ in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda registry: registry.ensure_ready(), registries))

    assert {record.vector_store_id for record in records} == {"vs-created"}
    assert len(vector_stores.create_calls) == 1


def test_provider_failure_is_sanitized_and_retry_can_succeed(database):
    class ServerError(Exception):
        status_code = 500

    vector_stores = FakeVectorStores()
    calls = {"count": 0}

    def create(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ServerError("https://api.openai.com/v1 leaked request details")
        return SimpleNamespace(id="vs-retry")

    vector_stores.create = create
    registry = RetrievalIndexRegistry(
        session_factory=_factory(database),
        client_factory=lambda: SimpleNamespace(vector_stores=vector_stores),
        app_settings=_settings(),
    )

    failed = registry.ensure_ready()
    assert failed.state == RetrievalIndexState.ERROR.value
    assert failed.error_code == "provider_unavailable"
    assert failed.error_detail is None

    succeeded = registry.ensure_ready()
    assert succeeded.state == RetrievalIndexState.READY.value
    assert succeeded.vector_store_id == "vs-retry"
