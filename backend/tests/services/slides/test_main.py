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

from app.models.slides import JobStatus, SlidesTaskPayload
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

    def test_semantic_review_rejects_malformed_findings(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                slides_adapter.parse_review(
                    '{"summary":"ok","blocking_findings":[],"findings":[{"detail":"not a string"}]}',
                    Path(temporary),
                )

    def test_semantic_review_schema_and_parser_use_string_findings(self):
        schema = slides_adapter.review_output_schema
        self.assertEqual(schema["properties"]["blocking_findings"]["items"], {"type": "string"})
        self.assertEqual(schema["properties"]["findings"]["items"], {"type": "string"})
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            review = slides_adapter.parse_review(
                '{"summary":"Two issues found.","blocking_findings":["Slide 4 is unsupported."],"findings":["Slide 2 title could be clearer."]}',
                workspace,
            )
            self.assertEqual(
                review,
                {
                    "summary": "Two issues found.",
                    "blocking_findings": ["Slide 4 is unsupported."],
                    "findings": ["Slide 2 title could be clearer."],
                },
            )
            self.assertEqual(
                json.loads((workspace / "work/intermediate/semantic_review.json").read_text(encoding="utf-8")),
                review,
            )
            with self.assertRaises(ValueError):
                slides_adapter.parse_review('{"summary":"bad"}', workspace)
            self.assertFalse((workspace / "work/intermediate/semantic_review.json").exists())

        prompt = slides_adapter.build_review_prompt(_payload(uuid4()), Path("/tmp/workspace"), None, None)
        self.assertIn("authoritative requested presentation title", prompt)
        self.assertIn("ordinary rounding", prompt)
        self.assertIn("independent root cause", prompt)
        self.assertNotIn("read-only sandbox", prompt)

    def test_revision_feedback_contains_only_blocking_findings(self):
        feedback = slides_adapter.revision_feedback(
            _payload(uuid4()),
            {
                "blocking_findings": ["Fix slide 10 policy attribution."],
                "findings": ["Consider using a range annotation."],
            },
            None,
        )
        self.assertEqual(json.loads(feedback), ["Fix slide 10 policy attribution."])

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

    def test_review_context_lists_semantic_review_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = asyncio.run(slides_adapter.semantic_review_context(_payload(uuid4()), Path(temporary)))
        self.assertEqual(context["requested_title"], "2026 / Taiwan: NHI briefing")
        self.assertIn("work/intermediate/evidence_map.json", context["artifacts"])
        self.assertIn("work/rendered/final/*.png", context["artifacts"])

    def test_missing_author_artifact_is_a_workflow_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(JobError, "artifact was not generated"):
                slides_adapter.post_author_completion_check(
                    _payload(uuid4()),
                    None,
                    Path(temporary),
                )

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

            with self.assertRaisesRegex(JobError, "expected 8 slides, found 2"):
                verify_output(root, expected_slide_count=8)

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
                verify_output(root, expected_slide_count=2, runner=office_runner)

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

            def fake_verify(job_dir, *, expected_slide_count=None):
                observed.append(expected_slide_count)
                deck = job_dir / "output" / "presentation.pptx"
                deck.parent.mkdir(parents=True, exist_ok=True)
                deck.write_bytes(b"deck")
                return deck

            with (
                patch("app.services.slides.adapter.verify_output", fake_verify),
                patch("app.services.slides.adapter.publish_output", return_value=root / "published.pptx"),
            ):
                asyncio.run(slides_adapter.validate_generated(request, root))
                asyncio.run(slides_adapter.publish(request, SimpleNamespace(response=None), root))

            self.assertEqual(observed, [request.slides_count, request.slides_count])
