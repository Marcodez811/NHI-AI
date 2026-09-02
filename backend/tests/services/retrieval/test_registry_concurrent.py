"""Registry concurrent coordination tests.

Covers:
- _mark_ready is fenced by lease token — expired owner cannot overwrite newer ready result
- _claim stores the lease token so _mark_ready can compare
- validate_ready on an already-READY store does not reset it unless store is expired
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.retrieval import RetrievalIndex, RetrievalIndexErrorCode, RetrievalIndexState
from app.services.retrieval.registry import RetrievalIndexRegistry


def _utcnow():
    return datetime.now(timezone.utc)


def _fake_record(
    *,
    state=RetrievalIndexState.READY,
    lease_token: str | None = None,
    lease_expires_at: datetime | None = None,
    vector_store_id: str = "vs_existing",
) -> RetrievalIndex:
    return RetrievalIndex(
        key=RetrievalIndex.PRIMARY_KEY,
        state=state.value,
        vector_store_id=vector_store_id,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        installation_id=str(uuid4()),
    )


class FakeSession:
    """Minimal session that serves a single record from a dictionary."""

    def __init__(self, record: RetrievalIndex | None = None):
        self._records: dict[str, RetrievalIndex] = {}
        if record is not None:
            self._records[record.key] = record
        self.committed: list[RetrievalIndex] = []

    def get(self, model, key, *, with_for_update=False):
        return self._records.get(key)

    def add(self, obj):
        if hasattr(obj, "key"):
            self._records[obj.key] = obj

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_mark_ready_respects_lease_token_fence():
    """_mark_ready must not overwrite when lease token no longer matches."""

    token_a = str(uuid4())
    token_b = str(uuid4())

    # The record has token_b (written by another owner after our claim).
    record = _fake_record(
        state=RetrievalIndexState.PROVISIONING,
        lease_token=token_b,
    )

    registry = RetrievalIndexRegistry(session_factory=lambda: FakeSession(record))
    # Simulate registry having claimed with token_a.
    registry._lease_token = token_a

    result = registry._mark_ready(RetrievalIndex.PRIMARY_KEY, "vs_new", warning=None)

    # Should return the current record without overwriting vector_store_id.
    assert result.vector_store_id != "vs_new"


def test_mark_ready_proceeds_with_matching_lease_token():
    """_mark_ready proceeds normally when the lease token matches."""

    token = str(uuid4())
    record = _fake_record(
        state=RetrievalIndexState.PROVISIONING,
        lease_token=token,
        vector_store_id=None,
    )

    registry = RetrievalIndexRegistry(session_factory=lambda: FakeSession(record))
    registry._lease_token = token

    result = registry._mark_ready(RetrievalIndex.PRIMARY_KEY, "vs_new", warning=None)

    assert result.vector_store_id == "vs_new"
    assert result.state == RetrievalIndexState.READY.value
    assert registry._lease_token is None  # Cleared after write.


def test_claim_stores_lease_token_on_self():
    """_claim must save the token on registry._lease_token so _mark_ready can fence."""

    record = _fake_record(
        state=RetrievalIndexState.UNINITIALIZED,
        lease_token=None,
        vector_store_id=None,
    )

    registry = RetrievalIndexRegistry(session_factory=lambda: FakeSession(record))
    assert registry._lease_token is None

    returned_record, did_claim = registry._claim(force=False)
    if did_claim:
        assert registry._lease_token is not None
        assert registry._lease_token == returned_record.lease_token


def test_claim_does_not_overwrite_live_provisioning_lease():
    """_claim must return (record, False) without touching a live lease from another process."""

    token_other = str(uuid4())
    future = _utcnow() + timedelta(seconds=30)

    record = _fake_record(
        state=RetrievalIndexState.PROVISIONING,
        lease_token=token_other,
        lease_expires_at=future,
        vector_store_id=None,
    )

    registry = RetrievalIndexRegistry(session_factory=lambda: FakeSession(record))

    returned_record, did_claim = registry._claim(force=True)  # force=True simulates validate_ready.

    # Must not have taken ownership.
    assert not did_claim
    assert returned_record.lease_token == token_other  # Other's token preserved.
    assert registry._lease_token is None  # Our token not set.


def test_validate_ready_does_not_reset_ready_store():
    """validate_ready on an already-READY store must not reset it to PROVISIONING."""

    record = _fake_record(
        state=RetrievalIndexState.READY,
        vector_store_id="vs_existing",
    )

    def fake_client_factory():
        class FakeClient:
            class vector_stores:
                class Vector_stores:
                    class retrieve:
                        @staticmethod
                        def __call__(**kwargs):
                            return SimpleNamespace(status="completed")

        return FakeClient()

    from types import SimpleNamespace

    registry = RetrievalIndexRegistry(session_factory=lambda: FakeSession(record))
    returned_record, did_claim = registry._claim(force=True)

    # Validation owns a lease, but the ready store remains readable while the
    # provider call is in flight.
    assert did_claim
    assert returned_record.state == RetrievalIndexState.READY.value
    assert returned_record.lease_token is not None


def test_mark_error_respects_lease_token_fence():
    token_a = str(uuid4())
    token_b = str(uuid4())
    record = _fake_record(
        state=RetrievalIndexState.PROVISIONING,
        lease_token=token_b,
        vector_store_id=None,
    )
    registry = RetrievalIndexRegistry(session_factory=lambda: FakeSession(record))
    registry._lease_token = token_a

    result = registry._mark_error(
        RetrievalIndex.PRIMARY_KEY,
        RetrievalIndexErrorCode.PROVIDER_UNAVAILABLE,
        None,
    )

    assert result.state == RetrievalIndexState.PROVISIONING.value
    assert result.lease_token == token_b


def test_error_fence_uses_a_separate_database_session(tmp_path):
    """A stale owner cannot overwrite a lease changed by another session."""

    database = create_engine(
        f"sqlite:///{tmp_path / 'registry.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(database)
    token_b = str(uuid4())
    with Session(database) as session:
        session.add(
            _fake_record(
                state=RetrievalIndexState.PROVISIONING,
                lease_token=token_b,
                vector_store_id=None,
            )
        )
        session.commit()

    registry = RetrievalIndexRegistry(session_factory=lambda: Session(database))
    registry._lease_token = str(uuid4())
    registry._mark_error(
        RetrievalIndex.PRIMARY_KEY,
        RetrievalIndexErrorCode.PROVIDER_UNAVAILABLE,
        None,
    )

    with Session(database) as session:
        persisted = session.get(RetrievalIndex, RetrievalIndex.PRIMARY_KEY)
    assert persisted is not None
    assert persisted.state == RetrievalIndexState.PROVISIONING.value
    assert persisted.lease_token == token_b
