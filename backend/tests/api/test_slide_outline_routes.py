"""Stage 5 (docs/agents-sdk-migration-plan.md) outline API tests.

Route functions are called directly, the same pattern
``tests/api/test_slide_persistence.py`` already uses, rather than going
through FastAPI's dependency-injection machinery or a live HTTP client.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest

from app.api.routes.slides import (
    approve_slide_job_outline,
    get_slide_job_outline,
    send_slide_job_outline_message,
)
from app.config import settings
from app.models.slides import (
    ApproveOutlineRequest,
    JobStatus,
    OutlineMessageRequest,
    OutlineNode,
    SlideJob,
    SlideOutline,
)
from fastapi import HTTPException
from app.services.slides.outline_repository import InMemorySlideOutlineRepository
from app.services.slides.repository import InMemorySlideJobRepository


def _outline(title: str = "Q3 policy briefing") -> SlideOutline:
    return SlideOutline(
        title=title,
        narrative="A grounded walkthrough of the quarter's policy shifts.",
        nodes=[
            OutlineNode(
                id="intro",
                heading="Introduction",
                intent="Frame the quarter's central question.",
                key_points=["Context", "Stakes"],
                evidence_refs=["evidence-1"],
                emphasis="normal",
                approx_slides=2,
            )
        ],
        total_slides=2,
    )


class _Kicker:
    def __init__(self, task) -> None:
        self.task = task

    def with_task_id(self, task_id):
        self.task.task_id = task_id
        return self

    async def kiq(self, payload):
        self.task.calls.append(payload)


class _Task:
    def __init__(self) -> None:
        self.task_id = None
        self.calls: list = []

    def kicker(self):
        return _Kicker(self)


class _UnavailableTask(_Task):
    def kicker(self):
        task = self

        class _UnavailableKicker(_Kicker):
            async def kiq(self, payload):
                task.calls.append(payload)
                raise RuntimeError("TaskIQ is unavailable")

        return _UnavailableKicker(self)


class _CrashBeforeDeliveryMarkRepository(InMemorySlideOutlineRepository):
    """Simulate a process death after TaskIQ accepts a publication."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    async def mark_approval_outbox_dispatched(self, outbox_id):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("process died before recording delivery")
        return await super().mark_approval_outbox_dispatched(outbox_id)


class _FakePlanner:
    """A planner double that never reaches the network.

    Persists a revised outline through the same ``SlideOutlineRepository``
    the route hands it, and yields the same SSE event vocabulary the real
    ``PlannerConversationService`` does, so the route/streaming wiring is
    exercised end to end.
    """

    def __init__(self, revised: SlideOutline) -> None:
        self.revised = revised
        self.calls: list = []

    async def continue_conversation(self, *, job_id, message, latest, outline_repository) -> AsyncGenerator[str, None]:
        self.calls.append((job_id, message, latest.revision))
        yield 'data: {"type":"status","phase":"planning"}\n\n'
        revision = await outline_repository.create_next_revision(job_id, self.revised, session_id=latest.session_id)
        yield f'data: {{"type":"done","revision":{revision.revision}}}\n\n'


async def _seed_job(slide_repository: InMemorySlideJobRepository, job_id) -> None:
    """Seed a durable job already parked AWAITING_INPUT, as it would be by the
    time a human ever reaches the approve endpoint (the worker pauses it
    there before an outline exists to approve).
    """

    await slide_repository.create(
        SlideJob(
            id=job_id,
            title="Q3 policy briefing",
            document_ids=[str(uuid4())],
            slides_count=8,
            guidance="Focus on reform impact.",
            tone="formal",
        )
    )
    await slide_repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
    await slide_repository.pause_for_outline_approval(job_id, lease_token="worker-1")


@pytest.mark.asyncio
async def test_get_slide_job_outline_returns_the_latest_revision():
    outline_repository = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await outline_repository.create_next_revision(job_id, _outline("First draft"), session_id="session-1")
    await outline_repository.create_next_revision(job_id, _outline("Second draft"), session_id="session-1")

    response = await get_slide_job_outline(job_id, outline_repository)

    assert response.revision == 2
    assert response.outline.title == "Second draft"


