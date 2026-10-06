"""Settings-page overrides, one row per setting key (config.yaml path)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class AppSettingOverride(SQLModel, table=True):
    __tablename__ = "app_setting_overrides"

    key: str = Field(primary_key=True, max_length=120)
    value: Any = Field(sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
