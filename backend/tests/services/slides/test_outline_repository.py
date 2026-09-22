from uuid import uuid4

import pytest

from app.models.slides import OutlineNode, SlideOutline
from app.services.slides.outline_repository import (
    InMemorySlideOutlineRepository,
    OutlineAlreadyApprovedError,
    OutlineRevisionNotFoundError,
    StaleOutlineRevisionError,
)


def make_outline(title: str = "Q3 policy briefing") -> SlideOutline:
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
            ),
            OutlineNode(
                id="findings",
                heading="Findings",
                intent="Present the grounded findings in order of impact.",
                key_points=["Finding A", "Finding B", "Finding C"],
                evidence_refs=["evidence-2", "evidence-3"],
                emphasis="deep",
                approx_slides=4,
            ),
        ],
        total_slides=6,
    )


@pytest.mark.asyncio
async def test_revisions_are_appended_with_increasing_numbers():
    repo = InMemorySlideOutlineRepository()
    job_id = uuid4()

    first = await repo.create_next_revision(job_id, make_outline(), session_id="session-1")
    second = await repo.create_next_revision(job_id, make_outline("Revised briefing"), session_id="session-1")

    assert first.revision == 1
    assert second.revision == 2
    # Append-only: the earlier revision must still be readable unchanged.
    assert (await repo.get_revision(job_id, 1)).outline["title"] == "Q3 policy briefing"


@pytest.mark.asyncio
async def test_get_latest_returns_the_highest_revision():
    repo = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await repo.create_next_revision(job_id, make_outline(), session_id="session-1")
    await repo.create_next_revision(job_id, make_outline(), session_id="session-1")

    latest = await repo.get_latest(job_id)

    assert latest is not None
    assert latest.revision == 2


@pytest.mark.asyncio
async def test_get_latest_returns_none_for_unknown_job():
    repo = InMemorySlideOutlineRepository()
    assert await repo.get_latest(uuid4()) is None


@pytest.mark.asyncio
async def test_approve_with_no_revisions_raises_not_found():
    repo = InMemorySlideOutlineRepository()
    with pytest.raises(OutlineRevisionNotFoundError):
        await repo.approve(uuid4(), expected_revision=1)


@pytest.mark.asyncio
async def test_approving_a_stale_revision_fails():
    repo = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await repo.create_next_revision(job_id, make_outline(), session_id="session-1")
    await repo.create_next_revision(job_id, make_outline(), session_id="session-1")

    with pytest.raises(StaleOutlineRevisionError) as caught:
        await repo.approve(job_id, expected_revision=1)

    assert caught.value.expected_revision == 1
    assert caught.value.latest_revision == 2


@pytest.mark.asyncio
async def test_approving_twice_fails():
    repo = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await repo.create_next_revision(job_id, make_outline(), session_id="session-1")

    approved = await repo.approve(job_id, expected_revision=1)
    assert approved.approved_at is not None

    with pytest.raises(OutlineAlreadyApprovedError):
        await repo.approve(job_id, expected_revision=1)


@pytest.mark.asyncio
async def test_approving_the_current_latest_revision_succeeds():
    repo = InMemorySlideOutlineRepository()
    job_id = uuid4()
    await repo.create_next_revision(job_id, make_outline(), session_id="session-1")
    await repo.create_next_revision(job_id, make_outline("Revised briefing"), session_id="session-1")

    approved = await repo.approve(job_id, expected_revision=2)

    assert approved.revision == 2
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_revisions_are_scoped_per_job():
    repo = InMemorySlideOutlineRepository()
    first_job, second_job = uuid4(), uuid4()
    await repo.create_next_revision(first_job, make_outline(), session_id="session-1")

    # A fresh job starts its own revision sequence at 1, independent of any
    # other job's history.
    record = await repo.create_next_revision(second_job, make_outline(), session_id="session-2")
    assert record.revision == 1
