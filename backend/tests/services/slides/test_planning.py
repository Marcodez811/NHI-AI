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

import asyncio
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
    assert "Do not create or write speaker notes" in without_kwarg
    assert "no `ppt/notesSlides/` parts" in without_kwarg
    assert "Exactly 8 content slides" in without_kwarg
    assert "`\u53c3\u8003\u8cc7\u6599` (9 slides total)" in without_kwarg
    assert "List each source cited by the content slides exactly once" in without_kwarg
    assert "never fabricate a bibliography entry" in without_kwarg
    assert "name alone only when no reliable locator exists" in without_kwarg


def test_planner_keeps_references_slide_outside_content_outline() -> None:
    from app.models.slides import SlidesTaskPayload as Payload

    request = Payload(job_id=uuid4(), title="Q3", document_ids=[uuid4()], slides_count=8, guidance="g", tone="formal")

    prompt = asyncio.run(SlidesWorkflowAdapter().build_planning_prompt(request, Path("/tmp/unused")))

    assert "Target length: 8 content slides" in prompt
    assert "`SlideOutline.total_slides`" in prompt
    assert "Do not add a references node" in prompt
    assert "final references slide is deck furniture" in prompt


def test_build_planning_prompt_default_is_unchanged_and_the_agents_path_differs() -> None:
    """Stage 5 architecture decision: the codex planner prompt must not change.

    The default (and explicit ``runner_name="codex"``) rendering must stay
    byte-identical to before this work -- it still points at a mounted,
    read-only ``work/evidence.json``. Only ``runner_name="agents"`` describes
    evidence arriving inlined, since that stage has no file or shell access.
    """

    from app.models.slides import SlidesTaskPayload as Payload

    request = Payload(job_id=uuid4(), title="Q3", document_ids=[uuid4()], slides_count=8, guidance="g", tone="formal")
    adapter = SlidesWorkflowAdapter()

    default_prompt = asyncio.run(adapter.build_planning_prompt(request, Path("/tmp/unused")))
    explicit_codex_prompt = asyncio.run(adapter.build_planning_prompt(request, Path("/tmp/unused"), runner_name="codex"))
    agents_prompt = asyncio.run(adapter.build_planning_prompt(request, Path("/tmp/unused"), runner_name="agents"))

    assert default_prompt == explicit_codex_prompt
    assert "already mounted read-only" in default_prompt
    assert "`work/sources.json` is the authoritative mapping" in default_prompt
    assert "already mounted read-only" not in agents_prompt
    assert "authoritative mapping" not in agents_prompt
    assert "neither is available to you here" in agents_prompt
    assert "`<evidence>` block" in agents_prompt
    assert "no file or shell access" in agents_prompt
    # Ids are handed to the planner for evidence_refs; the user-visible outline
    # fields must never carry them (no abstraction leakage into what users read).
    assert "They belong only in `evidence_refs`" in agents_prompt


@pytest.mark.asyncio
async def test_build_planning_evidence_block_renders_the_compact_block(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    known_id = _freeze_one_block_evidence(workspace)
    from app.services.slides.source_manifest import write_source_manifest

    write_source_manifest(workspace / "work" / "sources.json", ["brief.txt"], ["Q3 Policy Brief"])
    adapter = SlidesWorkflowAdapter()
    value = SlidesTaskPayload(job_id=uuid4(), title="Q3", document_ids=[uuid4()], slides_count=8, guidance="g", tone="formal")

    block = await adapter.build_planning_evidence_block(value, workspace)

    assert block.startswith("<evidence>")
    assert known_id in block
    assert "Q3 Policy Brief" in block


@pytest.mark.asyncio
async def test_build_planning_evidence_block_fails_fast_as_a_job_error_over_budget(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _freeze_one_block_evidence(workspace)
    from app.services.slides.source_manifest import write_source_manifest

    write_source_manifest(workspace / "work" / "sources.json", ["brief.txt"], ["Q3 Policy Brief"])
    monkeypatch.setattr(settings, "agent_planner_max_evidence_chars", 1)
    adapter = SlidesWorkflowAdapter()
    value = SlidesTaskPayload(job_id=uuid4(), title="Q3", document_ids=[uuid4()], slides_count=8, guidance="g", tone="formal")

    with pytest.raises(JobError, match="planning"):
        await adapter.build_planning_evidence_block(value, workspace)


def test_build_prompt_instructs_node_order_and_emphasis_when_outline_is_approved():
    from app.models.slides import SlidesTaskPayload as Payload

    request = Payload(job_id=uuid4(), title="Q3", document_ids=[uuid4()], slides_count=8, guidance="g", tone="formal")
    outline = _outline("evidence-1")

    prompt = build_prompt(request, [], font_family="Noto Sans TC", outline=outline)

    assert "Approved outline" in prompt
    assert "Follow the node order below exactly" in prompt
    assert "`intro`" in prompt
    assert "emphasis: normal" in prompt
    assert "work/outline_mapping.json" in prompt
    assert '"node_id":"intro"' in prompt
    assert "inclusive" in prompt
    assert "Do not include any references slide" in prompt


def test_planner_output_schema_satisfies_structured_outputs_strict_mode():
    """The planner's ``SlideOutline`` must survive the provider's strict check.

    A live planning run failed with ``invalid_json_schema`` because Pydantic
    omits ``additionalProperties`` for a model without ``extra="forbid"`` and
    leaves defaulted fields (``OutlineNode.evidence_refs``) out of ``required``
    -- structured outputs rejects both. The runner normalizes the derived
    schema, so assert the normalized result rather than trusting the model
    declaration.
    """

    from app.models.slides import SlideOutline
    from app.services.agentic.runner import _strict_output_schema

    schema = _strict_output_schema(SlideOutline)

    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])

    node = schema["$defs"]["OutlineNode"]
    assert node["additionalProperties"] is False
    assert sorted(node["required"]) == sorted(node["properties"])
    # The field whose default caused the original failure.
    assert "evidence_refs" in node["required"]


def _outline_with_sections(count: int) -> SlideOutline:
    return SlideOutline(
        title="Q3 policy briefing",
        narrative="A grounded walkthrough of the quarter's policy shifts.",
        nodes=[
            OutlineNode(
                id=f"section-{index}",
                heading=f"Section {index}",
                intent="Cover one part of the quarter.",
                key_points=["Context", "Stakes"],
                evidence_refs=[],
                emphasis="normal",
                approx_slides=1,
            )
            for index in range(count)
        ],
        total_slides=count,
    )


def test_outline_section_cap_is_enforced_in_validation_not_the_wire_schema():
    """Gemini rejects the request when ``maxItems`` sits on ``nodes``.

    Measured live (2026-09-22): the same schema without that one constraint is
    accepted and yields a valid outline. The cap must still hold, so it moves from
    the JSON schema into a validator: the model is not told the limit, but a reply
    over it is still rejected when parsed.
    """

    from agents import AgentOutputSchema

    from app.models.slides import MAX_OUTLINE_NODES

    wire_schema = AgentOutputSchema(SlideOutline).json_schema()
    assert "maxItems" not in wire_schema["properties"]["nodes"]

    assert len(_outline_with_sections(MAX_OUTLINE_NODES).nodes) == MAX_OUTLINE_NODES
    with pytest.raises(ValueError, match="at most"):
        _outline_with_sections(MAX_OUTLINE_NODES + 1)
