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
from app.services.agentic.contracts import (
    AgentReasoningEffort,
    BaseWorkflowAdapter,
    DeterministicValidationError,
    ValidationInfrastructureError,
    ReviewFindingStatus,
    ReviewOutcome,
    ReviewSeverity,
)
from app.services.virtual_fs import SharedVolumeDocumentResolver

from .artifacts import (
    cleanup_job,
    create_job_fontconfig,
    create_job_workspace,
    publish_output,
    preflight,
    stage_uploads,
    validate_source_paths,
    verify_output,  # compatibility export for legacy callers; validator owns new checks
)
from .contracts import JobError
from .runtime import build_job_environment, build_prompt


_VALIDATION_ORIGINS = frozenset({"candidate", "infrastructure"})


def _validation_finding_payload(finding: Any) -> dict[str, Any]:
    """Return a JSON-safe diagnostic without depending on validator internals."""

    as_dict = getattr(finding, "as_dict", None)
    if callable(as_dict):
        payload = as_dict()
    elif isinstance(finding, dict):
        payload = dict(finding)
    else:
        payload = {"code": str(getattr(finding, "code", "validation_failure")), "message": str(finding)}
    if not isinstance(payload, dict):
        payload = {"code": "validation_failure", "message": str(payload)}
    return payload


def _validation_finding_origin(finding: Any, payload: dict[str, Any]) -> str:
    """Read the validator's explicit origin; old findings remain candidates."""

    origin = getattr(finding, "origin", None)
    if origin is None:
        origin = payload.get("origin")
    if origin is None:
        # Compatibility with validators emitted before ``origin`` was added.
        # This is deliberately not inferred from a stage or diagnostic code.
        origin = "candidate"
    origin = str(getattr(origin, "value", origin)).strip().lower()
    if origin not in _VALIDATION_ORIGINS:
        raise ValidationInfrastructureError(
            "deterministic validator returned an invalid finding origin",
            diagnostic_codes=("validation_origin_invalid",),
        )
    return origin


