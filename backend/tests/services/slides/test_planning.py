"""Stage 5 (docs/agents-sdk-migration-plan.md) planner-node tests.

Covers the planner's output guardrail, revision-1 persistence through
``SlidesWorkflowAdapter.post_planning``, the default-off gate on
``settings.agent_planner_enabled``, and the author prompt's outline
instructions. No live model is used anywhere here -- evidence is produced by
the real (offline) extraction script, and the DB is a throwaway migrated
sqlite file, matching the pattern already used by
``tests/services/slides/test_outline_repository_sql.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlmodel import Session, create_engine

from app.config import settings
from app.models.slides import OutlineNode, SlideJob, SlideOutline, SlidesTaskPayload
from app.services.agentic.contracts import AgentExecutionResult
from app.services.slides.adapter import SlidesWorkflowAdapter
from app.services.slides.contracts import JobError
from app.services.slides.evidence import (
    EvidenceError,
    freeze_evidence,
    validate_outline_evidence_references,
)
from app.services.slides.outline_repository import SQLModelSlideOutlineRepository
from app.services.slides.runtime import build_prompt


_EXTRACTION_PATH = Path(__file__).parents[3] / ".agents" / "skills" / "source-document-extraction" / "scripts" / "source_extraction.py"
_SPEC = importlib.util.spec_from_file_location("source_extraction_for_planning_test", _EXTRACTION_PATH)
assert _SPEC and _SPEC.loader
_EXTRACTION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EXTRACTION)


def _freeze_one_block_evidence(workspace: Path) -> str:
    """Extract and freeze one real evidence block; return its id."""

    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "brief.txt"
    source.write_text("An authoritative claim from the source brief.", encoding="utf-8")
    extracted = workspace / "work" / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence_path = workspace / "work" / "evidence.json"
    freeze_evidence(extracted, evidence_path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    return payload["blocks"][0]["id"]


def _outline(evidence_ref: str) -> SlideOutline:
    return SlideOutline(
        title="Q3 policy briefing",
        narrative="A grounded walkthrough of the quarter's policy shifts.",
        nodes=[
            OutlineNode(
                id="intro",
                heading="Introduction",
                intent="Frame the quarter's central question.",
                key_points=["Context", "Stakes"],
                evidence_refs=[evidence_ref],
                emphasis="normal",
                approx_slides=2,
            )
        ],
        total_slides=2,
    )


def _migrated_engine(tmp_path: Path):
    path = tmp_path / "planning.db"
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
        slides_count=8,
        guidance="Focus on reform impact.",
        tone="formal",
    )


def test_validate_outline_evidence_references_accepts_known_ids(tmp_path):
    known_id = _freeze_one_block_evidence(tmp_path)

    validate_outline_evidence_references(_outline(known_id), tmp_path / "work" / "evidence.json")


def test_validate_outline_evidence_references_rejects_an_unknown_id(tmp_path):
    _freeze_one_block_evidence(tmp_path)

    with pytest.raises(EvidenceError, match="unknown evidence ids"):
        validate_outline_evidence_references(_outline("nonexistent-block"), tmp_path / "work" / "evidence.json")


@pytest.mark.asyncio
async def test_post_planning_persists_revision_one_and_rejects_unknown_evidence_ref(tmp_path, monkeypatch):
    engine = _migrated_engine(tmp_path)
    monkeypatch.setattr("app.db.engine", engine)

    job_id = uuid4()
    with Session(engine) as session:
        session.add(_slide_job(job_id))
        session.commit()

    workspace = tmp_path / "workspace"
    known_id = _freeze_one_block_evidence(workspace)

    adapter = SlidesWorkflowAdapter()
    value = SlidesTaskPayload(
        job_id=job_id,
        title="Q3 policy briefing",
        document_ids=[uuid4()],
        slides_count=8,
        guidance="Focus on reform impact.",
        tone="formal",
    )

    good_outline = _outline(known_id)
    await adapter.post_planning(value, AgentExecutionResult(provider_run_id="run-1", output=good_outline), workspace)

    with Session(engine) as session:
        repository = SQLModelSlideOutlineRepository(session)
        latest = await repository.get_latest(job_id)
    assert latest is not None
    assert latest.revision == 1
    assert latest.outline["title"] == "Q3 policy briefing"
    assert latest.session_id == str(job_id)

    bad_outline = _outline("nonexistent-block")
    with pytest.raises(JobError, match="planning"):
        await adapter.post_planning(value, AgentExecutionResult(provider_run_id="run-2", output=bad_outline), workspace)

    # A rejected outline must never become a durable revision.
    with Session(engine) as session:
        repository = SQLModelSlideOutlineRepository(session)
        latest_after_rejection = await repository.get_latest(job_id)
    assert latest_after_rejection.revision == 1


def test_planner_role_defaults_off_and_is_gated_by_settings(monkeypatch):
    """Existing slides jobs must keep completing exactly as today until an
    operator opts in -- ``planner_role`` is the gate ``_execute_workflow``
    checks, mirroring ``extraction_skills``.
    """

    adapter = SlidesWorkflowAdapter()

    monkeypatch.setattr(settings, "agent_planner_enabled", False)
    assert adapter.planner_role is None

    monkeypatch.setattr(settings, "agent_planner_enabled", True)
    assert adapter.planner_role == "presentation_planner"


def test_build_prompt_is_unchanged_without_an_outline():
    from app.models.slides import SlidesTaskPayload as Payload

    request = Payload(job_id=uuid4(), title="Q3", document_ids=[uuid4()], slides_count=8, guidance="g", tone="formal")

    with_outline_none = build_prompt(request, [], font_family="Noto Sans TC", outline=None)
    without_kwarg = build_prompt(request, [], font_family="Noto Sans TC")

    assert with_outline_none == without_kwarg
    assert "Approved outline" not in with_outline_none


def test_build_prompt_instructs_node_order_and_emphasis_when_outline_is_approved():
    from app.models.slides import SlidesTaskPayload as Payload

    request = Payload(job_id=uuid4(), title="Q3", document_ids=[uuid4()], slides_count=8, guidance="g", tone="formal")
    outline = _outline("evidence-1")

    prompt = build_prompt(request, [], font_family="Noto Sans TC", outline=outline)

    assert "Approved outline" in prompt
    assert "Follow the node order below exactly" in prompt
    assert "`intro`" in prompt
    assert "emphasis: normal" in prompt
    assert "standalone line in the speaker" in prompt
    assert "exactly the ID" in prompt
