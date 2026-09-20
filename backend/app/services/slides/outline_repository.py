"""Durable persistence for planner outline revisions.

An outline discussion can stay open for days while a human negotiates
structure with the planner.  Serialized ``RunState`` resumption is
deliberately not used for this: it exists for resuming mid-tool-call
interruptions, not for a durable, reviewable history.  Instead every planner
turn that changes the plan is written as a new, immutable revision, and
approval always names the exact revision a human looked at.  There is
deliberately no update method -- a correction is always a new revision, never
a mutation of one that may already have been read or approved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Session, SQLModel, Field as SQLField, select

from app.models.slides import SlideOutline


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SlideOutlineRepositoryError(RuntimeError):
    """Base class for expected outline revision failures."""


class OutlineRevisionNotFoundError(SlideOutlineRepositoryError):
    """Raised when a job has no outline revision at all yet."""


class StaleOutlineRevisionError(SlideOutlineRepositoryError):
    """The revision an approval targets is no longer the latest.

    The planner may propose a new revision while a human is still looking at
    an older one; that approval must be rejected with a 409 rather than
    silently promoting a plan the user never actually saw.
    """

    def __init__(self, job_id: UUID, *, expected_revision: int, latest_revision: int) -> None:
        super().__init__(
            f"Revision {expected_revision} is stale for job {job_id}; "
            f"the latest revision is {latest_revision}."
        )
        self.job_id = job_id
        self.expected_revision = expected_revision
        self.latest_revision = latest_revision


class OutlineAlreadyApprovedError(SlideOutlineRepositoryError):
    """Raised when the targeted revision has already been approved once.

    Approval enqueues the author; approving twice must not enqueue it twice.
    """

    def __init__(self, job_id: UUID, *, revision: int) -> None:
        super().__init__(f"Revision {revision} for job {job_id} is already approved.")
        self.job_id = job_id
        self.revision = revision


class SlideOutlineRevision(SQLModel, table=True):
    """One immutable, append-only planner proposal for a slide job.

    The outline is stored as JSON rather than normalized columns because it
    is always read and written wholesale by the planner turn and the
    approval flow; nothing queries into individual nodes at the SQL layer.
    The ``(job_id, revision)`` pair is the durable identity an approval call
    names, so it is enforced unique rather than merely ordered by
    ``created_at``.
    """

    __tablename__ = "slide_outlines"
    __table_args__ = (UniqueConstraint("job_id", "revision", name="uq_slide_outlines_job_revision"),)

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    job_id: UUID = SQLField(foreign_key="slide_jobs.id", index=True)
    revision: int = SQLField(ge=1)
    outline: dict = SQLField(default_factory=dict, sa_column=Column(JSON, nullable=False))
    session_id: str = SQLField(max_length=128)
    created_at: datetime = SQLField(default_factory=_now, nullable=False)
    approved_at: datetime | None = SQLField(default=None)

    def to_outline(self) -> SlideOutline:
        """Deserialize the stored JSON back into the typed planning contract."""

        return SlideOutline.model_validate(self.outline)


class SlideOutlineApprovalOutbox(SQLModel, table=True):
    """A durable request to resume authoring after an outline approval.

    This is intentionally an *outbox*, not a second job queue: the SQL
    transaction that sets ``approved_at`` creates this row, then a dispatcher
    publishes it to TaskIQ.  A crash after publication but before
    ``dispatched_at`` is recorded may publish more than once; the slide-job
    lease claim makes that delivery safe.
    """

    __tablename__ = "slide_outline_approval_outbox"
    __table_args__ = (
        UniqueConstraint("job_id", "revision", name="uq_slide_outline_approval_outbox_job_revision"),
    )

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    job_id: UUID = SQLField(foreign_key="slide_jobs.id", index=True)
    revision: int = SQLField(ge=1)
    task_name: str = SQLField(default="agents.run", max_length=128)
    payload: dict = SQLField(default_factory=lambda: {"workflow": "slides", "resume_from": "author"}, sa_column=Column(JSON, nullable=False))
    created_at: datetime = SQLField(default_factory=_now, nullable=False)
    dispatched_at: datetime | None = SQLField(default=None, index=True)
    attempts: int = SQLField(default=0, ge=0)
    last_error: str | None = SQLField(default=None, max_length=512)


@runtime_checkable
class SlideOutlineRepository(Protocol):
    async def create_next_revision(
        self, job_id: UUID, outline: SlideOutline, *, session_id: str
    ) -> SlideOutlineRevision: ...

    async def get_latest(self, job_id: UUID) -> SlideOutlineRevision | None: ...

    async def get_revision(self, job_id: UUID, revision: int) -> SlideOutlineRevision | None: ...

    async def approve(self, job_id: UUID, *, expected_revision: int) -> SlideOutlineRevision: ...

    async def get_approval_outbox(
        self, job_id: UUID, revision: int
    ) -> SlideOutlineApprovalOutbox | None: ...

    async def mark_approval_outbox_dispatched(self, outbox_id: UUID) -> bool: ...

    async def record_approval_outbox_failure(self, outbox_id: UUID, error: str) -> None: ...


class InMemorySlideOutlineRepository:
    """Small async repository used by the default API dependency and tests."""

    def __init__(self) -> None:
        self.revisions: dict[UUID, SlideOutlineRevision] = {}
        self.approval_outbox: dict[UUID, SlideOutlineApprovalOutbox] = {}

    async def create_next_revision(
        self, job_id: UUID, outline: SlideOutline, *, session_id: str
    ) -> SlideOutlineRevision:
        latest = await self.get_latest(job_id)
        next_revision = 1 if latest is None else latest.revision + 1
        record = SlideOutlineRevision(
            job_id=job_id,
            revision=next_revision,
            outline=outline.model_dump(mode="json"),
            session_id=session_id,
        )
        self.revisions[record.id] = record
        return record

    async def get_latest(self, job_id: UUID) -> SlideOutlineRevision | None:
        candidates = [record for record in self.revisions.values() if record.job_id == job_id]
        if not candidates:
            return None
        return max(candidates, key=lambda record: record.revision)

    async def get_revision(self, job_id: UUID, revision: int) -> SlideOutlineRevision | None:
        return next(
            (
                record
                for record in self.revisions.values()
                if record.job_id == job_id and record.revision == revision
            ),
            None,
        )

    async def approve(self, job_id: UUID, *, expected_revision: int) -> SlideOutlineRevision:
        latest = await self.get_latest(job_id)
        if latest is None:
            raise OutlineRevisionNotFoundError(str(job_id))
        if latest.revision != expected_revision:
            raise StaleOutlineRevisionError(
                job_id, expected_revision=expected_revision, latest_revision=latest.revision
            )
        if latest.approved_at is not None:
            raise OutlineAlreadyApprovedError(job_id, revision=latest.revision)
        latest.approved_at = _now()
        self.revisions[latest.id] = latest
        event = SlideOutlineApprovalOutbox(job_id=job_id, revision=latest.revision)
        self.approval_outbox[event.id] = event
        return latest

    async def get_approval_outbox(
        self, job_id: UUID, revision: int
    ) -> SlideOutlineApprovalOutbox | None:
        return next(
            (
                event
                for event in self.approval_outbox.values()
                if event.job_id == job_id and event.revision == revision
            ),
            None,
        )

    async def mark_approval_outbox_dispatched(self, outbox_id: UUID) -> bool:
        event = self.approval_outbox.get(outbox_id)
        if event is None or event.dispatched_at is not None:
            return False
        event.attempts += 1
        event.last_error = None
        event.dispatched_at = _now()
        self.approval_outbox[outbox_id] = event
        return True

    async def record_approval_outbox_failure(self, outbox_id: UUID, error: str) -> None:
        event = self.approval_outbox.get(outbox_id)
        if event is None or event.dispatched_at is not None:
            return
        event.attempts += 1
        event.last_error = error[:512]
        self.approval_outbox[outbox_id] = event


class SQLModelSlideOutlineRepository(InMemorySlideOutlineRepository):
    """SQLModel repository backed by an injected synchronous ``Session``."""

    def __init__(self, session: Session) -> None:
        # Keep the in-memory methods as a behavioral reference, but never use
        # the local dictionary for a SQL-backed repository.
        super().__init__()
        self.session = session

    async def create_next_revision(
        self, job_id: UUID, outline: SlideOutline, *, session_id: str
    ) -> SlideOutlineRevision:
        latest = await self.get_latest(job_id)
        next_revision = 1 if latest is None else latest.revision + 1
        record = SlideOutlineRevision(
            job_id=job_id,
            revision=next_revision,
            outline=outline.model_dump(mode="json"),
            session_id=session_id,
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    async def get_latest(self, job_id: UUID) -> SlideOutlineRevision | None:
        return self.session.exec(
            select(SlideOutlineRevision)
            .where(SlideOutlineRevision.job_id == job_id)
            .order_by(SlideOutlineRevision.revision.desc())
        ).first()

    async def get_revision(self, job_id: UUID, revision: int) -> SlideOutlineRevision | None:
        return self.session.exec(
            select(SlideOutlineRevision).where(
                SlideOutlineRevision.job_id == job_id,
                SlideOutlineRevision.revision == revision,
            )
        ).first()

    async def approve(self, job_id: UUID, *, expected_revision: int) -> SlideOutlineRevision:
        latest = self.session.exec(
            select(SlideOutlineRevision)
            .where(SlideOutlineRevision.job_id == job_id)
            .order_by(SlideOutlineRevision.revision.desc())
            .with_for_update()
        ).first()
        if latest is None:
            raise OutlineRevisionNotFoundError(str(job_id))
        if latest.revision != expected_revision:
            raise StaleOutlineRevisionError(
                job_id, expected_revision=expected_revision, latest_revision=latest.revision
            )
        if latest.approved_at is not None:
            raise OutlineAlreadyApprovedError(job_id, revision=latest.revision)
        latest.approved_at = _now()
        event = SlideOutlineApprovalOutbox(job_id=job_id, revision=latest.revision)
        self.session.add(latest)
        self.session.add(event)
        self.session.commit()
        self.session.refresh(latest)
        return latest

    async def get_approval_outbox(
        self, job_id: UUID, revision: int
    ) -> SlideOutlineApprovalOutbox | None:
        return self.session.exec(
            select(SlideOutlineApprovalOutbox).where(
                SlideOutlineApprovalOutbox.job_id == job_id,
                SlideOutlineApprovalOutbox.revision == revision,
            )
        ).first()

    async def mark_approval_outbox_dispatched(self, outbox_id: UUID) -> bool:
        event = self.session.get(SlideOutlineApprovalOutbox, outbox_id)
        if event is None or event.dispatched_at is not None:
            return False
        event.attempts += 1
        event.last_error = None
        event.dispatched_at = _now()
        self.session.add(event)
        self.session.commit()
        return True

    async def record_approval_outbox_failure(self, outbox_id: UUID, error: str) -> None:
        event = self.session.get(SlideOutlineApprovalOutbox, outbox_id)
        if event is None or event.dispatched_at is not None:
            return
        event.attempts += 1
        event.last_error = error[:512]
        self.session.add(event)
        self.session.commit()

    async def pending_approval_outbox(self, *, limit: int = 100) -> list[SlideOutlineApprovalOutbox]:
        return list(
            self.session.exec(
                select(SlideOutlineApprovalOutbox)
                .where(SlideOutlineApprovalOutbox.dispatched_at.is_(None))
                .order_by(SlideOutlineApprovalOutbox.created_at)
                .limit(limit)
            ).all()
        )


__all__ = [
    "SlideOutlineRevision",
    "SlideOutlineApprovalOutbox",
    "SlideOutlineRepository",
    "InMemorySlideOutlineRepository",
    "SQLModelSlideOutlineRepository",
    "SlideOutlineRepositoryError",
    "OutlineRevisionNotFoundError",
    "StaleOutlineRevisionError",
    "OutlineAlreadyApprovedError",
]
