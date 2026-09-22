"""The outline conversation must use the same configured runner as revision one."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.config import settings
from app.services.agentic.runner import CodexAgentRunner
from app.services.agentic.sdk_runner import AgentsSdkRunner
from app.models.slides import SlideOutline
from app.services.slides.outline_repository import InMemorySlideOutlineRepository, SlideOutlineRevision
from app.services.slides.planner import PlannerConversationService


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


@pytest.mark.asyncio
async def test_conversation_request_keeps_planner_model_and_evidence_grant(tmp_path, monkeypatch) -> None:
    class RecordingRunner:
        def __init__(self) -> None:
            self.request = None

        async def run(self, request, *, progress_callback=None):
            self.request = request
            raise RuntimeError("fake runner stopped before a model call")

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
    assert '"type": "error"' in frames[-1]
