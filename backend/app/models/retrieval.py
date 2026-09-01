"""Durable state for the application's primary retrieval index.

The remote vector store is owned by the OpenAI project configured for the
backend, while this row is the source of truth for which store the running
application should use.  Keeping the identifier in the database is important
because the API and document workers are separate processes and do not share
runtime memory (or a writable environment file).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RetrievalIndexState(StrEnum):
    """Lifecycle states for the local retrieval-index record."""

    UNINITIALIZED = "uninitialized"
    PROVISIONING = "provisioning"
    READY = "ready"
    ERROR = "error"


class RetrievalIndexErrorCode(StrEnum):
    """Safe, stable error codes exposed to API/UI callers."""

    INVALID_SEED = "invalid_seed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    STORE_MISSING_OR_EXPIRED = "store_missing_or_expired"


class RetrievalIndex(SQLModel, table=True):
    """The singleton primary retrieval index.

    ``key`` is deliberately a fixed logical key instead of an auto-generated
    row ID.  A uniqueness constraint at the database boundary gives separate
    API/worker processes a common coordination point while provisioning.
    """

    __tablename__ = "retrieval_indexes"

    PRIMARY_KEY: ClassVar[str] = "primary"

    key: str = Field(default=PRIMARY_KEY, primary_key=True, max_length=64)
    installation_id: str = Field(default_factory=lambda: str(uuid4()), max_length=64, index=True)
    provider: str = Field(default="openai", max_length=32)
    vector_store_id: str | None = Field(default=None, max_length=255, index=True)
    state: str = Field(default=RetrievalIndexState.UNINITIALIZED.value, max_length=32, index=True)
    error_code: str | None = Field(default=None, max_length=64)
    error_detail: str | None = Field(default=None, max_length=512)
    warning_code: str | None = Field(default=None, max_length=64)
    lease_token: str | None = Field(default=None, max_length=64)
    lease_expires_at: datetime | None = Field(default=None)
    attempts: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    last_verified_at: datetime | None = Field(default=None)


class RetrievalIndexRead(BaseModel):
    """Provider-safe representation for status endpoints."""

    model_config = ConfigDict(from_attributes=True)

    state: RetrievalIndexState
    can_retrieve: bool
    error_code: RetrievalIndexErrorCode | None = None
    warning_code: str | None = None
