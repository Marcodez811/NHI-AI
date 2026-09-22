"""Interactive outline-planning conversation: the human-in-the-loop half of Stage 5.

``agents.run`` (``app/tasks/agents.py``) only ever produces the planner's first
outline revision and then pauses -- the worker must never block on a human. Every
turn after that (a human proposing a change, the planner replying with a revised
structure) happens synchronously over an HTTP request instead, through
``POST /slides/jobs/{job_id}/outline/messages`` (``app/api/routes/slides.py``).

This module is the service behind that route. It reuses the exact seam the worker
uses for planning -- one ``AgentExecutionRequest`` with ``output_type=SlideOutline``
and a read-only grant on the job's frozen ``work/evidence.json`` -- so a follow-up
turn sees byte-identical evidence to the one that produced revision 1, and runs the
same output guardrail (``validate_outline_evidence_references``) before a revised
outline is ever persisted or shown to a human.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.models.slides import SlideOutline
from app.services.agentic.contracts import AgentExecutionRequest, AgentRunner
from app.services.agentic.runner import CodexAgentRunner, CodexRunner, WorkflowExecutionError, safe_error
from app.services.agentic.sdk_runner import AgentsSdkRunner
from app.services.slides.adapter import slides_adapter
from app.services.slides.evidence import EvidenceError, build_planner_evidence_block, validate_outline_evidence_references
from app.services.slides.outline_repository import SlideOutlineRepository, SlideOutlineRevision


def _sse(event_type: str, payload: dict[str, object]) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


def _conversation_prompt(latest: SlideOutlineRevision, message: str, *, runner_name: str) -> str:
    """Return the human-turn instructions, worded for how evidence reaches the model.

    Mirrors ``SlidesWorkflowAdapter.build_planning_prompt``'s ``runner_name`` split
    (app/services/slides/adapter.py): the "codex" text is unchanged and still describes
    a mounted, read-only ``work/evidence.json``; the "agents" text instead points at the
    inlined ``<evidence>`` block this turn's request carries, since that stage has no
    file or shell access at all.
    """

    if runner_name == "agents":
        evidence_access = (
            "The frozen evidence is provided in the `<evidence>` block of this message, not "
            "through a workspace file -- this stage has no file or shell access. Return one "
            "revised, complete outline that addresses the human's message."
        )
    else:
        evidence_access = (
            "Read the frozen evidence store at "
            "`work/evidence.json` (mounted read-only; you cannot write to it or to any "
            "other workspace path) and return one revised, complete outline that "
            "addresses the human's message."
        )
    return (
        "Continue the outline-planning conversation for this presentation. The "
        "previously proposed outline is given below as JSON; a human has replied "
        f"with a follow-up instruction. {evidence_access} Ground every node in "
        "`evidence_refs` ids copied verbatim from that store -- an id it does not "
        "contain will be rejected before this reaches the human again.\n\n"
        f"## Previous outline (revision {latest.revision})\n"
        f"{json.dumps(latest.outline, ensure_ascii=False)}\n\n"
        f"## Human message\n{message}\n"
    )


class PlannerConversationService:
    """Continue a paused planning conversation and persist the next revision.

    ``runner`` is injectable so tests exercise this against a fake that never
    reaches the network; production resolves a real ``AgentRunner`` lazily
    (on first use, not at construction) the same way
    ``app.services.chat.responder.ResponseService`` lazily builds its client,
    so importing this module never requires provider credentials.
    """

    def __init__(self, *, runner: AgentRunner | None = None, jobs_root: Path | None = None) -> None:
        self._runner = runner
        self._jobs_root = jobs_root

    @property
    def jobs_root(self) -> Path:
        return self._jobs_root or settings.agent_jobs_root

    @property
    def runner(self) -> AgentRunner:
        if self._runner is None:
            if slides_adapter.planner_runner == "agents":
                self._runner = AgentsSdkRunner(
                    model=slides_adapter.planner_model,
                    reasoning_effort=slides_adapter.planner_reasoning_effort,
                    timeout_seconds=float(settings.agent_timeout_minutes) * 60,
                    heartbeat_seconds=settings.agent_heartbeat_seconds,
                    litellm_api_keys={
                        "gemini": settings.gemini_api_key,
                        "anthropic": settings.anthropic_api_key,
                        "openai": settings.openai_api_key,
                    },
                )
            elif slides_adapter.planner_runner == "codex":
                self._runner = CodexAgentRunner(
                    CodexRunner(
                        model=settings.agent_planner_model or settings.agent_default_model,
                        reasoning_effort=(
                            settings.agent_planner_reasoning_effort or settings.agent_default_reasoning_effort
                        ),
                        api_key=settings.openai_api_key,
                        timeout_seconds=float(settings.agent_timeout_minutes) * 60,
                        heartbeat_seconds=settings.agent_heartbeat_seconds,
                        require_process_isolation=settings.agent_require_process_isolation,
                    )
                )
            else:
                raise WorkflowExecutionError("Unknown planner runner")
        return self._runner

    async def continue_conversation(
        self,
        *,
        job_id: UUID,
        message: str,
        latest: SlideOutlineRevision,
        outline_repository: SlideOutlineRepository,
    ) -> AsyncGenerator[str, None]:
        """Yield SSE frames for one planner turn and persist the resulting revision.

        Mirrors ``app.api.routes.chat.stream_chat``'s event vocabulary
        (``status`` / ``text_delta`` / ``done`` / ``error``) so the frontend can
        reuse the same SSE parsing it already has for chat.
        """

        yield _sse("status", {"phase": "planning"})
        workspace = (self.jobs_root / str(job_id)).resolve()
        evidence_path = workspace / "work" / "evidence.json"
        runner_name = slides_adapter.planner_runner
        instructions = _conversation_prompt(latest, message, runner_name=runner_name)
        if runner_name == "agents":
            # Same "no grants -> no sandbox" shape as the worker's first-revision
            # activation (_execute_planning, app/services/agentic/service.py): the
            # compact evidence goes into the prompt instead of a filesystem grant.
            try:
                evidence_block = await asyncio.to_thread(
                    build_planner_evidence_block,
                    workspace,
                    max_chars=settings.agent_planner_max_evidence_chars,
                )
            except EvidenceError as exc:
                yield _sse(
                    "error",
                    {"code": "planner_evidence_unavailable", "message": safe_error(str(exc), "無法讀取佐證資料，請稍後再試。")},
                )
                return
            request = AgentExecutionRequest(
                run_id=f"{job_id}-outline-{latest.revision + 1}",
                node_id="planning",
                role=slides_adapter.planner_role or "presentation_planner",
                model=slides_adapter.planner_model,
                reasoning_effort=slides_adapter.planner_reasoning_effort,
                workspace=workspace,
                prompt=evidence_block,
                instructions=instructions,
                output_type=SlideOutline,
            )
        else:
            request = AgentExecutionRequest(
                run_id=f"{job_id}-outline-{latest.revision + 1}",
                node_id="planning",
                role=slides_adapter.planner_role or "presentation_planner",
                model=slides_adapter.planner_model,
                reasoning_effort=slides_adapter.planner_reasoning_effort,
                workspace=workspace,
                prompt=instructions,
                output_type=SlideOutline,
                read_only_paths=(evidence_path,),
                restrict_workspace=True,
            )
        try:
            result = await self.runner.run(request)
        except Exception as exc:
            yield _sse(
                "error",
                {"code": "planner_unavailable", "message": safe_error(str(exc), "規劃助理暫時無法使用，請稍後再試。")},
            )
            return
        outline = result.output
        if not isinstance(outline, SlideOutline):
            yield _sse("error", {"code": "planner_invalid_output", "message": "規劃助理未回傳有效的大綱結構。"})
            return
        try:
            validate_outline_evidence_references(outline, evidence_path)
        except EvidenceError:
            yield _sse("error", {"code": "planner_invalid_evidence", "message": "大綱引用了不存在的佐證資料，請重新提出要求。"})
            return
        revision = await outline_repository.create_next_revision(job_id, outline, session_id=latest.session_id)
        if result.response:
            yield _sse("text_delta", {"text": result.response})
        yield _sse(
            "done",
            {"revision": revision.revision, "outline": outline.model_dump(mode="json")},
        )


__all__ = ["PlannerConversationService"]
