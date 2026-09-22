"""SQL outline repository append-only and approval-conflict tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlmodel import Session, create_engine, select

from app.models.slides import OutlineNode, SlideJob, SlideOutline
from app.services.slides.outline_repository import (
    OutlineAlreadyApprovedError,
    SQLModelSlideOutlineRepository,
    SlideOutlineApprovalOutbox,
    StaleOutlineRevisionError,
)


def _migrated_engine(tmp_path: Path):
    path = tmp_path / "outline_repository.db"
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def _slide_job(job_id) -> SlideJob:
    return SlideJob(
        id=job_id,
        title="Q3 policy briefing",
        document_ids=[str(uuid4())],
        slides_count=10,
        guidance="Focus on reform impact",
        tone="formal",
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
        ],
        total_slides=2,
    )


@pytest.mark.asyncio
async def test_sql_repository_appends_revisions_and_reads_latest(tmp_path):
    engine = _migrated_engine(tmp_path)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(_slide_job(job_id))
        session.commit()

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        await repo.create_next_revision(job_id, make_outline(), session_id="session-1")
        await repo.create_next_revision(job_id, make_outline("Revised briefing"), session_id="session-1")

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        latest = await repo.get_latest(job_id)
        assert latest is not None
        assert latest.revision == 2
        assert latest.outline["title"] == "Revised briefing"

        first = await repo.get_revision(job_id, 1)
        assert first is not None
        assert first.outline["title"] == "Q3 policy briefing"


@pytest.mark.asyncio
async def test_sql_repository_rejects_approval_of_a_stale_revision(tmp_path):
    engine = _migrated_engine(tmp_path)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(_slide_job(job_id))
        session.commit()

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        await repo.create_next_revision(job_id, make_outline(), session_id="session-1")
        await repo.create_next_revision(job_id, make_outline("Revised briefing"), session_id="session-1")

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        with pytest.raises(StaleOutlineRevisionError):
            await repo.approve(job_id, expected_revision=1)


@pytest.mark.asyncio
async def test_sql_repository_rejects_double_approval(tmp_path):
    engine = _migrated_engine(tmp_path)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(_slide_job(job_id))
        session.commit()

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        await repo.create_next_revision(job_id, make_outline(), session_id="session-1")

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        approved = await repo.approve(job_id, expected_revision=1)
        assert approved.approved_at is not None

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        with pytest.raises(OutlineAlreadyApprovedError):
            await repo.approve(job_id, expected_revision=1)


@pytest.mark.asyncio
async def test_sql_approval_commits_the_unique_resume_outbox_with_the_approval(tmp_path):
    """The durable enqueue intent exists as soon as approval is observable."""

    engine = _migrated_engine(tmp_path)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(_slide_job(job_id))
        session.commit()

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        await repo.create_next_revision(job_id, make_outline(), session_id="session-1")
        await repo.approve(job_id, expected_revision=1)

    with Session(engine) as session:
        approval = session.exec(
            select(SlideOutlineApprovalOutbox).where(
                SlideOutlineApprovalOutbox.job_id == job_id,
                SlideOutlineApprovalOutbox.revision == 1,
            )
        ).one()
        assert approval.task_name == "agents.run"
        assert approval.payload == {"workflow": "slides", "resume_from": "author"}
        assert approval.dispatched_at is None


@pytest.mark.asyncio
async def test_sql_repository_enforces_unique_job_and_revision(tmp_path):
    engine = _migrated_engine(tmp_path)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(_slide_job(job_id))
        session.commit()

    with Session(engine) as session:
        repo = SQLModelSlideOutlineRepository(session)
        await repo.create_next_revision(job_id, make_outline(), session_id="session-1")

    # Inserting a duplicate (job_id, revision) pair directly must violate the
    # unique constraint the migration creates; the repository itself never
    # produces this, but the schema must still guard against it.
    from sqlalchemy.exc import IntegrityError

    from app.services.slides.outline_repository import SlideOutlineRevision

    with Session(engine) as session:
        session.add(SlideOutlineRevision(job_id=job_id, revision=1, outline={}, session_id="session-2"))
        with pytest.raises(IntegrityError):
            session.commit()
