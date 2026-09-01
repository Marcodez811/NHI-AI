"""Provision and validate the application's OpenAI vector store.

The registry intentionally keeps remote calls outside database transactions.
It first claims a short-lived lease in the singleton row, performs the
provider operation, and then commits the result.  That coordination works
across the API and document-worker processes and prevents duplicate stores
when they start together.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from openai import OpenAI
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.config import Settings, settings
from app.db import engine
from app.models.retrieval import (
    RetrievalIndex,
    RetrievalIndexErrorCode,
    RetrievalIndexState,
)

logger = logging.getLogger(__name__)


class RetrievalProviderError(RuntimeError):
    """A provider call failed with a safe, stable application error code."""

    def __init__(self, code: RetrievalIndexErrorCode, message: str = "") -> None:
        self.code = code
        super().__init__(message or code.value)


class VectorStoreClient(Protocol):
    """Small subset of the OpenAI client used by this registry."""

    vector_stores: Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive datetime round trips for comparison."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _provider_error(exc: Exception, *, seed: bool = False) -> RetrievalProviderError:
    """Convert SDK/network errors into codes safe to persist and expose."""

    if seed and _status_code(exc) in {400, 401, 403, 404}:
        return RetrievalProviderError(RetrievalIndexErrorCode.INVALID_SEED)
    if _status_code(exc) in {401, 403, 404}:
        return RetrievalProviderError(RetrievalIndexErrorCode.STORE_MISSING_OR_EXPIRED)
    return RetrievalProviderError(RetrievalIndexErrorCode.PROVIDER_UNAVAILABLE)


def _object_value(value: Any, key: str, default: Any = None) -> Any:
    """Read both SDK model objects and dict-shaped test doubles."""

    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


class RetrievalIndexRegistry:
    """Database-backed registry for one primary OpenAI vector store.

    ``session_factory`` and ``client_factory`` are injectable to keep startup
    and provider behavior deterministic in tests.  The default factories are
    intentionally lazy, so importing this module does not create a network
    client or open a database connection.
    """

    _process_lock = threading.RLock()

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        client_factory: Callable[[], VectorStoreClient] | None = None,
        app_settings: Settings | Any | None = None,
        now: Callable[[], datetime] = _utcnow,
        lease_seconds: float = 30.0,
    ) -> None:
        self._session_factory = session_factory or (lambda: Session(engine))
        self._client_factory = client_factory or self._default_client
        self._settings = app_settings or settings
        self._now = now
        self._lease_seconds = max(1.0, float(lease_seconds))

    @property
    def _bootstrap_timeout(self) -> float:
        return float(getattr(self._settings, "openai_vector_store_bootstrap_timeout_seconds", 10.0))

    @property
    def _store_name(self) -> str:
        return str(getattr(self._settings, "openai_vector_store_name", "NHI-AI Knowledge Base"))

    def _default_client(self) -> VectorStoreClient:
        api_key = getattr(self._settings, "openai_api_key", None)
        if not api_key:
            raise RetrievalProviderError(RetrievalIndexErrorCode.PROVIDER_NOT_CONFIGURED)
        return OpenAI(api_key=api_key, timeout=self._bootstrap_timeout)

    def get_record(self) -> RetrievalIndex | None:
        with self._session_factory() as session:
            return session.get(RetrievalIndex, RetrievalIndex.PRIMARY_KEY)

    def get_ready_id(self) -> str | None:
        """Return the persisted ID without making a provider call."""

        record = self.get_record()
        if record is None or record.state != RetrievalIndexState.READY.value:
            return None
        return record.vector_store_id

    def validate_ready(self) -> RetrievalIndex:
        """Re-check the persisted remote store and refresh its status.

        Startup uses this explicit path so provider access is validated once,
        while request paths can continue using ``get_ready_id`` without an
        OpenAI round trip for every chat or upload.
        """

        return self.ensure_ready(force=True)

    def status(self) -> dict[str, Any]:
        """Return a provider-safe status snapshot for API and UI callers."""

        record = self.get_record()
        if record is None:
            return {
                "state": RetrievalIndexState.UNINITIALIZED.value,
                "can_retrieve": False,
                "error_code": None,
                "warning_code": None,
            }
        return {
            "state": record.state,
            "can_retrieve": record.state == RetrievalIndexState.READY.value and bool(record.vector_store_id),
            "error_code": record.error_code,
            "warning_code": record.warning_code,
        }

    def ensure_ready(self, *, force: bool = False) -> RetrievalIndex:
        """Adopt, create, or validate the primary vector store.

        A concurrent caller that observes another live lease gets the current
        provisioning row.  The caller that owns the lease performs the remote
        operation and returns a ready/error row after persisting its result.
        """

        # This lock avoids SQLite's coarse write-lock contention for concurrent
        # callers in one process.  The persisted lease remains the authority
        # across separate processes.
        with self._process_lock:
            record, owner = self._claim(force=force)
            if not owner:
                return record

            try:
                record = self._provision(record)
            except RetrievalProviderError as exc:
                record = self._mark_error(record.key, exc.code, str(exc) or None)
            except Exception as exc:  # defensive boundary around SDK changes
                logger.exception("retrieval index bootstrap failed")
                safe = _provider_error(exc)
                record = self._mark_error(record.key, safe.code, None)
            return record

    def _claim(self, *, force: bool) -> tuple[RetrievalIndex, bool]:
        now = self._now()
        lease_expires_at = now + timedelta(seconds=self._lease_seconds)
        token = str(uuid4())

        try:
            with self._session_factory() as session:
                # PostgreSQL serializes claims for the singleton row.  SQLite
                # ignores FOR UPDATE, so the process lock plus the unique-key
                # retry below still provide safe local behavior.
                record = session.get(
                    RetrievalIndex,
                    RetrievalIndex.PRIMARY_KEY,
                    with_for_update=True,
                )
                if record is None:
                    record = RetrievalIndex(
                        key=RetrievalIndex.PRIMARY_KEY,
                        state=RetrievalIndexState.PROVISIONING.value,
                        lease_token=token,
                        lease_expires_at=lease_expires_at,
                        attempts=1,
                    )
                    session.add(record)
                    session.commit()
                    session.refresh(record)
                    return record, True

                if (
                    not force
                    and record.state == RetrievalIndexState.PROVISIONING.value
                    and record.lease_expires_at is not None
                    and _as_utc(record.lease_expires_at) > _as_utc(now)
                ):
                    return record, False

                if (
                    not force
                    and record.state == RetrievalIndexState.READY.value
                    and record.vector_store_id
                ):
                    # Existing ready IDs are validated by the owner on startup
                    # only when a caller explicitly asks for force.  Normal
                    # request paths can use the cheap database lookup.
                    configured_id = self._configured_id()
                    expected_warning = (
                        "environment_id_mismatch"
                        if configured_id and configured_id != record.vector_store_id
                        else None
                    )
                    if record.warning_code != expected_warning:
                        record.warning_code = expected_warning
                        record.updated_at = now
                        session.add(record)
                        session.commit()
                        session.refresh(record)
                    return record, False

                record.state = RetrievalIndexState.PROVISIONING.value
                record.lease_token = token
                record.lease_expires_at = lease_expires_at
                record.error_code = None
                record.error_detail = None
                record.attempts += 1
                record.updated_at = now
                session.add(record)
                session.commit()
                session.refresh(record)
                return record, True
        except IntegrityError:
            # A separate process inserted the singleton between our read and
            # commit.  The remote operation belongs to that process.
            with self._session_factory() as session:
                record = session.get(RetrievalIndex, RetrievalIndex.PRIMARY_KEY)
                if record is None:
                    raise
                return record, False

    def _provision(self, record: RetrievalIndex) -> RetrievalIndex:
        client = self._client_factory()
        configured_id = self._configured_id()

        # Once a store has been adopted/created, the database ID remains
        # authoritative.  A later environment change must not silently move
        # documents to a different corpus, even when a caller forces a
        # validation retry.
        if record.vector_store_id:
            try:
                remote = client.vector_stores.retrieve(
                    record.vector_store_id,
                    timeout=self._bootstrap_timeout,
                )
            except Exception as exc:
                raise _provider_error(exc) from exc
            if self._is_expired(remote):
                raise RetrievalProviderError(RetrievalIndexErrorCode.STORE_MISSING_OR_EXPIRED)
            warning = (
                "environment_id_mismatch"
                if configured_id and configured_id != record.vector_store_id
                else None
            )
            return self._mark_ready(record.key, record.vector_store_id, warning=warning)

        if configured_id:
            try:
                remote = client.vector_stores.retrieve(
                    configured_id,
                    timeout=self._bootstrap_timeout,
                )
            except Exception as exc:
                raise _provider_error(exc, seed=True) from exc
            if self._is_expired(remote):
                raise RetrievalProviderError(RetrievalIndexErrorCode.INVALID_SEED)
            return self._mark_ready(record.key, configured_id, warning=None)

        # A crashed creator can leave a remote store behind after the database
        # transaction.  Search by installation metadata before creating a new
        # store, so retrying does not keep leaking orphaned stores.
        recovered = self._find_matching_store(client, record.installation_id)
        if recovered:
            return self._mark_ready(record.key, recovered, warning=None)

        try:
            created = client.vector_stores.create(
                name=self._store_name,
                description="Primary NHI-AI knowledge base for grounded answers.",
                metadata={
                    "application": "nhi-ai",
                    "index_key": record.key,
                    "installation_id": record.installation_id,
                },
                timeout=self._bootstrap_timeout,
            )
        except Exception as exc:
            raise _provider_error(exc) from exc
        vector_store_id = _object_value(created, "id")
        if not vector_store_id:
            raise RetrievalProviderError(RetrievalIndexErrorCode.PROVIDER_UNAVAILABLE)
        return self._mark_ready(record.key, str(vector_store_id), warning=None)

    def _configured_id(self) -> str | None:
        value = getattr(self._settings, "openai_vector_store_id", None)
        return str(value).strip() if value and str(value).strip() else None

    @staticmethod
    def _is_expired(remote: Any) -> bool:
        return str(_object_value(remote, "status", "")).lower() == "expired"

    def _find_matching_store(self, client: VectorStoreClient, installation_id: str) -> str | None:
        list_method = getattr(getattr(client, "vector_stores", None), "list", None)
        if not callable(list_method):
            return None
        try:
            stores = list_method(limit=100, order="asc", timeout=self._bootstrap_timeout)
            for store in self._iter_page(stores):
                metadata = _object_value(store, "metadata", {}) or {}
                if isinstance(metadata, dict) and metadata.get("installation_id") == installation_id:
                    vector_store_id = _object_value(store, "id")
                    if vector_store_id and not self._is_expired(store):
                        return str(vector_store_id)
        except Exception:
            # Listing is recovery best-effort.  A list permission/network
            # failure should not prevent normal creation from being attempted.
            logger.debug("retrieval index recovery lookup failed", exc_info=True)
        return None

    @staticmethod
    def _iter_page(page: Any) -> Iterable[Any]:
        if page is None:
            return ()
        if isinstance(page, dict):
            return page.get("data", ())
        data = getattr(page, "data", None)
        if data is not None:
            return data
        try:
            return iter(page)
        except TypeError:
            return ()

    def _mark_ready(self, key: str, vector_store_id: str, *, warning: str | None) -> RetrievalIndex:
        now = self._now()
        with self._session_factory() as session:
            record = session.get(RetrievalIndex, key)
            if record is None:
                raise RuntimeError("Retrieval index record disappeared during provisioning.")
            record.vector_store_id = vector_store_id
            record.state = RetrievalIndexState.READY.value
            record.error_code = None
            record.error_detail = None
            record.warning_code = warning
            record.lease_token = None
            record.lease_expires_at = None
            record.last_verified_at = now
            record.updated_at = now
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def _mark_error(
        self,
        key: str,
        code: RetrievalIndexErrorCode,
        detail: str | None,
    ) -> RetrievalIndex:
        now = self._now()
        with self._session_factory() as session:
            record = session.get(RetrievalIndex, key)
            if record is None:
                raise RuntimeError("Retrieval index record disappeared during provisioning.")
            record.state = RetrievalIndexState.ERROR.value
            record.error_code = code.value
            # Never persist SDK exception text: it can contain API URLs,
            # request IDs, or credentials supplied through error messages.
            record.error_detail = detail if detail in {None, ""} else None
            record.lease_token = None
            record.lease_expires_at = None
            record.updated_at = now
            session.add(record)
            session.commit()
            session.refresh(record)
            return record


# Names used by callers that think in terms of a provider resource rather than
# the local domain object.  Keeping aliases avoids coupling API/worker code to
# one naming convention.
RetrievalIndexService = RetrievalIndexRegistry
VectorStoreRegistry = RetrievalIndexRegistry
