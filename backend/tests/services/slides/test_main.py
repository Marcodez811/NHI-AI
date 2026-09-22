from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.config import settings
from app.models.slides import JobStatus, SlidesTaskPayload
from app.services.agentic.contracts import (
    DeterministicValidationError,
    ReviewFinding,
    ReviewFindingStatus,
    ReviewOutcome,
    ReviewSeverity,
    ValidationInfrastructureError,
)
from app.services.slides import agent as slides
from app.services.slides.adapter import slides_adapter
from app.services.slides.artifacts import JobError, publish_output, verify_output
from pypdf import PdfWriter


def _payload(job_id):
    return SlidesTaskPayload(
        job_id=job_id,
        title="2026 / Taiwan: NHI briefing",
        document_ids=[uuid4()],
        slides_count=8,
        guidance="Keep the summary concise.",
        tone="formal",
    )


class SlidesServiceTests(unittest.TestCase):
    def test_validate_source_paths_requires_resolved_unique_regular_supported_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            source.write_text("source", encoding="utf-8")
            resolved = source.resolve()

            self.assertEqual(slides.validate_source_paths([resolved]), [resolved])
            with self.assertRaisesRegex(slides.JobError, "unique"):
                slides.validate_source_paths([resolved, resolved])
            with self.assertRaisesRegex(slides.JobError, "resolved"):
                slides.validate_source_paths([Path("source.txt")])
            with self.assertRaisesRegex(slides.JobError, "regular"):
                slides.validate_source_paths([root.resolve()])

    def test_generate_slides_returns_only_safe_published_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            source.write_text("source", encoding="utf-8")
            job_id = uuid4()
            request = _payload(job_id)
            observed = {}

            async def fake_run_codex(*args, **kwargs):
                observed.update(kwargs)
                return Path("audit"), Path("message")

            def fake_verify(job_dir, *, expected_slide_count=None):
                observed["expected_slide_count"] = expected_slide_count
                deck = job_dir / "output" / "presentation.pptx"
                deck.write_bytes(b"deck")
                return deck

            with (
                patch.object(slides, "create_job_fontconfig", return_value=None),
                patch.object(slides, "preflight", return_value="Noto Sans TC"),
                patch.object(slides, "stage_required_skills", return_value=[]),
                patch.object(slides, "run_codex", fake_run_codex),
                patch.object(slides, "verify_output", fake_verify),
            ):
                result = asyncio.run(
                    slides.generate_slides(
                        job_id,
                        [source.resolve()],
                        request,
                        jobs_root=root / "jobs",
                        output_root=root / "published",
                        api_key="secret-api-key",
                        model="test-model",
                    )
                )

            self.assertEqual(result.status, JobStatus.COMPLETED)
            self.assertEqual(result.artifact_key, f"{job_id}.pptx")
            self.assertEqual(result.download_filename, "2026 Taiwan NHI briefing.pptx")
            self.assertEqual(observed["api_key"], "secret-api-key")
            self.assertEqual(observed["expected_slide_count"], request.slides_count)
            self.assertTrue((root / "published" / result.artifact_key).is_file())
            self.assertFalse((root / "jobs" / str(job_id)).exists())
            serialized = result.model_dump_json()
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("secret-api-key", serialized)

    def test_failure_keeps_workspace_when_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            source.write_text("source", encoding="utf-8")
            job_id = uuid4()

            with patch.object(slides, "preflight", side_effect=slides.JobError("preflight", "missing tool")):
                with self.assertRaisesRegex(slides.JobError, "missing tool"):
                    asyncio.run(
                        slides.generate_slides(
                            job_id,
                            [source.resolve()],
                            _payload(job_id),
                            jobs_root=root / "jobs",
                            output_root=root / "published",
                            api_key="test-key",
                            model="test-model",
                        )
                    )
            self.assertTrue((root / "jobs" / str(job_id)).is_dir())

    def test_publish_does_not_overwrite_existing_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "presentation.pptx"
            source.write_bytes(b"new deck")
            destination = root / "published"
            destination.mkdir()
            existing = destination / "job.pptx"
            existing.write_bytes(b"existing deck")
            with self.assertRaises(JobError):
                publish_output(source, "job", destination)
            self.assertEqual(existing.read_bytes(), b"existing deck")
            self.assertEqual(source.read_bytes(), b"new deck")

    def test_extraction_and_reviewer_runner_follow_settings(self):
        self.assertEqual(slides_adapter.extraction_runner, "codex")
        self.assertEqual(slides_adapter.reviewer_runner, "codex")
        with (
            patch.object(settings, "agent_extraction_runner", "agents"),
            patch.object(settings, "agent_reviewer_runner", "agents"),
        ):
            self.assertEqual(slides_adapter.extraction_runner, "agents")
            self.assertEqual(slides_adapter.reviewer_runner, "agents")
        # Both default back to "codex" once the override is gone: a developer
        # who changes no settings sees no behavior change.
        self.assertEqual(slides_adapter.extraction_runner, "codex")
        self.assertEqual(slides_adapter.reviewer_runner, "codex")

    def test_review_prompt_includes_policy_guidance_and_ignores_history_argument(self):
        prompt = slides_adapter.build_review_prompt(_payload(uuid4()), Path("/tmp/workspace"), None, None)
        self.assertIn("authoritative requested presentation title", prompt)
        self.assertIn("ordinary rounding", prompt)
        self.assertIn("fresh semantic-only review", prompt)
        self.assertIn("internal EvidenceStore/block-ID/path/hash leakage", prompt)
        self.assertIn("image-chart values", prompt)
        self.assertIn("expanded references on the final references slide", prompt)
        self.assertIn("unsupported or fabricated reference entry as blocking", prompt)
        self.assertIn("every footer and reference entry as an author claim", prompt)
        self.assertNotIn("expanded references in slide notes", prompt)
        self.assertNotIn("Speaker notes are author claims", prompt)
        self.assertNotIn("Prior blocking findings", prompt)

        previous = ReviewOutcome(
            summary="prior",
            findings=[
                ReviewFinding(
                    finding_id="review-1-finding-1",
                    severity=ReviewSeverity.BLOCKING,
                    category="factual",
                    issue_key="unsupported-claim",
                    locations=("slide:4",),
                    description="Claim is unsupported.",
                )
            ],
        )
        prompt_with_history = slides_adapter.build_review_prompt(
            _payload(uuid4()), Path("/tmp/workspace"), None, None, previous
        )
        self.assertEqual(prompt_with_history, prompt)

    def test_revision_feedback_contains_only_blocking_findings(self):
        review = ReviewOutcome(
            summary="needs work",
            findings=[
                ReviewFinding(
                    severity=ReviewSeverity.BLOCKING,
                    category="factual",
                    issue_key="policy-attribution",
                    locations=("slide:10",),
                    description="Policy attribution is missing.",
                    correction="Fix slide 10 policy attribution.",
                ),
                ReviewFinding(
                    severity=ReviewSeverity.ADVISORY,
                    category="cosmetic",
                    issue_key="range-annotation",
                    locations=("slide:2",),
                    description="Range annotation could be clearer.",
                ),
            ],
        )
        feedback = slides_adapter.revision_feedback(
            _payload(uuid4()),
            review,
            None,
        )
        self.assertEqual(len(json.loads(feedback)), 1)
        self.assertEqual(json.loads(feedback)[0]["issue_key"], "policy-attribution")

    def test_correction_prompt_requires_targeted_in_place_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "work").mkdir()
            (workspace / "work/slide_context.json").write_text(
                json.dumps({"staged_names": [], "font": "Noto Sans TC"}),
                encoding="utf-8",
            )
            prompt = asyncio.run(
                slides_adapter.build_prompt(
                    _payload(uuid4()),
                    workspace,
                    revision_feedback='["Fix slide 10 policy attribution."]',
                )
            )

        self.assertIn("corrective revision, not a regeneration", prompt)
        self.assertIn("Modify the existing presentation and affected artifacts in place", prompt)
        self.assertIn("Address every blocking finding below", prompt)
        self.assertIn("verify each blocking finding individually", prompt)
        self.assertIn("2026 / Taiwan: NHI briefing", prompt)
        self.assertIn("audience-facing source footers", prompt)
        self.assertIn("numbered final references slide", prompt)
        self.assertIn("requested 8 content slides", prompt)
        self.assertIn("final `\u53c3\u8003\u8cc7\u6599` slide", prompt)
        self.assertIn("remove any speaker notes", prompt)
        self.assertIn("work/sources.json", prompt)
        self.assertIn("collision suffixes", prompt)

    def test_correction_prompt_preserves_prior_validator_and_reviewer_findings(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "work").mkdir()
            (workspace / "work/slide_context.json").write_text(
                json.dumps({"staged_names": [], "font": "Noto Sans TC"}),
                encoding="utf-8",
            )
            prompt = asyncio.run(
                slides_adapter.build_prompt(
                    _payload(uuid4()),
                    workspace,
                    revision_feedback='[{"code": "reviewer_policy_attribution"}]',
                    prior_revision_feedback=(
                        'After author attempt 1 (validator):\n[{"code": "pptx_missing"}]',
                        'After author attempt 2 (validator):\n[{"code": "render_missing"}]',
                    ),
                )
            )

        self.assertTrue(slides_adapter.preserve_revision_feedback_history)
        self.assertIn("pptx_missing", prompt)
        self.assertIn("render_missing", prompt)
        self.assertIn("reviewer_policy_attribution", prompt)
        self.assertLess(prompt.index("pptx_missing"), prompt.index("render_missing"))
        self.assertLess(prompt.index("render_missing"), prompt.index("reviewer_policy_attribution"))

    def test_review_context_lists_semantic_review_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = asyncio.run(slides_adapter.semantic_review_context(_payload(uuid4()), Path(temporary)))
        self.assertEqual(context["requested_title"], "2026 / Taiwan: NHI briefing")
        self.assertEqual(
            context["artifacts"],
            [
                "work/evidence.json",
                "work/intermediate/deck_snapshot.json",
                "work/rendered/final/*.png",
            ],
        )
        self.assertIn("work/rendered/final/*.png", context["artifacts"])

    def test_author_writable_paths_exist_and_exclude_backend_final_renders(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = slides.create_job_workspace("job", Path(temporary))
            writable = slides_adapter.stage_writable_paths("author", workspace)
            self.assertTrue(all(path.exists() for path in writable))
            self.assertNotIn(workspace / "work" / "rendered" / "final", writable)
            self.assertIn(workspace / "work" / "outline_mapping.json", writable)
            self.assertNotIn(workspace / "work", writable)

    def test_validator_candidate_findings_are_structured_retry_feedback(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate_findings = (
                SimpleNamespace(
                    origin="candidate",
                    as_dict=lambda: {"code": "pptx_missing", "message": "presentation.pptx is missing"},
                ),
                SimpleNamespace(
                    origin="candidate",
                    as_dict=lambda: {"code": "render_missing", "message": "slide render is missing"},
                ),
            )
            validation = SimpleNamespace(status="FAIL", findings=candidate_findings)
            with (
                patch("app.services.slides.evidence.load_frozen_evidence", return_value={}),
                patch("app.services.slides.validation.validate_candidate_deck", return_value=validation),
            ):
                with self.assertRaises(DeterministicValidationError) as raised:
                    asyncio.run(slides_adapter.validate_generated(_payload(uuid4()), Path(temporary)))
            self.assertIn('"code": "pptx_missing"', str(raised.exception))
            self.assertIn('"code": "render_missing"', str(raised.exception))
            self.assertEqual(raised.exception.diagnostic_codes, ("pptx_missing", "render_missing"))

    def test_validator_infrastructure_origin_wins_mixed_findings(self):
        with tempfile.TemporaryDirectory() as temporary:
            findings = (
                SimpleNamespace(
                    origin="candidate",
                    as_dict=lambda: {"code": "pptx_missing", "message": "presentation.pptx is missing"},
                ),
                SimpleNamespace(
                    origin="infrastructure",
                    as_dict=lambda: {"code": "libreoffice_unavailable", "message": "renderer unavailable"},
                ),
            )
            validation = SimpleNamespace(status="FAIL", findings=findings)
            with (
                patch("app.services.slides.evidence.load_frozen_evidence", return_value={}),
                patch("app.services.slides.validation.validate_candidate_deck", return_value=validation),
            ):
                with self.assertRaises(ValidationInfrastructureError) as raised:
                    asyncio.run(slides_adapter.validate_generated(_payload(uuid4()), Path(temporary)))
            self.assertEqual(raised.exception.diagnostic_codes, ("pptx_missing", "libreoffice_unavailable"))

    def test_missing_author_artifact_is_deferred_to_deterministic_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            # The coordinator must reach ``validate_generated`` so a missing
            # PPTX is represented as a correctable ``pptx_missing`` finding.
            self.assertIsNone(
                slides_adapter.post_author_completion_check(
                    _payload(uuid4()),
                    None,
                    Path(temporary),
                )
            )

    def test_post_author_completion_check_cleans_every_author_attempt(self):
        """The backend, not the author, owns package cleanup (Fix 1a).

        ``post_author_completion_check`` runs after every author attempt, before
        ``validate_generated`` -- this proves the slides adapter wires that hook to the
        repository-owned cleaner rather than leaving cleanup to the author's own script.
        """

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "output").mkdir(parents=True)
            (workspace / "output" / "presentation.pptx").write_bytes(b"placeholder")

            with patch("app.services.slides.adapter.clean_candidate_deck") as cleaner:
                cleaner.return_value = ["ppt/notesSlides/notesSlide1.xml"]
                result = slides_adapter.post_author_completion_check(
                    _payload(uuid4()), None, workspace
                )

            self.assertIsNone(result)
            cleaner.assert_called_once_with(workspace)

    def test_verify_output_rejects_requested_slide_count_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deck = root / "output" / "presentation.pptx"
            deck.parent.mkdir()
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr("[Content_Types].xml", "content")
                archive.writestr("ppt/slides/slide1.xml", "slide")
                archive.writestr("ppt/slides/slide2.xml", "slide")
                archive.writestr("padding.bin", "x" * 12_000)

            with self.assertRaisesRegex(
                JobError,
                "expected 8 content slides plus one references slide",
            ):
                verify_output(root, expected_slide_count=8)

    def test_verify_output_rejects_speaker_notes_parts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deck = root / "output" / "presentation.pptx"
            deck.parent.mkdir()
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr("[Content_Types].xml", "content")
                archive.writestr("ppt/slides/slide1.xml", "slide")
                archive.writestr("ppt/slides/slide2.xml", "slide")
                archive.writestr("ppt/notesSlides/notesSlide1.xml", "notes")
                archive.writestr("padding.bin", "x" * 12_000)

            with self.assertRaisesRegex(JobError, "contains speaker notes"):
                verify_output(root, expected_slide_count=1)

    def test_verify_output_rejects_identical_final_renders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deck = root / "output" / "presentation.pptx"
            deck.parent.mkdir()
            with zipfile.ZipFile(deck, "w") as archive:
                archive.writestr("[Content_Types].xml", "content")
                archive.writestr("ppt/slides/slide1.xml", "slide")
                archive.writestr("ppt/slides/slide2.xml", "slide")
                archive.writestr("padding.bin", "x" * 12_000)
            (root / "input").mkdir()
            intermediate = root / "work" / "intermediate"
            intermediate.mkdir(parents=True)
            for name in ("evidence_map.json", "content_check.json", "review_report.json", "qa_report.json"):
                (intermediate / name).write_text("{}", encoding="utf-8")
            renders = root / "work" / "rendered" / "final"
            renders.mkdir(parents=True)
            for number in (1, 2):
                (renders / f"slide-{number}.png").write_bytes(b"\x89PNG\r\n\x1a\nidentical")

            def office_runner(command, **kwargs):
                if "soffice.py" in " ".join(command):
                    output_dir = Path(command[command.index("--outdir") + 1])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    writer = PdfWriter()
                    writer.add_blank_page(width=100, height=100)
                    writer.add_blank_page(width=100, height=100)
                    with (output_dir / "presentation.pdf").open("wb") as handle:
                        writer.write(handle)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with self.assertRaisesRegex(JobError, "all final slide renders are identical"):
                verify_output(root, expected_slide_count=1, runner=office_runner)

    def test_preflight_requires_thumbnail_renderer(self):
        def tool_lookup(name):
            return None if name == "pdftoppm" else f"/usr/bin/{name}"

        with self.assertRaisesRegex(JobError, "pdftoppm"):
            slides.preflight(
                [],
                tool_lookup=tool_lookup,
                module_lookup=lambda name: object(),
                runner=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Noto Sans TC\n", stderr=""),
            )

    def test_validate_and_publish_pass_requested_slide_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _payload(uuid4())
            observed = []

            def fake_validate(job_dir, *, expected_slide_count=None, **kwargs):
                observed.append(expected_slide_count)
                deck = job_dir / "output" / "presentation.pptx"
                deck.parent.mkdir(parents=True, exist_ok=True)
                deck.write_bytes(b"deck")
                return SimpleNamespace(status="PASS", findings=())

            with (
                patch("app.services.slides.evidence.load_frozen_evidence", return_value={}),
                patch("app.services.slides.validation.validate_candidate_deck", fake_validate),
                patch("app.services.slides.adapter.publish_output", return_value=root / "published.pptx"),
            ):
                asyncio.run(slides_adapter.validate_generated(request, root))
                asyncio.run(slides_adapter.publish(request, SimpleNamespace(response=None), root))

            self.assertEqual(observed, [request.slides_count, request.slides_count])
