"""Public coordinator facade for bounded agentic workflow execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import WorkflowRegistry, workflow_registry
from .runner import runner_registry as default_runner_registry


class WorkflowCoordinator:
    """Coordinate an allowlisted workflow through provider-neutral runners.

    The implementation remains in ``service.execute_workflow`` for backward
    compatibility with the TaskIQ entrypoint.  This facade gives integrations
    and tests an explicit coordinator object without introducing a second
    lifecycle implementation.
    """

    def __init__(
        self,
        *,
        registry: WorkflowRegistry = workflow_registry,
        runners: Any | None = None,
        runner_registry: Any | None = None,
        workspace_root: Path = Path("/tmp/agentic/jobs"),
        skills_root: Path | None = None,
    ) -> None:
        self.registry = registry
        self.runners = runner_registry if runner_registry is not None else (runners or default_runner_registry)
        self.workspace_root = workspace_root
        self.skills_root = skills_root

    async def execute(self, payload: Any, *, progress_callback: Any = None, timeout_seconds: float | None = None) -> Any:
        from .service import execute_workflow

        # ``runner=None`` means the service resolves the process-wide registry;
        # a configured registry is passed explicitly for deterministic tests or
        # mixed-provider server plans.
        return await execute_workflow(
            payload,
            registry=self.registry,
            runner=self.runners,
            workspace_root=self.workspace_root,
            skills_root=self.skills_root,
            progress_callback=progress_callback,
            timeout_seconds=timeout_seconds,
        )


__all__ = ["WorkflowCoordinator"]
