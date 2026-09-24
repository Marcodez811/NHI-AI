"""Public orchestration facade for source-grounded PowerPoint generation."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from loguru import logger

from app.models.slides import DEFAULT_TIMEOUT_MINUTES, JobStatus, SlidesTaskPayload, SlidesTaskResult

from .artifacts import (
    cleanup_job,
    create_job_fontconfig,
    create_job_workspace,
    preflight,
    publish_output,
    stage_required_skills,
    stage_uploads,
    validate_source_paths,
    verify_output,
)
from .contracts import JobError, ProgressCallback
from .runtime import build_job_environment, build_prompt, run_codex


def _download_filename(title: str) -> str:
    """Return a portable, non-empty filename while retaining Unicode titles."""

    cleaned = "".join(char for char in title.strip() if char.isalnum() or char in " ._-")
    cleaned = " ".join(cleaned.split()).strip(" ._-")[:120]
    return f"{cleaned or 'presentation'}.pptx"


async def generate_slides(
    job_id: UUID,
    source_paths: Sequence[Path],
    request: SlidesTaskPayload,
    *,
    jobs_root: Path,
    output_root: Path,
    api_key: str,
    model: str,
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
    keep_workspace_on_failure: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> SlidesTaskResult:
    """Generate, validate, and publish one deck from worker-resolved sources.

    The result deliberately contains only safe publish metadata. Task adapters
    should catch :class:`JobError` to record failures.
    """

    if request.job_id != job_id:
        raise JobError("request", "payload job_id does not match the requested job")
    if timeout_minutes < 1:
        raise JobError("request", "timeout_minutes must be at least 1")
    started_at = datetime.now(timezone.utc)
    job_dir: Path | None = None
    job_logger = logger.bind(job_id=str(job_id))
    try:
        # These helpers perform filesystem traversal, ZIP parsing, and (during
        # preflight/verification) blocking subprocess calls.  Keep them off
        # Taskiq's event loop so another job in the same worker can make
        # progress while this job is preparing or releasing artifacts.
        validated_sources = await asyncio.to_thread(validate_source_paths, source_paths)
        job_logger.info("Starting presentation job with {} source file(s)", len(validated_sources))
        job_dir = await asyncio.to_thread(create_job_workspace, str(job_id), jobs_root)
        fontconfig_file = await asyncio.to_thread(create_job_fontconfig, job_dir)
        preflight_environment = build_job_environment(fontconfig_file=fontconfig_file)
        cjk_font = await asyncio.to_thread(preflight, validated_sources, environment=preflight_environment)
        # Rewrite the fontconfig now that the real CJK font is known, so the delivery
        # font declared in the prompt (Microsoft JhengHei) aliases to it for rendering
        # here. See create_job_fontconfig.
        fontconfig_file = await asyncio.to_thread(create_job_fontconfig, job_dir, cjk_font=cjk_font)
        await asyncio.to_thread(stage_required_skills, job_dir)
        staged_names = await asyncio.to_thread(stage_uploads, job_dir, validated_sources)
        prompt = build_prompt(request, staged_names, font_family=cjk_font)
        await asyncio.to_thread((job_dir / "work" / "prompt.md").write_text, prompt, encoding="utf-8")
        await run_codex(
            job_dir,
            prompt,
            staged_names,
            model=model,
            timeout_minutes=timeout_minutes,
            api_key=api_key,
            fontconfig_file=fontconfig_file,
            cjk_font=cjk_font,
            progress_callback=progress_callback,
        )
        verified_output = await asyncio.to_thread(
            verify_output,
            job_dir,
            expected_slide_count=request.slides_count,
        )
        published = await asyncio.to_thread(publish_output, verified_output, str(job_id), output_root)
        finished_at = datetime.now(timezone.utc)
        await asyncio.to_thread(cleanup_job, job_dir)
        job_logger.success("Presentation job completed")
        return SlidesTaskResult(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            artifact_key=published.name,
            download_filename=_download_filename(request.title),
            started_at=started_at,
            finished_at=finished_at,
        )
    except JobError:
        job_logger.error("Presentation job failed")
        if job_dir is not None and not keep_workspace_on_failure:
            await asyncio.to_thread(cleanup_job, job_dir)
        raise
    except Exception as exc:
        job_logger.exception("Unexpected presentation job failure")
        if job_dir is not None and not keep_workspace_on_failure:
            await asyncio.to_thread(cleanup_job, job_dir)
        raise JobError("unexpected", f"presentation generation failed ({type(exc).__name__})") from exc
