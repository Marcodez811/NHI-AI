"""Bind a job's immutable model selection to its asynchronous workflow context."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_settings import StageModelSettings


active_stage_settings: ContextVar[dict[str, StageModelSettings] | None] = ContextVar(
    "active_stage_settings", default=None
)


def stage_settings(stage: str) -> StageModelSettings | None:
    """Return the current job's selection without sharing it across workers."""

    snapshot = active_stage_settings.get()
    return snapshot.get(stage) if snapshot is not None else None
