"""Explicit allowlist for agentic workflows."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .contracts import WorkflowAdapter


class WorkflowRegistryError(ValueError):
    """Base class for deterministic registry validation failures."""


class UnknownWorkflowError(WorkflowRegistryError):
    """Raised when a task names a workflow that is not registered."""


class WorkflowRegistry:
    """A name-to-adapter allowlist; it never imports a requested module."""

    def __init__(self, workflows: dict[str, WorkflowAdapter[Any, Any]] | None = None):
        self._workflows: dict[str, WorkflowAdapter[Any, Any]] = {}
        for name, adapter in (workflows or {}).items():
            self.register(name, adapter)

    def _ensure_builtins(self) -> None:
        """Load built-in adapters lazily on the process-wide registry only."""

        global _bootstrapping
        if self is not globals().get("default_registry") or _bootstrapping or "slides" in self._workflows:
            return
        _bootstrapping = True
        try:
            from app.services.slides.adapter import slides_adapter

            if "slides" not in self._workflows:
                self.register("slides", slides_adapter)
        finally:
            _bootstrapping = False

    def register(self, name: str, adapter: WorkflowAdapter[Any, Any]) -> WorkflowAdapter[Any, Any]:
        if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise WorkflowRegistryError("workflow names must be simple names")
        adapter_name = getattr(adapter, "name", name) or name
        if adapter_name != name:
            raise WorkflowRegistryError("workflow name does not match adapter name")
        if name in self._workflows:
            raise WorkflowRegistryError(f"workflow is already registered: {name}")
        self._workflows[name] = adapter
        return adapter

    def get(self, name: str) -> WorkflowAdapter[Any, Any]:
        self._ensure_builtins()
        try:
            return self._workflows[name]
        except KeyError as exc:
            raise UnknownWorkflowError(f"unknown workflow: {name}") from exc

    def resolve(self, name: str) -> WorkflowAdapter[Any, Any]:
        return self.get(name)

    def names(self) -> tuple[str, ...]:
        self._ensure_builtins()
        return tuple(sorted(self._workflows))

    def __contains__(self, name: object) -> bool:
        self._ensure_builtins()
        return name in self._workflows

    def is_registered(self, name: str) -> bool:
        """Check the raw registry during built-in registration."""

        return name in self._workflows

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())


_bootstrapping = False
default_registry = WorkflowRegistry()
workflow_registry = default_registry


def register_workflow(name: str, adapter: WorkflowAdapter[Any, Any]) -> WorkflowAdapter[Any, Any]:
    """Register an adapter in the process-wide task allowlist."""

    return default_registry.register(name, adapter)
