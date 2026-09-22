from app.services.agentic.events import AgentEventType
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlmodel import Session, create_engine, select

from app.models.slides import JobStatus, OutlineNode, SlideJob, SlideOutline
from app.services.agentic.contracts import AgentPhase, AgentTaskPayload, AgentTaskResult, WorkflowStatus
from app.services.slides.outline_repository import (
    SQLModelSlideOutlineRepository,
    SlideOutlineApprovalOutbox,
)
from app.services.slides.repository import SQLModelSlideJobRepository
from app.tasks.agents import _progress_telemetry_type


def test_progress_telemetry_classifies_provider_work_as_node_progress():
    assert _progress_telemetry_type({"event_type": "node_progress"}) is AgentEventType.NODE_PROGRESS
    assert _progress_telemetry_type({"heartbeat": True}) is AgentEventType.HEARTBEAT
    assert _progress_telemetry_type({"phase": "reviewing"}) is AgentEventType.PHASE_CHANGED


def test_worker_builds_allowlisted_codex_runner_with_default_model(monkeypatch):
    from app.config import settings
    from app.tasks.agents import _build_runner_registry

    monkeypatch.setattr(settings, "agent_default_model", "worker-default")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("worker-key"))
    monkeypatch.setattr(settings, "agent_heartbeat_seconds", 10.0)
    registry, timeout = _build_runner_registry()
    runner = registry.resolve("codex")

    assert runner.name == "codex"
    assert runner.codex_runner.model == "worker-default"
    assert runner.codex_runner.reasoning_effort.value == "high"
    assert runner.codex_runner.api_key.get_secret_value() == "worker-key"
    assert runner.codex_runner.heartbeat_seconds == 10.0
    assert timeout == runner.timeout_seconds


def test_worker_passes_optional_provider_keys_to_agents_sdk_runner(monkeypatch):
    from app.config import settings
    from app.tasks.agents import _build_runner_registry

    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("gemini-key"))
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr("anthropic-key"))

    registry, _ = _build_runner_registry()

    runner = registry.resolve("agents")
    assert runner.litellm_api_keys["gemini"].get_secret_value() == "gemini-key"
    assert runner.litellm_api_keys["anthropic"].get_secret_value() == "anthropic-key"


@pytest.mark.asyncio
async def test_node_progress_enrichment_keeps_heartbeats_attributed_to_the_active_attempt():
    from app.services.agentic.service import _node_progress_callback

    received = []

    async def progress(event):
        received.append(event)

    callback = _node_progress_callback(
        progress,
        SimpleNamespace(
            node_id="author",
            role="presentation_author",
            model="author-model",
            attempt=2,
        ),
        "codex",
    )
    await callback({"heartbeat": True, "stage": "drafting"})

    assert received == [{
        "heartbeat": True,
        "stage": "drafting",
        "node_id": "author",
        "role": "presentation_author",
        "runner": "codex",
        "model": "author-model",
        "attempt": 2,
    }]