@pytest.mark.asyncio
async def test_get_slide_job_outline_404_when_no_revision_exists():
    with pytest.raises(HTTPException) as caught:
        await get_slide_job_outline(uuid4(), InMemorySlideOutlineRepository())

    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_approve_outline_stale_revision_returns_409(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agent_jobs_root", tmp_path)
    outline_repository = InMemorySlideOutlineRepository()
    slide_repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await _seed_job(slide_repository, job_id)
    await outline_repository.create_next_revision(job_id, _outline("First draft"), session_id="session-1")
    await outline_repository.create_next_revision(job_id, _outline("Second draft"), session_id="session-1")
    task = _Task()

    with pytest.raises(HTTPException) as caught:
        await approve_slide_job_outline(
            job_id,
            ApproveOutlineRequest(expected_revision=1),
            outline_repository,
            task,
            slide_repository,
        )

    assert caught.value.status_code == 409
    assert task.calls == []


@pytest.mark.asyncio
async def test_approve_outline_idempotent_retry_enqueues_the_resume_exactly_once(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agent_jobs_root", tmp_path)
    outline_repository = InMemorySlideOutlineRepository()
    slide_repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await _seed_job(slide_repository, job_id)
    await outline_repository.create_next_revision(job_id, _outline(), session_id="session-1")
    task = _Task()
    request = ApproveOutlineRequest(expected_revision=1)

    first = await approve_slide_job_outline(job_id, request, outline_repository, task, slide_repository)
    second = await approve_slide_job_outline(job_id, request, outline_repository, task, slide_repository)

    assert first.job_id == second.job_id == job_id
    assert first.approved_revision == second.approved_revision == 1
    # Approval itself does not resume the job -- the enqueued worker is what
    # eventually reclaims it via ``resume_from="author"`` -- so it is still
    # reported as AWAITING_INPUT immediately after approval.
    assert first.status is JobStatus.AWAITING_INPUT
    # Exactly one resume was enqueued despite the repeated call.
    assert len(task.calls) == 1
    assert task.calls[0].resume_from == "author"
    assert task.calls[0].job_id == job_id

    outline_path = tmp_path / str(job_id) / "work" / "outline.json"
    assert outline_path.is_file()
    assert json.loads(outline_path.read_text(encoding="utf-8"))["title"] == "Q3 policy briefing"


@pytest.mark.asyncio
async def test_approval_outbox_retries_after_taskiq_is_unavailable(monkeypatch, tmp_path):
    """A failed immediate dispatch leaves the committed approval recoverable."""

    monkeypatch.setattr(settings, "agent_jobs_root", tmp_path)
    outline_repository = InMemorySlideOutlineRepository()
    slide_repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await _seed_job(slide_repository, job_id)
    await outline_repository.create_next_revision(job_id, _outline(), session_id="session-1")

    with pytest.raises(HTTPException) as caught:
        await approve_slide_job_outline(
            job_id,
            ApproveOutlineRequest(expected_revision=1),
            outline_repository,
            _UnavailableTask(),
            slide_repository,
        )
    assert caught.value.status_code == 503
    event = await outline_repository.get_approval_outbox(job_id, 1)
    assert event is not None
    assert event.dispatched_at is None
    assert event.attempts == 1

    resumed = _Task()
    response = await approve_slide_job_outline(
        job_id,
        ApproveOutlineRequest(expected_revision=1),
        outline_repository,
        resumed,
        slide_repository,
    )
    assert response.approved_revision == 1
    assert len(resumed.calls) == 1
    event = await outline_repository.get_approval_outbox(job_id, 1)
    assert event is not None
    assert event.dispatched_at is not None


@pytest.mark.asyncio
async def test_approval_outbox_republishes_after_crash_before_delivery_mark(monkeypatch, tmp_path):
    """A publish-before-mark crash is at-least-once, not a lost resume."""

    monkeypatch.setattr(settings, "agent_jobs_root", tmp_path)
    outline_repository = _CrashBeforeDeliveryMarkRepository()
    slide_repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await _seed_job(slide_repository, job_id)
    await outline_repository.create_next_revision(job_id, _outline(), session_id="session-1")
    task = _Task()

    with pytest.raises(HTTPException) as caught:
        await approve_slide_job_outline(
            job_id,
            ApproveOutlineRequest(expected_revision=1),
            outline_repository,
            task,
            slide_repository,
        )
    assert caught.value.status_code == 503
    assert len(task.calls) == 1

    await approve_slide_job_outline(
        job_id,
        ApproveOutlineRequest(expected_revision=1),
        outline_repository,
        task,
        slide_repository,
    )
    assert len(task.calls) == 2
    event = await outline_repository.get_approval_outbox(job_id, 1)
    assert event is not None
    assert event.dispatched_at is not None


@pytest.mark.asyncio
async def test_approve_outline_returns_404_for_an_unknown_job(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "agent_jobs_root", tmp_path)
    outline_repository = InMemorySlideOutlineRepository()
    slide_repository = InMemorySlideJobRepository()
    task = _Task()

    with pytest.raises(HTTPException) as caught:
        await approve_slide_job_outline(
            uuid4(),
            ApproveOutlineRequest(expected_revision=1),
            outline_repository,
            task,
            slide_repository,
        )

    assert caught.value.status_code == 404
    assert task.calls == []


async def _collect_sse(response) -> list[str]:
    return [chunk async for chunk in response.body_iterator]


@pytest.mark.asyncio
async def test_send_outline_message_streams_and_persists_the_next_revision():
    outline_repository = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await outline_repository.create_next_revision(job_id, _outline("First draft"), session_id="session-1")
    revised = _outline("Deeper on reform impact")
    planner = _FakePlanner(revised)

    response = await send_slide_job_outline_message(
        job_id,
        OutlineMessageRequest(message="Go deeper on the reform impact section."),
        outline_repository,
        planner,
    )
    chunks = await _collect_sse(response)

    assert any('"type":"done"' in chunk for chunk in chunks)
    assert planner.calls == [(job_id, "Go deeper on the reform impact section.", 1)]
    latest = await outline_repository.get_latest(job_id)
    assert latest.revision == 2
    assert latest.outline["title"] == "Deeper on reform impact"


@pytest.mark.asyncio
async def test_outline_message_stream_forbids_proxy_buffering():
    # An outline revision can take over a minute, during which the stream carries
    # only heartbeats. Without ``no-transform``, the frontend's compressing proxy
    # buffered them and the browser timed out at 45 seconds with nothing received.
    outline_repository = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await outline_repository.create_next_revision(job_id, _outline("First draft"), session_id="session-1")

    response = await send_slide_job_outline_message(
        job_id,
        OutlineMessageRequest(message="Tighten the introduction."),
        outline_repository,
        _FakePlanner(_outline("Tighter")),
    )
    await _collect_sse(response)

    assert "no-transform" in response.headers["cache-control"]
    assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_outline_message_disconnect_closes_planner_stream():
    outline_repository = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await outline_repository.create_next_revision(job_id, _outline(), session_id="session-1")
    closed = []

    class PausedPlanner:
        async def continue_conversation(self, **kwargs):
            try:
                yield 'data: {"type":"status","phase":"planning"}\n\n'
            finally:
                closed.append(True)

    response = await send_slide_job_outline_message(
        job_id,
        OutlineMessageRequest(message="Adjust the outline."),
        outline_repository,
        PausedPlanner(),
    )
    assert '"type":"status"' in await response.body_iterator.__anext__()

    await response.body_iterator.aclose()

    assert closed == [True]


@pytest.mark.asyncio
async def test_send_outline_message_404_when_no_outline_exists():
    with pytest.raises(HTTPException) as caught:
        await send_slide_job_outline_message(
            uuid4(),
            OutlineMessageRequest(message="hello"),
            InMemorySlideOutlineRepository(),
            _FakePlanner(_outline()),
        )

    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_send_outline_message_409_once_the_outline_is_approved():
    outline_repository = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await outline_repository.create_next_revision(job_id, _outline(), session_id="session-1")
    await outline_repository.approve(job_id, expected_revision=1)

    with pytest.raises(HTTPException) as caught:
        await send_slide_job_outline_message(
            job_id,
            OutlineMessageRequest(message="hello"),
            outline_repository,
            _FakePlanner(_outline()),
        )

    assert caught.value.status_code == 409
