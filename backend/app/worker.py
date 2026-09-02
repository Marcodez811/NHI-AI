"""Typed, settings-driven Taskiq worker launcher.

Keeping process counts and async concurrency in Settings makes Compose and
native deployments use the same contract and prevents the two workload types
from accidentally sharing a worker process.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal, Sequence

from app.config import settings

WorkerKind = Literal["documents", "tasks", "scheduler"]


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    kind: WorkerKind
    broker: str
    modules: tuple[str, ...]
    processes: int
    max_async_tasks: int


def worker_spec(kind: WorkerKind) -> WorkerSpec:
    if kind == "documents":
        return WorkerSpec(
            kind=kind,
            broker="app.broker:documents_broker",
            modules=("app.tasks.documents",),
            processes=settings.documents_worker_processes,
            max_async_tasks=settings.documents_worker_max_async_tasks,
        )
    if kind == "tasks":
        return WorkerSpec(
            kind=kind,
            broker="app.broker:tasks_broker",
            modules=("app.tasks.agents",),
            processes=settings.tasks_worker_processes,
            max_async_tasks=settings.tasks_worker_max_async_tasks,
        )
    if kind == "scheduler":
        return WorkerSpec(
            kind=kind,
            broker="app.scheduler:scheduler",
            modules=("app.tasks.documents",),
            processes=1,
            max_async_tasks=1,
        )
    raise ValueError(f"unknown worker kind: {kind!r}")


def taskiq_argv(kind: WorkerKind) -> list[str]:
    spec = worker_spec(kind)
    if kind == "scheduler":
        return ["taskiq", "scheduler", spec.broker, *spec.modules]
    return [
        "taskiq",
        "worker",
        spec.broker,
        *spec.modules,
        "--workers",
        str(spec.processes),
        "--max-async-tasks",
        str(spec.max_async_tasks),
    ]


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("documents", "tasks", "scheduler"):
        raise SystemExit("usage: python -m app.worker {documents|tasks|scheduler}")
    # Reuse Taskiq's own CLI parser and lifecycle.  This launcher only owns
    # selecting the typed workload settings above.
    sys.argv[:] = taskiq_argv(args[0])
    from taskiq.__main__ import main as taskiq_main

    taskiq_main()


if __name__ == "__main__":  # pragma: no cover
    main()
