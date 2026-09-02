"""The allowlisted ``slides`` workflow adapter.

The adapter contains presentation policy; the generic agentic service owns the
thread lifecycle and bounded review/correction loop.  Filesystem and process
heavy work is deliberately moved to worker threads so Taskiq's event loop
remains available to other jobs.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.slides import JobStatus, SlidesTaskPayload, SlidesTaskResult
from app.services.agentic.contracts import BaseWorkflowAdapter, DeterministicValidationError
from app.services.virtual_fs import SharedVolumeDocumentResolver

from .artifacts import (
    cleanup_job,
    create_job_fontconfig,
    create_job_workspace,
    publish_output,
    preflight,
    stage_uploads,
    validate_source_paths,
    verify_output,
)
from .contracts import JobError
from .runtime import build_job_environment, build_prompt


class SlidesWorkflowAdapter(BaseWorkflowAdapter[SlidesTaskPayload, SlidesTaskResult]):
    name = "slides"
    declared_skills = ("source-document-extraction", "pptx-nhi-tw")
    input_type = SlidesTaskPayload
    output_type = SlidesTaskResult
    # The parser below remains authoritative so malformed provider responses
    # fail closed even when an older SDK ignores output_schema.
    review_output_schema = {
        "type": "object",
        "required": ["blocking_findings", "findings", "summary"],
        "properties": {
            "summary": {"type": "string"},
            "blocking_findings": {"type": "array", "items": {"type": "string"}},
            "findings": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }

    @property
    def max_review_rounds(self) -> int:
        return settings.agent_max_review_rounds

    def deterministic_validate(self, value: SlidesTaskPayload, workspace: Path) -> None:
        # Input/preflight checks run in prepare_input.  Generated-deck checks
        # are invoked by the generic loop after every write-capable turn.
        return None

    async def prepare_workspace(self, value: SlidesTaskPayload, workspace: Path) -> None:
        await asyncio.to_thread(create_job_workspace, str(value.job_id), workspace.parent)
        fontconfig = await asyncio.to_thread(create_job_fontconfig, workspace)
        await asyncio.to_thread(
            (workspace / "work" / "slide_context.json").write_text,
            json.dumps({"fontconfig": str(fontconfig) if fontconfig else None}, ensure_ascii=False),
            encoding="utf-8",
        )

    async def prepare_input(self, value: SlidesTaskPayload, workspace: Path) -> None:
        source_paths = await SharedVolumeDocumentResolver(settings.documents_root).resolve_many(value.document_ids)
        validated = await asyncio.to_thread(validate_source_paths, source_paths)
        context_path = workspace / "work" / "slide_context.json"
        context_exists = await asyncio.to_thread(context_path.exists)
        context_text = await asyncio.to_thread(context_path.read_text, encoding="utf-8") if context_exists else None
        context = json.loads(context_text) if context_text else {}
        fontconfig = Path(context["fontconfig"]) if context.get("fontconfig") else None
        font = await asyncio.to_thread(
            preflight,
            validated,
            environment=build_job_environment(fontconfig_file=fontconfig),
        )
        staged_names = await asyncio.to_thread(stage_uploads, workspace, validated)
        context.update({"staged_names": staged_names, "font": font})
        await asyncio.to_thread(context_path.write_text, json.dumps(context, ensure_ascii=False), encoding="utf-8")

    async def semantic_review_context(self, value: SlidesTaskPayload, workspace: Path) -> Any:
        return {
            "requested_title": value.title,
            "requested_slide_count": value.slides_count,
            "artifacts": [
                "output/presentation.pptx",
                "work/intermediate/evidence_map.json",
                "work/intermediate/content_check.json",
                "work/intermediate/review_report.json",
                "work/intermediate/qa_report.json",
                "work/rendered/final/*.png",
            ]
        }

    def post_author_completion_check(
        self,
        value: SlidesTaskPayload,
        result: Any,
        workspace: Path,
    ) -> None:
        del value, result
        if not (workspace / "output" / "presentation.pptx").is_file():
            raise JobError("author", "presentation artifact was not generated")

    async def build_prompt(self, value: SlidesTaskPayload, workspace: Path, *, semantic_review_context: Any = None, revision_feedback: str | None = None) -> str:
        context_path = workspace / "work" / "slide_context.json"
        context = json.loads(await asyncio.to_thread(context_path.read_text, encoding="utf-8"))
        prompt = build_prompt(value, context.get("staged_names", []), font_family=context.get("font"))
        if revision_feedback:
            prompt += (
                "\n\n## Corrective revision instructions\n"
                "This is a corrective revision, not a regeneration. The workspace already contains "
                "the previous candidate presentation and its evidence/QA artifacts. Modify the "
                "existing presentation and affected artifacts in place. Preserve the requested "
                f"title exactly as `{value.title}` and the requested slide count of {value.slides_count}; "
                "do not redesign or rewrite unaffected slides.\n"
                "Address every blocking finding below. After changes, regenerate affected renders, "
                "update EvidenceMap, regenerate content_check/review_report/qa_report, and rerun all "
                "deterministic checks. Before finishing, verify each blocking finding individually "
                "and state which slide or artifact change resolves it.\n\n"
                "## Blocking review findings to correct\n"
                + revision_feedback
            )
        # Preserve the job-local brief used by the original slide runtime;
        # this is also useful when diagnosing a retained failed workspace.
        await asyncio.to_thread((workspace / "work" / "prompt.md").write_text, prompt, encoding="utf-8")
        return prompt

    def build_review_prompt(self, value: SlidesTaskPayload, workspace: Path, audit: Any, review_context: Any) -> str:
        requested_title = getattr(value, "title", "") or "(not provided)"
        return (
            "Perform semantic review of the generated presentation now. Do not modify any file. "
            "Inspect the PPTX and validation artifacts listed below. "
            f"The authoritative requested presentation title is `{requested_title}`; do not classify "
            "that exact requested title as template/test residue solely because it looks unusual. "
            "Return ONLY valid JSON with exactly these top-level fields: summary (string), "
            "blocking_findings (array of strings), findings (array of strings). A blocking finding "
            "must prevent publication. Each finding string must include concrete slide/artifact "
            "evidence and a correction recommendation. "
            f"Artifacts: {json.dumps(review_context or {}, ensure_ascii=False)}"
        )

    async def validate_generated(self, value: SlidesTaskPayload, workspace: Path) -> None:
        try:
            await asyncio.to_thread(
                verify_output,
                workspace,
                expected_slide_count=value.slides_count,
            )
        except Exception as exc:
            # ``verify_output`` reports expected, correctable artifact findings
            # as JobError. Other exceptions indicate a validator/runtime fault
            # and must remain terminal failures.
            from .contracts import JobError

            if isinstance(exc, JobError):
                raise DeterministicValidationError(str(exc)) from exc
            raise

    def parse_review(self, response: str | None, workspace: Path) -> dict[str, Any]:
        semantic_review_path = workspace / "work" / "intermediate" / "semantic_review.json"
        semantic_review_path.unlink(missing_ok=True)
        if not response or not isinstance(response, str):
            raise ValueError("semantic review response was empty")
        text = response.strip()
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            review = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic review response was malformed") from exc
        if not isinstance(review, dict) or set(review) != {"summary", "blocking_findings", "findings"}:
            raise ValueError("semantic review response was malformed")
        if not isinstance(review.get("summary"), str):
            raise ValueError("semantic review response was malformed")
        if not isinstance(review.get("blocking_findings"), list) or not isinstance(review.get("findings"), list):
            raise ValueError("semantic review response was malformed")
        if any(not isinstance(item, str) for item in review["blocking_findings"] + review["findings"]):
            raise ValueError("semantic review response was malformed")
        semantic_review_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = semantic_review_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(semantic_review_path)
        return review

    def review_has_blocking_findings(self, review: dict[str, Any]) -> bool:
        return bool(review.get("blocking_findings"))

    def revision_feedback(self, value: SlidesTaskPayload, review: dict[str, Any], result: Any) -> str:
        findings = review.get("blocking_findings", [])
        # Advisory findings remain in semantic_review.json for diagnosis, but
        # only publication blockers consume the bounded correction budget.
        return json.dumps(findings, ensure_ascii=False)[:12000]

    async def publish(self, value: SlidesTaskPayload, result: Any, workspace: Path) -> SlidesTaskResult:
        response = getattr(result, "response", None)
        if response:
            await asyncio.to_thread(
                (workspace / "work" / "final_message.txt").write_text,
                response,
                encoding="utf-8",
            )
        verified = await asyncio.to_thread(
            verify_output,
            workspace,
            expected_slide_count=value.slides_count,
        )
        published = await asyncio.to_thread(publish_output, verified, str(value.job_id), settings.agent_output_root)
        return SlidesTaskResult(
            job_id=value.job_id,
            status=JobStatus.COMPLETED,
            artifact_key=published.name,
            download_filename=self._download_filename(value.title),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

    async def cleanup(self, value: SlidesTaskPayload, workspace: Path, *, success: bool) -> None:
        if success or not settings.agent_keep_workspace_on_failure:
            await asyncio.to_thread(cleanup_job, workspace)

    @staticmethod
    def _download_filename(title: str) -> str:
        cleaned = "".join(char for char in title.strip() if char.isalnum() or char in " ._-")
        cleaned = " ".join(cleaned.split()).strip(" ._-")[:120]
        return f"{cleaned or 'presentation'}.pptx"


slides_adapter = SlidesWorkflowAdapter()

# Registration lives beside the concrete adapter.  Keeping it here ensures a
# direct adapter import is valid while the generic package can remain free of
# an eager circular import.
from app.services.agentic.registry import workflow_registry

if not workflow_registry.is_registered("slides"):
    workflow_registry.register("slides", slides_adapter)

__all__ = ["SlidesWorkflowAdapter", "slides_adapter"]
