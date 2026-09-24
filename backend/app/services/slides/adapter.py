"""The allowlisted ``slides`` workflow adapter.

The adapter contains presentation policy; the generic agentic service owns the
thread lifecycle and bounded review/correction loop.  Filesystem and process
heavy work is deliberately moved to worker threads so Taskiq's event loop
remains available to other jobs.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.config import settings
from app.models.slides import JobStatus, SlideOutline, SlidesTaskPayload, SlidesTaskResult
from app.services.agentic.contracts import (
    AgentExecutionResult,
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
    CandidateDeckCleanupError,
    clean_candidate_deck,
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
from .source_manifest import SourceManifestError, load_source_manifest, write_source_manifest


_VALIDATION_ORIGINS = frozenset({"candidate", "infrastructure"})


async def _document_display_names(document_ids: list[UUID]) -> list[str]:
    """Resolve catalog names at execution time without putting IDs in artifacts."""

    from sqlmodel import Session

    from app.db import engine
    from app.services.documents.repository import SQLModelDocumentRepository

    display_names: list[str] = []
    with Session(engine) as session:
        repository = SQLModelDocumentRepository(session)
        for document_id in document_ids:
            document = await repository.get_document(document_id)
            if document is None or not document.display_name.strip():
                raise JobError("inputs", "source document metadata is unavailable")
            display_names.append(document.display_name)
    return display_names


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
    preserve_revision_feedback_history = True
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

    @property
    def extraction_runner(self) -> str:
        # docs/agents-sdk-migration-plan.md Stage 1: the extraction node's
        # runner is a per-node configuration choice, not a deploy. Defaults to
        # "codex" through the setting itself.
        return settings.agent_extraction_runner

    @property
    def reviewer_runner(self) -> str:
        # Stage 2: same seam as ``extraction_runner``, for the reviewer node.
        # The reviewer requests ``output_type=ReviewOutcome`` regardless of
        # which runner this resolves to (app/services/agentic/service.py);
        # both runners hand the coordinator an already-validated outcome.
        return settings.agent_reviewer_runner

    # Stage 5 (docs/agents-sdk-migration-plan.md): declaring a non-empty
    # ``planner_role`` is what opts this workflow into the planning phase --
    # ``_execute_workflow`` gates on it exactly the way it gates extraction on
    # ``extraction_skills``. Unlike extraction/reviewer runner selection, this
    # gate guards a whole new pause in the pipeline, not just which provider
    # runs an existing one, so it defaults *off*: ``settings.agent_planner_enabled``
    # keeps every existing slides job completing exactly as it does today until an
    # operator opts in by configuration, matching this migration's "flip by
    # config, not by deploy" pattern (Stage 0).
    planner_output_type = SlideOutline

    @property
    def planner_role(self) -> str | None:
        return "presentation_planner" if settings.agent_planner_enabled else None

    @property
    def planner_model(self) -> str:
        return settings.agent_planner_model or settings.agent_default_model

    @property
    def planner_reasoning_effort(self) -> AgentReasoningEffort:
        return settings.agent_planner_reasoning_effort or settings.agent_default_reasoning_effort

    @property
    def planner_runner(self) -> str:
        # Same per-node runner seam as extraction/reviewer; defaults to
        # "codex" through the setting itself.
        return settings.agent_planner_runner

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
        context_path = workspace / "work" / "slide_context.json"
        context_exists = await asyncio.to_thread(context_path.exists)
        context_text = await asyncio.to_thread(context_path.read_text, encoding="utf-8") if context_exists else None
        context = json.loads(context_text) if context_text else {}
        display_names = await _document_display_names(value.document_ids)
        sources_path = workspace / "work" / "sources.json"
        evidence_exists = await asyncio.to_thread((workspace / "work" / "evidence.json").is_file)
        sources_exist = await asyncio.to_thread(sources_path.exists)

        # Resuming after outline approval must use the already-frozen evidence.
        # The originals may legitimately have been removed during a long human
        # review, and reopening them would make the resumed author depend on a
        # different input boundary from the planner it is following.
        if evidence_exists:
            if not sources_exist:
                raise JobError("inputs", "source name manifest is unavailable")
            try:
                existing_sources = await asyncio.to_thread(load_source_manifest, sources_path)
                staged_names = [source.staged_filename for source in existing_sources]
                if len(staged_names) != len(display_names):
                    raise SourceManifestError("source manifest does not match the job inputs")
                await asyncio.to_thread(
                    write_source_manifest,
                    sources_path,
                    staged_names,
                    display_names,
                )
            except SourceManifestError as exc:
                raise JobError("inputs", "source name manifest is unavailable") from exc
            context["staged_names"] = staged_names
            await asyncio.to_thread(
                context_path.write_text,
                json.dumps(context, ensure_ascii=False),
                encoding="utf-8",
            )
            return

        source_paths = await SharedVolumeDocumentResolver(settings.documents_root).resolve_many(value.document_ids)
        validated = await asyncio.to_thread(validate_source_paths, source_paths)
        fontconfig = Path(context["fontconfig"]) if context.get("fontconfig") else None
        font = await asyncio.to_thread(
            preflight,
            validated,
            environment=build_job_environment(fontconfig_file=fontconfig),
        )
        if sources_exist:
            try:
                existing_sources = await asyncio.to_thread(load_source_manifest, sources_path)
            except SourceManifestError as exc:
                raise JobError("inputs", "source name manifest is unavailable") from exc
            staged_names = [source.staged_filename for source in existing_sources]
            if len(staged_names) != len(validated):
                raise JobError("inputs", "source name manifest does not match the job inputs")
            staged_files_exist = all(
                (workspace / "input" / name).is_file()
                and not (workspace / "input" / name).is_symlink()
                for name in staged_names
            )
            if not staged_files_exist:
                raise JobError("inputs", "staged source files are unavailable")
        else:
            staged_names = await asyncio.to_thread(stage_uploads, workspace, validated)
        try:
            await asyncio.to_thread(
                write_source_manifest,
                sources_path,
                staged_names,
                display_names,
            )
        except SourceManifestError as exc:
            raise JobError("inputs", "source name manifest could not be prepared") from exc
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
        if stage == "planner":
            # Read-only, exactly like the author: the planner sees the frozen
            # evidence through the sandbox filesystem grant, never inlined into
            # its prompt, so it and the author are grounded in byte-identical
            # evidence (docs/agents-sdk-migration-plan.md, Stage 5 decision).
            return (
                workspace / "work" / "evidence.json",
                workspace / "work" / "sources.json",
            )
        if stage == "author":
            return (
                workspace / "work" / "evidence.json",
                workspace / "work" / "sources.json",
                workspace / "work" / "extracted" / "assets",
                workspace / "template",
                # The human-approved outline (Stage 5); absent on a run whose
                # workflow never declares a planner, so this grant is a no-op
                # for those workflows.
                workspace / "work" / "outline.json",
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
                workspace / "work" / "outline_mapping.json",
                workspace / "work" / "slide_citations.json",
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

    async def build_planning_prompt(self, value: SlidesTaskPayload, workspace: Path, *, runner_name: str = "codex") -> str:
        """Return the planning activation's instructions.

        ``runner_name`` only changes how this describes evidence access: on the "codex"
        path (unchanged; the default) the frozen store is a mounted, read-only file, so
        the instructions point at `work/evidence.json`/`work/sources.json` exactly as
        before. On the "agents" path -- ``_execute_planning``
        (app/services/agentic/service.py) -- this stage has no file or shell access at
        all; the frozen evidence instead arrives inlined as an ``<evidence>`` block in
        the same message (``build_planning_evidence_block`` below), already carrying
        each block's knowledge-base source name, so there is nothing left to mount or
        look up.
        """

        del workspace
        if runner_name == "agents":
            evidence_access = (
                "Propose a structured outline for a presentation grounded entirely in the "
                "evidence given to you in the `<evidence>` block of this message -- this stage "
                "has no file or shell access, so that block is the only evidence you can see. Do "
                "not draft slide content or invent facts outside it -- return only the structured "
                "outline.\n\n"
                "Each evidence entry already carries its `source` as the knowledge-base name to "
                "use verbatim in a `key_point`; never invent, translate, or alter a source name, "
                "and never reference `work/sources.json` or `work/evidence.json` -- neither is "
                "available to you here.\n\n"
                "Evidence `id` values are internal identifiers. They belong only in "
                "`evidence_refs`: never write an id, or any hash-like string, in `title`, "
                "`narrative`, `heading`, `intent`, or `key_points` -- those fields are shown to the "
                "user, who knows the source documents only by their knowledge-base names."
            )
            evidence_refs_note = "ids copied verbatim from the `<evidence>` block above"
        else:
            evidence_access = (
                "Propose a structured outline for a presentation grounded entirely in the frozen\n"
                "EvidenceStore at `work/evidence.json` (already mounted read-only; you cannot write to it\n"
                "or to any other workspace path from this stage). Do not draft slide content or write any\n"
                "file -- return only the structured outline.\n\n"
                "`work/sources.json` is the authoritative mapping from staged evidence filenames to the\n"
                "knowledge-base names users recognize. Whenever a `key_point` names a source, use only its\n"
                "`display_name` from that file; never expose a staged filename, collision suffix, hash, or\n"
                "derived document title."
            )
            evidence_refs_note = "ids copied verbatim from `work/evidence.json` blocks"
        return f"""# Planning stage

