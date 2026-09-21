"""Lease-safety tests for the durable slide-job repository (Stage 5).

A job parked in ``JobStatus.AWAITING_INPUT`` is a deliberate pause, not a
crashed worker. ``scripts/reconcile.py`` has no lease-sweep logic at all --
it reconciles OpenAI vector-store attachments, not slide-job leases -- so the
actual protection against an ordinary delivery silently resuming a paused
workflow lives in ``SlideJobRepository.claim``, exercised here directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlmodel import Session, create_engine

from app.models.slides import JobStatus, SlideJob
from app.services.agentic.contracts import AgentPhase
from app.services.slides.repository import InMemorySlideJobRepository, SQLModelSlideJobRepository


def _job(job_id) -> SlideJob:
    return SlideJob(
        id=job_id,
        title="Q3 policy briefing",
        document_ids=[str(uuid4())],
        slides_count=8,
        guidance="Focus on reform impact.",
        tone="formal",
    )


@pytest.mark.asyncio
async def test_pause_for_outline_approval_releases_the_lease_without_finishing_the_job():
    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await repository.create(_job(job_id))
    await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)

    paused = await repository.pause_for_outline_approval(job_id, lease_token="worker-1")

    assert paused is not None
    assert paused.status == JobStatus.AWAITING_INPUT.value
    assert paused.phase == AgentPhase.AWAITING_OUTLINE.value
    assert paused.lease_token is None
    assert paused.lease_expires_at is None
    # Paused, not done: unlike mark_terminal, this must never stamp finished_at.
    assert paused.finished_at is None


@pytest.mark.asyncio
async def test_pause_for_outline_approval_refuses_a_lease_it_does_not_own():
    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await repository.create(_job(job_id))
    await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)

    result = await repository.pause_for_outline_approval(job_id, lease_token="a-different-worker")

    assert result is None


@pytest.mark.asyncio
async def test_claim_refuses_to_reclaim_a_job_awaiting_outline_approval():
    """The actual crashed-worker-sweep protection: an ordinary (non-resume)
    delivery for a job parked in AWAITING_INPUT must not pull it back into
    RUNNING, must not retry it, and must not treat it as failed.
    """

    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await repository.create(_job(job_id))
    await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
    await repository.pause_for_outline_approval(job_id, lease_token="worker-1")

    job, claimed = await repository.claim(job_id, lease_token="worker-2", lease_seconds=300)

    assert claimed is False
    assert job is not None
    assert job.status == JobStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_claim_allows_an_explicit_resume_to_reclaim_an_awaiting_outline_job():
    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await repository.create(_job(job_id))
    await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
    await repository.pause_for_outline_approval(job_id, lease_token="worker-1")

    job, claimed = await repository.claim(
        job_id,
        lease_token="worker-2",
        lease_seconds=300,
        allow_resume_from_awaiting_input=True,
    )

    assert claimed is True
    assert job is not None
    assert job.status == JobStatus.RUNNING.value
    assert job.lease_token == "worker-2"


def _migrated_engine(tmp_path: Path):
    path = tmp_path / "repository.db"
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


@pytest.mark.asyncio
async def test_sql_repository_also_refuses_to_reclaim_an_awaiting_outline_job(tmp_path):
    """The same crashed-worker-sweep guard, against the real SQL backend."""

    engine = _migrated_engine(tmp_path)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(_job(job_id))
        session.commit()

    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
        paused = await repository.pause_for_outline_approval(job_id, lease_token="worker-1")
        assert paused.status == JobStatus.AWAITING_INPUT.value
        assert paused.finished_at is None

    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        job, claimed = await repository.claim(job_id, lease_token="worker-2", lease_seconds=300)
        assert claimed is False
        assert job.status == JobStatus.AWAITING_INPUT.value

        job, claimed = await repository.claim(
            job_id, lease_token="worker-2", lease_seconds=300, allow_resume_from_awaiting_input=True,
        )
        assert claimed is True


@pytest.mark.asyncio
async def test_expire_awaiting_input_leaves_a_fresh_pause_untouched():
    """A pause within its TTL is not abandoned; the sweep must not touch it."""

    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await repository.create(_job(job_id))
    await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
    await repository.pause_for_outline_approval(job_id, lease_token="worker-1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    result = await repository.expire_awaiting_input(job_id, cutoff=cutoff, error="expired")

    assert result is None
    job = await repository.get(job_id)
    assert job.status == JobStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_expire_awaiting_input_fails_a_pause_older_than_the_cutoff():
    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await repository.create(_job(job_id))
    await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
    await repository.pause_for_outline_approval(job_id, lease_token="worker-1")

    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
    result = await repository.expire_awaiting_input(job_id, cutoff=cutoff, error="outline approval TTL exceeded")

    assert result is not None
    assert result.status == JobStatus.FAILED.value
    assert result.phase == AgentPhase.FAILED.value
    assert result.error == "outline approval TTL exceeded"
    assert result.finished_at is not None
    assert result.lease_token is None
    assert result.lease_expires_at is None


@pytest.mark.asyncio
async def test_expire_awaiting_input_never_races_a_resume_that_already_claimed_the_job():
    """The sweep must not clobber a job a concurrent resume already reclaimed.

    This simulates the interleaving the TTL sweep must survive: the sweep
    reads a stale AWAITING_INPUT id, but by the time it calls
    ``expire_awaiting_input`` the approve-endpoint's resume has already
    moved the job to RUNNING under a fresh lease.
    """

    repository = InMemorySlideJobRepository()
    job_id = uuid4()
    await repository.create(_job(job_id))
    await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
    await repository.pause_for_outline_approval(job_id, lease_token="worker-1")

    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
    _, claimed = await repository.claim(
        job_id, lease_token="worker-2", lease_seconds=300, allow_resume_from_awaiting_input=True,
    )
    assert claimed is True

    result = await repository.expire_awaiting_input(job_id, cutoff=cutoff, error="expired")

    assert result is None
    job = await repository.get(job_id)
    assert job.status == JobStatus.RUNNING.value
    assert job.lease_token == "worker-2"


@pytest.mark.asyncio
async def test_sql_repository_expire_awaiting_input_never_races_a_resume(tmp_path):
    """The same no-clobber guarantee, against the real SQL backend's row lock."""

    engine = _migrated_engine(tmp_path)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(_job(job_id))
        session.commit()

    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
        await repository.pause_for_outline_approval(job_id, lease_token="worker-1")

    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)

    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        _, claimed = await repository.claim(
            job_id, lease_token="worker-2", lease_seconds=300, allow_resume_from_awaiting_input=True,
        )
        assert claimed is True

    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        result = await repository.expire_awaiting_input(job_id, cutoff=cutoff, error="expired")
        assert result is None

    with Session(engine) as session:
        job = session.get(SlideJob, job_id)
        assert job.status == JobStatus.RUNNING.value
        assert job.lease_token == "worker-2"
        assert job.status == JobStatus.RUNNING.value


@pytest.mark.asyncio
@pytest.mark.parametrize("sql_backed", [False, True])
async def test_list_recent_orders_newest_first_and_bounds_limit(tmp_path: Path, sql_backed: bool):
    """Both storage implementations expose the same bounded discovery order."""

    engine = _migrated_engine(tmp_path) if sql_backed else None
    session = Session(engine) if engine is not None else None
    try:
        repository = SQLModelSlideJobRepository(session) if session is not None else InMemorySlideJobRepository()
        now = datetime.now(timezone.utc)
        for offset in (2, 0, 1):
            job = _job(uuid4())
            job.title = f"Job {offset}"
            job.created_at = now - timedelta(days=offset)
            await repository.create(job)

        assert [job.title for job in await repository.list_recent(limit=2)] == ["Job 0", "Job 1"]
        assert len(await repository.list_recent(limit=1000)) == 3
        assert len(await repository.list_recent(limit=0)) == 1
    finally:
        if session is not None:
            session.close()
