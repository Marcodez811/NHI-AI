"""Delivery of durable outline-approval requests to TaskIQ.

The approval repository owns the database transaction.  This module owns the
two non-transactional effects that follow it: materializing the already
approved outline for the existing author contract, then publishing
``agents.run``.  It marks an event delivered only after publication, so a
process crash in that small interval deliberately causes a later retry to
publish again.  The slide worker's durable lease claim is the idempotency
boundary for those duplicate deliveries.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.services.agentic import AgentTaskPayload
from app.services.slides.outline_repository import (
    SlideOutlineApprovalOutbox,
    SlideOutlineRepository,
    SlideOutlineRevision,
)


async def dispatch_approval_outbox(
    *,
    event: SlideOutlineApprovalOutbox,
    revision: SlideOutlineRevision,
    repository: SlideOutlineRepository,
    task: Any,
    jobs_root: Path,
) -> bool:
    """Publish one approval event and mark it delivered after TaskIQ accepts it.

    ``False`` means a concurrent dispatcher already completed the event.  An
    exception deliberately leaves it pending for a request retry or the
    periodic dispatcher; callers should surface their ordinary temporary
    service-unavailable response without rolling back the approval.
    """

    if event.dispatched_at is not None:
        return False
    if event.task_name != "agents.run":
        raise RuntimeError(f"Unsupported approval outbox task: {event.task_name}")
    try:
        outline_path = jobs_root / str(event.job_id) / "work" / "outline.json"
        await asyncio.to_thread(outline_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            outline_path.write_text,
            json.dumps(revision.outline, ensure_ascii=False),
            encoding="utf-8",
        )
        payload = AgentTaskPayload(
            job_id=event.job_id,
            workflow=str(event.payload.get("workflow", "slides")),
            input={},
            resume_from=event.payload.get("resume_from"),
        )
        await task.kicker().with_task_id(str(event.job_id)).kiq(payload)
        marked = await repository.mark_approval_outbox_dispatched(event.id)
        if not marked:
            # A competing dispatcher may have committed the delivery after
            # this process published.  The duplicate is valid at-least-once
            # behavior and the worker claim prevents duplicated authoring.
            return False
        return True
    except Exception as exc:
        try:
            await repository.record_approval_outbox_failure(event.id, str(exc))
        except Exception:
            # A recording failure must not hide a TaskIQ/file-system failure;
            # the untouched pending row remains recoverable by the sweep.
            pass
        raise


__all__ = ["dispatch_approval_outbox"]
