from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.models.slides import JobStatus, SlidesTaskPayload
from app.services.slides import agent as slides
from app.services.slides.adapter import slides_adapter
from app.services.slides.artifacts import JobError, publish_output


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

            def fake_verify(job_dir):
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
        with self.assertRaises(ValueError):
            slides_adapter.parse_review(
                '{"summary":"ok","blocking_findings":[],"findings":["not an object"]}',
                Path("/tmp/workspace"),
            )
