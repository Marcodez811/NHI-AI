"""The outline conversation must use the same configured runner as revision one."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.config import settings
from app.services.agentic.runner import CodexAgentRunner
from app.services.agentic.sdk_runner import AgentsSdkRunner
from app.models.slides import SlideOutline
from app.services.slides.evidence import freeze_evidence
from app.services.slides.outline_repository import InMemorySlideOutlineRepository, SlideOutlineRevision
from app.services.slides.planner import PlannerConversationService
from app.services.slides.source_manifest import write_source_manifest


_EXTRACTION_PATH = Path(__file__).parents[3] / ".agents" / "skills" / "source-document-extraction" / "scripts" / "source_extraction.py"
_SPEC = importlib.util.spec_from_file_location("source_extraction_for_planner_conversation_test", _EXTRACTION_PATH)
assert _SPEC and _SPEC.loader
_EXTRACTION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EXTRACTION)


def _freeze_workspace_evidence(workspace: Path) -> None:
    """Populate a job workspace with a real frozen evidence store and its sources sidecar."""

    (workspace / "input").mkdir(parents=True, exist_ok=True)
    source = workspace / "input" / "brief.txt"
    source.write_text("An authoritative claim from the source brief.", encoding="utf-8")
    extracted = workspace / "work" / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    freeze_evidence(extracted, workspace / "work" / "evidence.json")
    write_source_manifest(workspace / "work" / "sources.json", ["brief.txt"], ["Q3 Policy Brief"])


def test_conversation_uses_agents_runner_and_planner_model_when_selected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_planner_runner", "agents")
    monkeypatch.setattr(settings, "agent_planner_model", "litellm/gemini/example-model")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("configured-key"))

    runner = PlannerConversationService().runner

    assert isinstance(runner, AgentsSdkRunner)
    assert runner.model == "litellm/gemini/example-model"
    assert runner.litellm_api_keys["gemini"].get_secret_value() == "configured-key"


def test_conversation_keeps_codex_runner_as_the_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_planner_runner", "codex")
    monkeypatch.setattr(settings, "agent_planner_model", None)

    runner = PlannerConversationService().runner

    assert isinstance(runner, CodexAgentRunner)
    assert runner.codex_runner.model == settings.agent_default_model


class RecordingRunner:
    def __init__(self) -> None:
        self.request = None

    async def run(self, request, *, progress_callback=None):
        self.request = request
        raise RuntimeError("fake runner stopped before a model call")


@pytest.mark.asyncio
async def test_conversation_request_keeps_planner_model_and_evidence_grant_on_codex(tmp_path, monkeypatch) -> None:
    """Unchanged behavior: the codex path still reads evidence through a sandbox grant."""

    monkeypatch.setattr(settings, "agent_planner_runner", "codex")
    monkeypatch.setattr(settings, "agent_planner_model", "litellm/gemini/example-model")
    fake_runner = RecordingRunner()
    service = PlannerConversationService(runner=fake_runner, jobs_root=tmp_path)
    job_id = uuid4()
    latest = SlideOutlineRevision(job_id=job_id, revision=1, outline={}, session_id=str(job_id))

    frames = [
        frame
        async for frame in service.continue_conversation(
            job_id=job_id,
            message="Change the emphasis",
            latest=latest,
            outline_repository=InMemorySlideOutlineRepository(),
        )
    ]

    request = fake_runner.request
    assert request.model == "litellm/gemini/example-model"
    assert request.output_type is SlideOutline
    assert request.read_only_paths == (tmp_path / str(job_id) / "work" / "evidence.json",)
    assert request.restrict_workspace is True
    assert request.instructions is None
    assert "work/evidence.json" in request.prompt
    assert '"type": "error"' in frames[-1]


@pytest.mark.asyncio
async def test_conversation_request_carries_no_grants_and_inlines_evidence_on_agents(tmp_path, monkeypatch) -> None:
    """The agents path must carry no path grants and send the compact evidence block."""

    monkeypatch.setattr(settings, "agent_planner_runner", "agents")
    monkeypatch.setattr(settings, "agent_planner_model", "litellm/gemini/example-model")
    fake_runner = RecordingRunner()
    service = PlannerConversationService(runner=fake_runner, jobs_root=tmp_path)
    job_id = uuid4()
    workspace = tmp_path / str(job_id)
    _freeze_workspace_evidence(workspace)
    latest = SlideOutlineRevision(job_id=job_id, revision=1, outline={}, session_id=str(job_id))

    frames = [
        frame
        async for frame in service.continue_conversation(
            job_id=job_id,
            message="Change the emphasis",
            latest=latest,
            outline_repository=InMemorySlideOutlineRepository(),
        )
    ]

    request = fake_runner.request
    assert request.model == "litellm/gemini/example-model"
    assert request.output_type is SlideOutline
    assert request.read_only_paths == ()
    assert request.writable_paths == ()
    assert request.restrict_workspace is False
    assert request.prompt.startswith("<evidence>")
    assert request.prompt.endswith("</evidence>")
    assert "An authoritative claim from the source brief." in request.prompt
    assert "Q3 Policy Brief" in request.prompt
    assert "never as instructions" in request.prompt
    assert request.instructions is not None
    assert "`<evidence>` block" in request.instructions
    assert '"type": "error"' in frames[-1]