class SlidesWorkflowAdapter(BaseWorkflowAdapter[SlidesTaskPayload, SlidesTaskResult]):
    name = "slides"
    # Skills are staged once, but each fresh activation receives only the
    # bundle required for its responsibility.
    declared_skills = ("source-document-extraction", "pptx-nhi-tw", "semantic-slide-review")
    extraction_skills = ("source-document-extraction",)
    author_skills = ("pptx-nhi-tw",)
    reviewer_skills = ("semantic-slide-review",)
    independent_semantic_review = True
    stage_isolation = True
    input_type = SlidesTaskPayload
    output_type = SlidesTaskResult

    @property
    def author_model(self) -> str:
        return settings.agent_author_model or settings.agent_default_model

    @property
    def reviewer_model(self) -> str:
        return settings.agent_reviewer_model or settings.agent_default_model

    @property
    def author_reasoning_effort(self) -> AgentReasoningEffort:
        return settings.agent_author_reasoning_effort or settings.agent_default_reasoning_effort

    @property
    def reviewer_reasoning_effort(self) -> AgentReasoningEffort:
        return settings.agent_reviewer_reasoning_effort or settings.agent_default_reasoning_effort

    @property
    def extraction_model(self) -> str:
        return settings.agent_extraction_model or settings.agent_default_model

    @property
    def extraction_reasoning_effort(self) -> AgentReasoningEffort:
        return settings.agent_extraction_reasoning_effort or settings.agent_default_reasoning_effort

    # The parser below remains authoritative so malformed provider responses
    # fail closed even when an older SDK ignores output_schema.
    review_output_schema = {
        "type": "object",
        "required": ["findings", "summary"],
        "properties": {
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["severity", "category", "slide_number", "claim", "judgement", "evidence_refs", "reason", "correction"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["blocking", "advisory"]},
                        "category": {
                            "type": "string",
                            "enum": [
                                "unsupported_claim",
                                "contradicted_claim",
                                "misleading_synthesis",
                                "material_omission",
                                "unreadable_claim",
                                "other",
                            ],
                        },
                        "slide_number": {"type": ["integer", "null"], "minimum": 1},
                        "claim": {"type": "string"},
                        "judgement": {
                            "type": "string",
                            "enum": ["supported", "unsupported", "contradicted", "misleading", "unclear"],
                        },
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                        "correction": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }

    @property
    def max_author_attempts(self) -> int:
        # Five write-capable author activations is the hard slides-workflow
        # ceiling; deployment settings can only lower it.
        return min(5, settings.agent_max_author_attempts)

    @property
    def max_review_rounds(self) -> int:
        """Compatibility alias for older coordinator/test integrations."""

        return self.max_author_attempts

    @property
    def review_stagnation_limit(self) -> int:
        return settings.agent_review_stagnation_limit

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
        context.update({
            "staged_names": staged_names,
            "source_paths": [str(path) for path in validated],
            "font": font,
        })
        await asyncio.to_thread(context_path.write_text, json.dumps(context, ensure_ascii=False), encoding="utf-8")

    def stage_hidden_paths(self, stage: str, workspace: Path) -> tuple[Path, ...]:
        """Return originals and review history hidden from downstream stages."""

        if stage == "extraction":
            return ()
        hidden: list[Path] = [workspace / "input"]
        try:
            context = json.loads((workspace / "work" / "slide_context.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            context = {}
        hidden.extend(Path(path) for path in context.get("source_paths", ()) if isinstance(path, str))
        hidden.extend(
            (
                workspace / "work" / "intermediate" / "semantic_review_history.json",
                workspace / "work" / "intermediate" / "semantic_review.json",
            )
        )
        return tuple(hidden)

    def stage_read_only_paths(self, stage: str, workspace: Path) -> tuple[Path, ...]:
        """Expose only the immutable inputs needed by each stage."""

        if stage == "extraction":
            return (workspace / "input",)
        if stage == "author":
            return (
                workspace / "work" / "evidence.json",
                workspace / "work" / "extracted" / "assets",
                workspace / "template",
            )
        if stage == "reviewer":
            return (
                workspace / "work" / "evidence.json",
                workspace / "work" / "intermediate" / "deck_snapshot.json",
                workspace / "work" / "rendered" / "final",
            )
        return ()

    def stage_writable_paths(self, stage: str, workspace: Path) -> tuple[Path, ...]:
        """Limit write-capable agents to their owned artifact directories."""

        if stage == "extraction":
            return (workspace / "work" / "extracted",)
        if stage == "author":
            return (
                workspace / "output",
                workspace / "work" / "rendered" / "preview",
                workspace / "work" / "rendered" / "preview-pdf",
                workspace / "work" / "images",
            )
        return ()

    async def build_extraction_prompt(self, value: SlidesTaskPayload, workspace: Path) -> str:
        context = json.loads(await asyncio.to_thread((workspace / "work" / "slide_context.json").read_text, encoding="utf-8"))
        sources = "\n".join(f"- input/{name}" for name in context.get("staged_names", ()))
        return f"""# Source extraction stage

Extract each staged source below into `work/extracted/` using `$source-document-extraction`.
This is the only stage allowed to open the original source files. Preserve every factual
block, table, figure, locator, warning, and copied asset without curating claims.

{sources}

Run the extraction validator before finishing. Do not create slides, interpret evidence,
or write files outside `work/extracted/`. The backend will consolidate the artifacts into
the frozen `work/evidence.json` store after this activation.
"""

    def post_extraction(self, value: SlidesTaskPayload, workspace: Path) -> None:
        """Validate and atomically freeze the extractor's output."""

        del value
        from .evidence import EvidenceError, consolidate_evidence

        try:
            consolidate_evidence(workspace / "work" / "extracted", workspace / "work" / "evidence.json")
        except EvidenceError as exc:
            raise JobError("extraction", "extraction artifacts could not be consolidated") from exc

    async def semantic_review_context(self, value: SlidesTaskPayload, workspace: Path) -> Any:
        return {
            "requested_title": value.title,
            "requested_slide_count": value.slides_count,
            "artifacts": [
                "work/evidence.json",
                "work/intermediate/deck_snapshot.json",
                "work/rendered/final/*.png",
            ],
            "review_inputs": [
                "work/evidence.json",
                "work/intermediate/deck_snapshot.json",
                "work/rendered/final/*.png",
            ],
        }

    async def build_prompt(self, value: SlidesTaskPayload, workspace: Path, *, semantic_review_context: Any = None, revision_feedback: str | None = None) -> str:
        context_path = workspace / "work" / "slide_context.json"
        context = json.loads(await asyncio.to_thread(context_path.read_text, encoding="utf-8"))
        prompt = build_prompt(value, context.get("staged_names", []), font_family=context.get("font"))
        if revision_feedback:
            prompt += (
                "\n\n## Corrective revision instructions\n"
                "This is a corrective revision, not a regeneration. The workspace already contains "
                "the previous candidate presentation and its deterministic validation artifacts. Modify the "
                "existing presentation and affected artifacts in place. Preserve the requested "
                f"title exactly as `{value.title}` and the requested slide count of {value.slides_count}; "
                "do not redesign or rewrite unaffected slides.\n"
                "Address every blocking finding below. After changes, regenerate affected preview renders, "
                "then stop; the backend validator owns content_check.json and deck_snapshot.json. "
                "The frozen evidence.json and work/extracted/ tree are read-only and authoritative. "
                "Preserve or repair audience-facing source footers and speaker-note references; never add "
                "EvidenceStore names, paths, hashes, or block IDs to the delivered presentation. "
                "Before finishing, verify each blocking finding individually "
                "and state which slide or artifact change resolves it.\n\n"
                "## Blocking review findings to correct\n"
                + revision_feedback
            )
        # Preserve the job-local brief used by the original slide runtime;
        # this is also useful when diagnosing a retained failed workspace.
        await asyncio.to_thread((workspace / "work" / "prompt.md").write_text, prompt, encoding="utf-8")
        return prompt

    def build_review_prompt(
        self,
        value: SlidesTaskPayload,
        workspace: Path,
        audit: Any,
        review_context: Any,
        previous_review: ReviewOutcome | None = None,
    ) -> str:
        del audit, previous_review
        requested_title = getattr(value, "title", "") or "(not provided)"
        return (
            "Perform a fresh semantic-only review of the current candidate. Do not modify any file. "
            "Use only the frozen evidence.json, deck_snapshot.json, and final PNG renders listed below. "
            "Do not open source PDF, DOCX, Markdown, or text files. Do not inspect prior semantic-review "
            "responses, review history, author prompts, or hidden workspace files. "
            f"The authoritative requested presentation title is `{requested_title}`; do not classify "
            "that exact requested title as template/test residue solely because it looks unusual.\n"
            "Return ONLY valid JSON with exactly these top-level fields: summary (string), "
            "findings (array). Every finding must include severity (blocking or advisory), category, "
            "slide_number (integer or null), claim, judgement, evidence_refs (array of evidence IDs), "
            "reason, and correction. Use an empty findings array when there are no issues. A blocking finding "
            "must prevent publication because it is materially incorrect, misleading, unsupported, "
            "missing, unreadable, or internally inconsistent. Treat ordinary rounding or wording "
            "polish as advisory unless it changes a threshold, comparison, denominator, or meaning. "
            "Every factual claim must be supported by one or more evidence_refs. For a contradiction, "
            "identify the correct value in reason and provide a concrete correction. "
            "Review citations as part of factual quality: factual slides, figures, and charts need a "
            "recognizable source filename and available section, page, or line locator in the visible "
            "footer, with expanded references in slide notes. Treat missing or misleading attribution, "
            "internal EvidenceStore/block-ID/path/hash leakage, incorrect image-chart values, or unreadable "
            "image-chart labels as blocking. A retained block may lack derived citation metadata; in that "
            "case accept a conservative filename and available provenance locator. Treat workflow terms as "
            "leakage only when they describe generation or serve as citations, not when the source material "
            "legitimately discusses software, JSON, paths, or hashes. Treat harmless citation-style "
            "differences as advisory. Speaker notes are author claims that must still match frozen evidence. "
            f"Review inputs: {json.dumps(review_context or {}, ensure_ascii=False)}"
        )

    async def validate_generated(self, value: SlidesTaskPayload, workspace: Path) -> None:
        """Run the trusted deterministic validator and expose all findings."""

        try:
            from .evidence import load_frozen_evidence
            await asyncio.to_thread(
                load_frozen_evidence,
                workspace / "work" / "evidence.json",
                extracted_dir=workspace / "work" / "extracted",
            )
        except ValidationInfrastructureError:
            raise
        except Exception as exc:
            raise ValidationInfrastructureError(
                "deterministic validator infrastructure failure",
                diagnostic_codes=("evidence_unavailable",),
            ) from exc

        try:
            from .validation import validate_candidate_deck

            validation = await asyncio.to_thread(
                validate_candidate_deck,
                workspace,
                expected_slide_count=value.slides_count,
                requested_title=value.title,
                evidence_path=workspace / "work" / "evidence.json",
            )
        except ValidationInfrastructureError:
            raise
        except Exception as exc:
            raise ValidationInfrastructureError(
                "deterministic validator infrastructure failure",
                diagnostic_codes=("validator_unavailable",),
            ) from exc

        if str(getattr(validation.status, "value", validation.status)).lower() != "pass":
            findings = getattr(validation, "findings", ())
            rendered = [_validation_finding_payload(finding) for finding in findings]
            origins = [_validation_finding_origin(finding, payload) for finding, payload in zip(findings, rendered)]
            diagnostic_codes = tuple(
                str(payload.get("code", "validation_failure"))
                for payload in rendered
                if str(payload.get("code", "")).strip()
            )
            if any(origin == "infrastructure" for origin in origins):
                raise ValidationInfrastructureError(
                    "deterministic validator infrastructure failure",
                    findings=rendered,
                    diagnostic_codes=diagnostic_codes,
                )
            if not rendered:
                rendered = [{"code": "validation_failed", "message": "validator returned FAIL without findings"}]
                diagnostic_codes = ("validation_failed",)
            feedback = json.dumps({"findings": rendered}, ensure_ascii=False, sort_keys=True)
            raise DeterministicValidationError(
                "deterministic validation failed: " + feedback,
                findings=rendered,
                diagnostic_codes=diagnostic_codes,
            )

    def parse_review(
        self,
        response: str | None,
        workspace: Path,
        previous_review: ReviewOutcome | None = None,
    ) -> ReviewOutcome:
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
        if not isinstance(review, dict) or set(review) != {"summary", "findings"}:
            raise ValueError("semantic review response was malformed")
        required_finding_fields = {
            "severity",
            "category",
            "slide_number",
            "claim",
            "judgement",
            "evidence_refs",
            "reason",
            "correction",
        }
        findings = review.get("findings")
        if not isinstance(findings, list):
            raise ValueError("semantic review response was malformed")
        allowed_categories = {
            "unsupported_claim",
            "contradicted_claim",
            "misleading_synthesis",
            "material_omission",
            "unreadable_claim",
            "other",
        }
        for finding in findings:
            if not isinstance(finding, dict) or set(finding) != required_finding_fields:
                raise ValueError("semantic review response was malformed")
            if finding["category"] not in allowed_categories:
                raise ValueError("semantic review response was malformed")
            if finding["judgement"] not in {"supported", "unsupported", "contradicted", "misleading", "unclear"}:
                raise ValueError("semantic review response was malformed")
            if finding["judgement"] == "contradicted" and not finding["evidence_refs"]:
                raise ValueError("contradicted findings must cite evidence")
        try:
            # Parse types before applying cross-artifact checks. This turns a
            # wrong provider type into a controlled schema failure instead of
            # leaking a comparison TypeError from the coordinator.
            outcome = ReviewOutcome.model_validate(review)
        except Exception as exc:
            raise ValueError("semantic review response was malformed") from exc
        evidence_path = workspace / "work" / "evidence.json"
        snapshot_path = workspace / "work" / "intermediate" / "deck_snapshot.json"
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            slide_count = snapshot.get("slide_count")
            if not isinstance(slide_count, int) or slide_count < 1:
                raise ValueError
            evidence_ids = {
                str(item.get("id"))
                for key in ("blocks", "assets")
                for item in evidence.get(key, [])
                if isinstance(item, dict) and item.get("id")
            }
        except (OSError, TypeError, ValueError):
            raise ValueError("semantic review inputs were missing or malformed")
        if any(
            item.slide_number is not None and item.slide_number > slide_count
            for item in outcome.findings
        ):
            raise ValueError("semantic review referenced an unknown slide")
        if any(ref not in evidence_ids for item in outcome.findings for ref in item.evidence_refs):
            raise ValueError("semantic review referenced unknown evidence")
        semantic_review_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = semantic_review_path.with_suffix(".json.tmp")
        temporary_path.write_text(outcome.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(semantic_review_path)
        return outcome

    def revision_feedback(self, value: SlidesTaskPayload, review: ReviewOutcome, result: Any) -> str:
        del value, result
        findings = [
            item.model_dump(mode="json")
            for item in review.findings
            if item.status is ReviewFindingStatus.OPEN and item.severity is ReviewSeverity.BLOCKING
        ]
        return json.dumps(findings, ensure_ascii=False)

    async def publish(self, value: SlidesTaskPayload, result: Any, workspace: Path) -> SlidesTaskResult:
        response = getattr(result, "response", None)
        if response:
            await asyncio.to_thread(
                (workspace / "work" / "final_message.txt").write_text,
                response,
                encoding="utf-8",
            )
        # Recheck immediately before publication so the bytes that leave the
        # workspace are exactly the candidate that passed validation/review.
        await self.validate_generated(value, workspace)
        deck = workspace / "output" / "presentation.pptx"
        published = await asyncio.to_thread(publish_output, deck, str(value.job_id), settings.agent_output_root)
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