def _migrated_engine(tmp_path: Path):
    path = tmp_path / "agents_worker.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def _outline() -> SlideOutline:
    return SlideOutline(
        title="Q3 policy briefing",
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


class _OutboxKicker:
    def __init__(self, task):
        self.task = task

    def with_task_id(self, task_id):
        self.task.task_ids.append(task_id)
        return self

    async def kiq(self, payload):
        self.task.payloads.append(payload)


class _OutboxTask:
    def __init__(self):
        self.task_ids: list[str] = []
        self.payloads: list[AgentTaskPayload] = []

    def kicker(self):
        return _OutboxKicker(self)


@pytest.mark.asyncio
async def test_approval_outbox_sweep_recovers_a_committed_unpublished_approval(tmp_path, monkeypatch):
    """The scheduled dispatcher closes a process crash after the DB commit."""

    import app.tasks.agents as agents_module
    from app.config import settings

    engine = _migrated_engine(tmp_path)
    monkeypatch.setattr(agents_module, "engine", engine)
    monkeypatch.setattr(settings, "agent_jobs_root", tmp_path / "jobs")
    task = _OutboxTask()
    monkeypatch.setattr(agents_module, "run", task)
    job_id = uuid4()
    with Session(engine) as session:
        session.add(SlideJob(
            id=job_id,
            title="Q3 policy briefing",
            document_ids=[str(uuid4())],
            slides_count=8,
            guidance="Focus on reform impact.",
            tone="formal",
        ))
        session.commit()
    with Session(engine) as session:
        repository = SQLModelSlideOutlineRepository(session)
        await repository.create_next_revision(job_id, _outline(), session_id="session-1")
        await repository.approve(job_id, expected_revision=1)

    result = await agents_module.dispatch_outline_approval_outbox_task()
    assert result == {"delivered": 1, "failed": 0}
    assert len(task.payloads) == 1
    assert task.payloads[0].resume_from == "author"

    with Session(engine) as session:
        event = session.exec(select(SlideOutlineApprovalOutbox)).one()
        assert event.dispatched_at is not None
    assert (tmp_path / "jobs" / str(job_id) / "work" / "outline.json").is_file()


@pytest.mark.asyncio
async def test_worker_releases_the_lease_and_parks_the_job_on_an_outline_pause(tmp_path, monkeypatch):
    """Stage 5 (docs/agents-sdk-migration-plan.md): when ``execute_workflow``
    returns the non-terminal AWAITING_OUTLINE result, ``agents.run`` must
    release the worker's lease and park the durable job at AWAITING_INPUT
    instead of running the ordinary ``mark_terminal`` path -- the job is
    neither completed nor failed. ``execute_workflow`` and the runner registry
    are faked here so this exercises only that glue, not a live model call.
    """

    import app.tasks.agents as agents_module

    engine = _migrated_engine(tmp_path)
    monkeypatch.setattr(agents_module, "engine", engine)
    monkeypatch.setattr(agents_module, "_build_runner_registry", lambda: (object(), 60.0))

    job_id = uuid4()
    with Session(engine) as session:
        session.add(SlideJob(
            id=job_id,
            title="Q3 policy briefing",
            document_ids=[str(uuid4())],
            slides_count=8,
            guidance="Focus on reform impact.",
            tone="formal",
        ))
        session.commit()

    async def fake_execute_workflow(payload, **kwargs):
        started = datetime.now(timezone.utc)
        return AgentTaskResult(
            job_id=payload.job_id,
            workflow=payload.workflow,
            status=WorkflowStatus.RUNNING,
            phase=AgentPhase.AWAITING_OUTLINE,
            output=None,
            started_at=started,
            finished_at=started,
            error=None,
        )

    monkeypatch.setattr(agents_module, "execute_workflow", fake_execute_workflow)

    events: list = []

    async def fake_emit_telemetry(run_id, event_type, **fields):
        events.append((event_type, fields.get("phase")))

    monkeypatch.setattr(agents_module, "_emit_telemetry", fake_emit_telemetry)

    result = await agents_module.run(AgentTaskPayload(job_id=job_id, workflow="slides", input={}))

    assert result.status is WorkflowStatus.RUNNING
    assert result.phase is AgentPhase.AWAITING_OUTLINE

    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        durable = await repository.get(job_id)
    assert durable.status == JobStatus.AWAITING_INPUT.value
    assert durable.phase == AgentPhase.AWAITING_OUTLINE.value
    assert durable.lease_token is None
    assert durable.lease_expires_at is None
    assert durable.finished_at is None

    # No completion/failure telemetry was ever emitted for this pause.
    assert (AgentEventType.RUN_COMPLETED, AgentPhase.AWAITING_OUTLINE) not in events
    assert (AgentEventType.RUN_FAILED, AgentPhase.AWAITING_OUTLINE) not in events
    assert (AgentEventType.PHASE_CHANGED, AgentPhase.AWAITING_OUTLINE) in events

    # A subsequent, ordinary (non-resume) delivery must not silently reclaim
    # the paused job -- this is the actual crashed-worker-sweep guard.
    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        _, claimed = await repository.claim(job_id, lease_token="another-worker", lease_seconds=300)
    assert claimed is False


async def _paused_job(engine, job_id, *, updated_at: datetime) -> None:
    """Create a job parked in AWAITING_INPUT with a specific ``updated_at``.

    ``pause_for_outline_approval`` always stamps the current time, so the
    desired staleness is written directly afterwards -- there is no repository
    method for backdating a row, and the sweep only cares about the column.
    """

    with Session(engine) as session:
        session.add(SlideJob(
            id=job_id,
            title="Q3 policy briefing",
            document_ids=[str(uuid4())],
            slides_count=8,
            guidance="Focus on reform impact.",
            tone="formal",
        ))
        session.commit()
    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        await repository.claim(job_id, lease_token="worker-1", lease_seconds=300)
        await repository.pause_for_outline_approval(job_id, lease_token="worker-1")
    with Session(engine) as session:
        job = session.get(SlideJob, job_id)
        job.updated_at = updated_at
        session.add(job)
        session.commit()


@pytest.mark.asyncio
async def test_expire_awaiting_outline_task_expires_a_stale_pause_and_removes_its_workspace(tmp_path, monkeypatch):
    """Risk 3 (docs/agents-sdk-migration-plan.md, Stage 5b): a pause older
    than the TTL is failed with a clear reason and its workspace directory is
    removed, using the same ``cleanup_job`` helper every other terminal path
    uses.
    """

    import app.tasks.agents as agents_module
    from app.config import settings

    engine = _migrated_engine(tmp_path)
    monkeypatch.setattr(agents_module, "engine", engine)
    monkeypatch.setattr(settings, "agent_awaiting_outline_ttl_seconds", 60)
    jobs_root = tmp_path / "jobs"
    monkeypatch.setattr(settings, "agent_jobs_root", jobs_root)

    stale_id = uuid4()
    await _paused_job(engine, stale_id, updated_at=datetime.now(timezone.utc) - timedelta(hours=1))
    workspace = jobs_root / str(stale_id)
    (workspace / "work").mkdir(parents=True)
    (workspace / "work" / "outline.json").write_text("{}", encoding="utf-8")

    result = await agents_module.expire_awaiting_outline_task()

    assert result == {"expired": 1}
    with Session(engine) as session:
        job = session.get(SlideJob, stale_id)
    assert job.status == JobStatus.FAILED.value
    assert job.phase == AgentPhase.FAILED.value
    assert job.error and "60 seconds" in job.error
    assert job.finished_at is not None
    assert job.lease_token is None
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_expire_awaiting_outline_task_leaves_a_fresh_pause_and_its_workspace_alone(tmp_path, monkeypatch):
    import app.tasks.agents as agents_module
    from app.config import settings

    engine = _migrated_engine(tmp_path)
    monkeypatch.setattr(agents_module, "engine", engine)
    monkeypatch.setattr(settings, "agent_awaiting_outline_ttl_seconds", 60 * 60 * 24 * 7)
    jobs_root = tmp_path / "jobs"
    monkeypatch.setattr(settings, "agent_jobs_root", jobs_root)

    fresh_id = uuid4()
    await _paused_job(engine, fresh_id, updated_at=datetime.now(timezone.utc))
    workspace = jobs_root / str(fresh_id)
    workspace.mkdir(parents=True)

    result = await agents_module.expire_awaiting_outline_task()

    assert result == {"expired": 0}
    with Session(engine) as session:
        job = session.get(SlideJob, fresh_id)
    assert job.status == JobStatus.AWAITING_INPUT.value
    assert workspace.exists()


@pytest.mark.asyncio
async def test_expire_awaiting_outline_task_never_touches_a_running_job(tmp_path, monkeypatch):
    import app.tasks.agents as agents_module
    from app.config import settings

    engine = _migrated_engine(tmp_path)
    monkeypatch.setattr(agents_module, "engine", engine)
    monkeypatch.setattr(settings, "agent_awaiting_outline_ttl_seconds", 60)
    jobs_root = tmp_path / "jobs"
    monkeypatch.setattr(settings, "agent_jobs_root", jobs_root)

    running_id = uuid4()
    with Session(engine) as session:
        session.add(SlideJob(
            id=running_id,
            title="Q3 policy briefing",
            document_ids=[str(uuid4())],
            slides_count=8,
            guidance="Focus on reform impact.",
            tone="formal",
        ))
        session.commit()
    with Session(engine) as session:
        repository = SQLModelSlideJobRepository(session)
        await repository.claim(running_id, lease_token="worker-1", lease_seconds=300)
    with Session(engine) as session:
        job = session.get(SlideJob, running_id)
        job.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.add(job)
        session.commit()
    workspace = jobs_root / str(running_id)
    workspace.mkdir(parents=True)

    result = await agents_module.expire_awaiting_outline_task()

    assert result == {"expired": 0}
    with Session(engine) as session:
        job = session.get(SlideJob, running_id)
    assert job.status == JobStatus.RUNNING.value
    assert workspace.exists()
