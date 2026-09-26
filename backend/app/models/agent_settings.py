"""Persisted operator choices for each agent workflow stage."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class AgentStageSettings(SQLModel, table=True):
    """One row per (workflow, stage); NULL values defer to environment policy."""

    __tablename__ = "agent_stage_settings"

    workflow: str = Field(primary_key=True, max_length=32)
    stage: str = Field(primary_key=True, max_length=32)
    runner: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=200)
    reasoning_effort: str | None = Field(default=None, max_length=16)
    planner_enabled: bool | None = Field(default=None)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
    )
