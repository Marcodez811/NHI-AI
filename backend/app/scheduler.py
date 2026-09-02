"""TaskIQ scheduler configuration for durable document recovery schedules."""

from __future__ import annotations

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListRedisScheduleSource

from app.broker import documents_broker
from app.config import settings

schedule_source = ListRedisScheduleSource(
    url=settings.redis_url,
    prefix=settings.taskiq_schedule_prefix,
)
scheduler = TaskiqScheduler(
    broker=documents_broker,
    # Label schedules provide the recurring reconciliation cadence; the Redis
    # source retains one-shot exponential retry deliveries across restarts.
    sources=[LabelScheduleSource(documents_broker), schedule_source],
)


def main() -> None:  # pragma: no cover - exercised by the container launcher
    import sys

    sys.argv[:] = ["taskiq", "scheduler", "app.scheduler:scheduler", "app.tasks.documents"]
    from taskiq.__main__ import main as taskiq_main

    taskiq_main()


if __name__ == "__main__":
    main()
