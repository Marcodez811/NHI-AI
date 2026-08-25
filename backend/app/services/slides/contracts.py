"""Small shared contracts for the slides service implementation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class JobError(RuntimeError):
    """A generation failure tagged with a stable pipeline stage."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"[{stage}] {message}")


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