{evidence_access}

## Brief
- Title: {value.title}
- Tone: {value.tone}
- Target length: {value.slides_count} content slides. `SlideOutline.total_slides` and the
  sum of `approx_slides` describe only those content slides; the author adds a final
  `參考資料` slide outside the outline.
- Additional guidance: {value.guidance}

Organize the presentation into sections, not per-slide breakdowns: each node needs a
`heading`, an `intent` describing what it must accomplish, 2-5 evidence-grounded
`key_points`, and `evidence_refs` -- {evidence_refs_note}.
An id that does not exist in that store will be rejected before a human ever reviews this
outline. Set `emphasis` (light/normal/deep) to reflect how much author attention each
section deserves relative to the others, and `approx_slides` as a realistic hint whose sum
lands near the requested {value.slides_count} content slides. Do not add a references node;
the final references slide is deck furniture. Resolve conflicting evidence explicitly
rather than presenting both sides unreconciled.
"""

    async def build_planning_evidence_block(self, value: SlidesTaskPayload, workspace: Path) -> str:
        """Render the compact, delimited evidence block for the "agents" planner path.

        Only reached when ``planner_runner == "agents"`` (``_execute_planning``,
        app/services/agentic/service.py): the Codex path never calls this and keeps
        reading `work/evidence.json` through its own bwrap sandbox exactly as before.
        ``EvidenceError`` (including the size guard in ``render_compact_evidence``)
        becomes a tagged ``JobError`` here, the same conversion ``post_planning`` already
        does, so a job-level caller sees one consistent error surface regardless of
        which stage raised it.
        """

        del value
        from .evidence import EvidenceError, build_planner_evidence_block

        try:
            return await asyncio.to_thread(
                build_planner_evidence_block,
                workspace,
                max_chars=settings.agent_planner_max_evidence_chars,
            )
        except EvidenceError as exc:
            raise JobError("planning", str(exc)) from exc

    async def post_planning(self, value: SlidesTaskPayload, result: AgentExecutionResult, workspace: Path) -> None:
        """Validate and persist the planner's proposal as durable revision 1.

        The generic coordinator only knows a planning activation finished with
        some result; turning its typed ``SlideOutline`` into a `slide_outlines`
        row -- and rejecting one that cites evidence the extractor never
        produced -- is slides-specific policy, so it lives here rather than in
        ``app/services/agentic/service.py``.
        """

        from sqlmodel import Session

        from app.db import engine

        from .evidence import EvidenceError, validate_outline_evidence_references
        from .outline_repository import SQLModelSlideOutlineRepository

        outline = result.output
        if not isinstance(outline, SlideOutline):
            raise JobError("planning", "planner did not return a structured outline")
        try:
            validate_outline_evidence_references(outline, workspace / "work" / "evidence.json")
        except EvidenceError as exc:
            raise JobError("planning", "outline referenced evidence the extractor never produced") from exc

        # ``create_next_revision`` is an ``async def`` that does synchronous
        # SQLModel work internally, awaited directly rather than offloaded to a
        # thread -- the same pattern ``app/tasks/agents.py`` already uses for
        # every other durable-row write on this workflow's boundary.
        with Session(engine) as session:
            repository = SQLModelSlideOutlineRepository(session)
            await repository.create_next_revision(value.job_id, outline, session_id=str(value.job_id))

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

    async def build_prompt(
        self,
        value: SlidesTaskPayload,
        workspace: Path,
        *,
        semantic_review_context: Any = None,
        revision_feedback: str | None = None,
        prior_revision_feedback: tuple[str, ...] = (),
    ) -> str:
        context_path = workspace / "work" / "slide_context.json"
        context = json.loads(await asyncio.to_thread(context_path.read_text, encoding="utf-8"))
        outline_path = workspace / "work" / "outline.json"
        outline: SlideOutline | None = None
        if await asyncio.to_thread(outline_path.is_file):
            outline_text = await asyncio.to_thread(outline_path.read_text, encoding="utf-8")
            outline = SlideOutline.model_validate(json.loads(outline_text))
        prompt = build_prompt(value, context.get("staged_names", []), font_family=context.get("font"), outline=outline)
        if revision_feedback:
            prompt += (
                "\n\n## Corrective revision instructions\n"
                "This is a corrective revision, not a regeneration. The workspace already contains "
                "the previous candidate presentation and its deterministic validation artifacts. Modify the "
                "existing presentation and affected artifacts in place. Preserve the requested "
                f"title exactly as `{value.title}`, the requested {value.slides_count} content slides, "
                "and the final `參考資料` slide; "
                "do not redesign or rewrite unaffected slides.\n"
                "Address every blocking finding below. After changes, regenerate affected preview renders, "
                "then stop; the backend validator owns content_check.json and deck_snapshot.json. "
                "The frozen evidence.json and work/extracted/ tree are read-only and authoritative. "
                "Preserve or repair audience-facing source footers and the numbered final references slide "
                "using only knowledge-base display names "
                "from the read-only work/sources.json mapping, and remove any speaker notes. Never add "
                "staged filenames, collision suffixes, derived document titles, EvidenceStore names, "
                "paths, hashes, or block IDs to the delivered presentation. "
                "Before finishing, verify each blocking finding individually "
                "and state which slide or artifact change resolves it.\n"
            )
            if prior_revision_feedback:
                prompt += (
                    "\n## Earlier blocking findings whose fixes must be preserved\n"
                    "These findings came from earlier author attempts. They may already be fixed; "
                    "do not undo those fixes while addressing the latest feedback. Check the current "
                    "deck against each one before finishing, and repair any that has recurred.\n\n"
                    + "\n\n".join(prior_revision_feedback)
                    + "\n"
                )
            prompt += "\n## Latest blocking findings to correct\n" + revision_feedback
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
            "The `requested_slide_count` in Review inputs counts content slides only; the deck correctly "
            "ends with one additional final `參考資料` references slide, so the correct total is "
            "`requested_slide_count + 1` slides. Never raise a finding about the number of slides or the "
            "deck's overall length -- slide count and deck structure are the deterministic validator's job, "
            "not yours.\n"
            "Return ONLY valid JSON with exactly these top-level fields: summary (string), "
            "findings (array). Every finding must include severity (blocking or advisory), category, "
            "slide_number (integer or null), claim, judgement, evidence_refs (array of evidence IDs), "
            "reason, and correction. Use an empty findings array when there are no issues. A blocking finding "
            "must prevent publication because it is materially incorrect, misleading, unsupported, "
            "missing, unreadable, or internally inconsistent. Treat ordinary rounding or wording "
            "polish as advisory unless it changes a threshold, comparison, denominator, or meaning. "
            "Every factual claim must be supported by one or more evidence_refs. For a contradiction, "
            "identify the correct value in reason and provide a concrete correction. "
            "Citations are generated by the backend from the author's declared evidence IDs. "
            "Judge whether the cited evidence supports each slide's factual claims, figures, and "
            "charts; treat missing or misleading attribution and unsupported claims as blocking. "
            "A retained block may lack derived citation metadata; use its provenance to judge support. "
            "Treat internal EvidenceStore/block-ID/path/hash leakage, incorrect image-chart values, "
            "or unreadable image-chart labels as blocking. Treat workflow terms as leakage only when "
            "they describe generation, not when source material legitimately discusses software, "
            "JSON, paths, or hashes. Read footer and reference text from "
            "`work/intermediate/deck_snapshot.json`, not downscaled PNG renders. "
            "Citation structure, formatting, names, numbering and placeholders are checked "
            "deterministically: never raise structural or format findings about citations or "
            "request changes to their layout. Judge whether the cited evidence actually supports "
            "each claim, and whether synthesis is misleading or omits something material -- for "
            "example, a figure without the year the evidence ties it to. Never request adding or "
            "removing slides; the deterministic validator owns deck structure. "
            "The delivered deck must not use speaker notes. "
            f"Review inputs: {json.dumps(review_context or {}, ensure_ascii=False)}"
        )

    def post_author_completion_check(self, value: SlidesTaskPayload, result: Any, workspace: Path) -> None:
        """Clean the candidate and write citations after every author attempt.

        This runs for every attempt (initial and correction), before ``validate_generated``.
        Package cleanup -- stripping the notes graph, orphaned parts, and stale
        Content-Type overrides -- was previously left to the author's own ad hoc script,
        which once re-serialized ``[Content_Types].xml``/``presentation.xml`` with
        ``xml.etree.ElementTree`` and produced a deck LibreOffice could not open at all.
        Doing it here, deterministically, with the repository-owned ``clean.py``, removes
        package surgery from the author's job entirely.
        """

        del value, result
        from .citation_writer import write_citations
        from .validation import _write_json_atomic

        try:
            clean_candidate_deck(workspace)
        except CandidateDeckCleanupError as exc:
            problems = [{"code": "citation_candidate_package_invalid", "message": str(exc)}]
        else:
            problems = write_citations(workspace)
        _write_json_atomic(
            workspace / "work" / "intermediate" / "citation_writer.json",
            {"problems": problems},
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
                sources_path=workspace / "work" / "sources.json",
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
