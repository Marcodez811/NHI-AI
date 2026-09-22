"""Prompt construction and streamed Codex SDK execution for slides jobs."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox, SkillInput, TextInput, TurnResult

from app.models.slides import DEFAULT_TIMEOUT_MINUTES, SlideOutline, SlidesTaskPayload

from .artifacts import BACKEND_ROOT, PPTX_SKILL, SOURCE_SKILL
from .contracts import JobError, ProgressCallback

DEFAULT_PROGRESS_HEARTBEAT_SECONDS = 60.0


def _outline_instructions(outline: SlideOutline, requested_slide_count: int) -> str:
    """Render the approved outline as a node-order/emphasis instruction block.

    Stage 5 (docs/agents-sdk-migration-plan.md): a human already negotiated
    this structure with the planner. The author must honor node order and use
    ``emphasis`` as a relative attention budget rather than silently
    redesigning the deck's shape.
    """

    nodes = "\n".join(
        f"{index}. `{node.id}` -- {node.heading} (emphasis: {node.emphasis}, "
        f"~{node.approx_slides} slides): {node.intent}\n"
        f"   Key points: {'; '.join(node.key_points)}"
        for index, node in enumerate(outline.nodes, start=1)
    )
    return f"""

## Approved outline (must be honored)
A human has already reviewed and approved the following outline for this presentation.
Follow the node order below exactly: do not reorder, merge, split, drop, or introduce a
section the outline does not name. Treat `emphasis` as a relative attention budget across
sections -- a "deep" node earns more slides, more supporting detail, and more of the
evidence than a "light" one; `approx_slides` is a hint, not a hard per-node page count, but
the content-slide total should land near the requested {requested_slide_count}-slide brief.
The final references slide is deck furniture and is not part of this approved outline or
its `total_slides` value.

{nodes}

Narrative through-line: {outline.narrative}

For machine validation, write `work/outline_mapping.json` after the deck is complete.
Use exactly this shape, with one entry per approved node in the same order and inclusive,
one-based content-slide ranges:
`{{"nodes":[{{"node_id":"intro","slide_start":1,"slide_end":2}}]}}`.
Every content slide must belong to exactly one range. Do not include any references slide
in the mapping.
"""


def build_prompt(
    request: SlidesTaskPayload,
    staged_names: list[str],
    font_family: str | None = None,
    *,
    outline: SlideOutline | None = None,
) -> str:
    """Build the author brief from the frozen EvidenceStore contract.

    ``staged_names`` remains an argument for callers on the old runtime API,
    but source paths are intentionally absent from the author prompt.
    ``outline`` is the human-approved planning proposal (Stage 5); it is
    ``None`` for every workflow run that never declares a planner, so the
    prompt this function returns is completely unchanged for those runs.
    """

    del staged_names
    prompt = f"""# Presentation job

Create a polished, editable, source-grounded presentation from the frozen
EvidenceStore at `work/evidence.json`. This JSON file and assets under
`work/extracted/` are the only factual source you may use.

## Brief
- Title: {request.title}
- Tone: {request.tone}
- Length: Exactly {request.slides_count} content slides, followed by one final references
  slide titled exactly `參考資料` ({request.slides_count + 1} slides total).
- Font: Use {font_family or "a detected Traditional-Chinese/CJK-safe font"} consistently for slide text and charts.
- Additional guidance: {request.guidance}

Read the attached `$pptx-nhi-tw` skill completely. Synthesize the frozen evidence into one
narrative, resolve conflicts explicitly using the evidence blocks, and never invent facts or
data. Do not open or reinterpret files under `input/`, and do not modify `work/evidence.json`
or `work/extracted/`.

`work/sources.json` is the authoritative mapping from each staged evidence filename to the
knowledge-base `display_name` the user recognizes. Every visible source name must use that
`display_name`, even when an evidence block's `citation.source_name`, `citation.display_text`,
or a parsed document title disagrees. Keep available section, PDF-page, or text-line locator
detail from the evidence block, but never show a staged filename or collision suffix.
`EvidenceStore`, evidence IDs, hashes, JSON filenames, and extraction-process language are
internal workflow details. Never show them in slide text, citations, or the cover. Cite slides
that contain factual claims, figures, or charts. Each factual content-slide footer must use
`[N] 資料來源：<allowlisted display_name>，<locator>`, retaining every available
evidence-backed section, PDF-page, or text-line locator. Assign one number per source and
reuse that same number everywhere the source appears. When a slide draws on more than one
source, give each source its own footer paragraph; never combine sources on one line, whether
by repeating the `資料來源` marker or by stacking `[N]` numbers before a single marker. End the deck with the required
`參考資料` slide. List each source cited by the content slides exactly once, numbered `[1]`,
`[2]`, and so on with numbers that agree with the content footers, using its allowlisted
`display_name` from `work/sources.json` followed by every available evidence-backed section
path and page or line locator. Put exactly one entry in each paragraph, in the form
`[N] <allowlisted display_name>，<evidence-backed detail>`; omit the comma and detail only
when no reliable locator or section exists. For a retained block without `citation`, use
`provenance.source` only to select the matching staged filename in `work/sources.json`, then
use that entry's `display_name` plus any reliable provenance locator; use the allowlisted
name alone only when no reliable locator exists. The required references slide must be
derived from the frozen evidence: never fabricate a bibliography entry or add a pipeline
explanation to the cover. Never expose an XML path or invent a page, section, publisher,
date, or office.

Every chart must carry a descriptive title naming what it shows, with units on the relevant
axis label. The title belongs on the chart object itself, not on a slide heading placed above
it: for PptxGenJS, pass `showTitle: true` and `title` in the chart's own options so the title
is part of the chart, not just nearby text. Keep simple charts editable. For combination or dual-axis charts, dense labels, heatmaps,
or a native chart that still renders incorrectly after one correction, create a data-rendered
PNG from exact evidence values with the bundled chart-image script and add that image alone.
Keep all surrounding text and citations editable. Do not create or write speaker notes; the
delivered PPTX must contain no `ppt/notesSlides/` parts. Never re-serialize PPTX package XML
with `ElementTree` or any tool that invents its own namespace prefixes, and never hand-edit
`[Content_Types].xml`, `ppt/presentation.xml`, or any other package part directly -- the
backend runs deterministic package cleanup after every attempt, so you never need to touch
package internals yourself.

If template/ contains a PPTX, use it as the visual basis.
Assume network access and package installation are unavailable.

Render and inspect every slide, revising layout or visual issues in the candidate as needed.
The backend validator owns deterministic content checks and the deck snapshot. Required
author artifacts:
- output/presentation.pptx
- work/rendered/preview/*.png for the author's visual inspection only

The backend independently generates work/rendered/final/*.png for validation and semantic review.
"""
    if outline is not None:
        prompt += _outline_instructions(outline, request.slides_count)
    return prompt


def build_job_environment(backend_root: Path = BACKEND_ROOT, *, fontconfig_file: Path | None = None, cjk_font: str | None = None) -> dict[str, str]:
    """Expose /app and /app/node_modules to the isolated SDK workspace."""

    environment = os.environ.copy()
    python_bin = str(Path(sys.executable).resolve().parent)
    environment["PATH"] = os.pathsep.join(value for value in (python_bin, environment.get("PATH", "")) if value)
    environment["PYTHONPATH"] = os.pathsep.join(value for value in (str(backend_root), environment.get("PYTHONPATH", "")) if value)
    environment["NODE_PATH"] = os.pathsep.join(value for value in (str(backend_root / "node_modules"), environment.get("NODE_PATH", "")) if value)
    if sys.prefix != sys.base_prefix:
        environment["VIRTUAL_ENV"] = sys.prefix
    if fontconfig_file is not None:
        environment["FONTCONFIG_FILE"] = str(fontconfig_file)
    if cjk_font:
        environment["PPTX_CJK_FONT"] = cjk_font
    environment.update({"PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "UV_OFFLINE": "1", "npm_config_offline": "true", "npm_config_update_notifier": "false"})
    return environment


def build_skill_inputs(job_dir: Path, staged_names: list[str], prompt: str) -> list[Any]:
    skill_names = [PPTX_SKILL]
    if any(Path(name).suffix.lower() in {".docx", ".pdf"} for name in staged_names):
        skill_names.insert(0, SOURCE_SKILL)
    inputs: list[Any] = []
    for name in skill_names:
        skill_path = job_dir / ".agents" / "skills" / name / "SKILL.md"
        if not skill_path.is_file():
            raise JobError("codex_sdk", f"staged skill is missing: {name}")
        inputs.append(SkillInput(name=name, path=str(skill_path)))
    inputs.append(TextInput(prompt))
    return inputs


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if hasattr(value, "__dict__"):
        return {key: _json_value(item) for key, item in vars(value).items()}
    return str(value)


def _thread_item_root(item: Any) -> Any:
    return getattr(item, "root", item)


def _final_response_from_items(items: list[Any]) -> str | None:
    fallback: str | None = None
    for item in reversed(items):
        root = _thread_item_root(item)
        if getattr(root, "type", None) != "agentMessage":
            continue
        text = getattr(root, "text", None)
        if not isinstance(text, str):
            continue
        phase = _json_value(getattr(root, "phase", None))
        if phase == "final_answer":
            return text
        if phase is None and fallback is None:
            fallback = text
    return fallback


def _safe_progress_message(method: str, payload: Any) -> tuple[str, str] | None:
    if method == "turn/started":
        return ("agent", "Codex started the presentation turn")
    if method == "turn/plan/updated":
        return ("planning", "Codex updated the presentation work plan")
    if method == "item/started":
        item_type = str(getattr(_thread_item_root(getattr(payload, "item", None)), "type", "work"))
        messages = {
            "commandExecution": "Codex started a local generation or validation step",
            "fileChange": "Codex started updating presentation artifacts",
            "mcpToolCall": "Codex started a tool-assisted work step",
            "agentMessage": "Codex is preparing a status or result message",
        }
        return ("working", messages.get(item_type, "Codex started a presentation work step"))
    if method == "item/completed":
        item_type = str(getattr(_thread_item_root(getattr(payload, "item", None)), "type", "work"))
        messages = {
            "commandExecution": "Codex completed a local generation or validation step",
            "fileChange": "Codex completed updating presentation artifacts",
            "mcpToolCall": "Codex completed a tool-assisted work step",
            "agentMessage": "Codex completed a status or result message",
        }
        return ("working", messages.get(item_type, "Codex completed a presentation work step"))
    if method == "item/mcpToolCall/progress":
        return ("working", "A Codex tool-assisted step is still in progress")
    if method == "thread/compacted":
        return ("agent", "Codex compacted its working context and is continuing")
    if method in {"warning", "configWarning", "guardianWarning"}:
        return ("warning", "Codex reported a warning; details remain in the SDK audit log")
    if method == "error":
        return ("warning", "Codex reported a recoverable runtime error event")
    if method == "turn/completed":
        return ("agent", "Codex completed the presentation turn")
    return None


async def _collect_streamed_turn(handle: Any, emit_progress: Any) -> TurnResult:
    items: list[Any] = []
    usage: Any = None
    completed_turn: Any = None
    async for event in handle.stream():
        method = str(getattr(event, "method", "unknown"))
        payload = getattr(event, "payload", None)
        progress = _safe_progress_message(method, payload)
        if progress is not None:
            await emit_progress(progress[0], progress[1], sdk_event=method)
        if method == "item/completed" and getattr(payload, "turn_id", None) == handle.id:
            items.append(payload.item)
        elif method == "thread/tokenUsage/updated" and getattr(payload, "turn_id", None) == handle.id:
            usage = getattr(payload, "token_usage", None)
        elif method == "turn/completed":
            candidate = getattr(payload, "turn", None)
            if getattr(candidate, "id", None) == handle.id:
                completed_turn = candidate
    if completed_turn is None:
        raise RuntimeError("turn completed event not received")
    status = _json_value(getattr(completed_turn, "status", None))
    if status != "completed":
        error = getattr(completed_turn, "error", None)
        raise RuntimeError(getattr(error, "message", None) or f"turn ended with status {status}")
    return TurnResult(id=completed_turn.id, status=completed_turn.status, error=getattr(completed_turn, "error", None), started_at=getattr(completed_turn, "started_at", None), completed_at=getattr(completed_turn, "completed_at", None), duration_ms=getattr(completed_turn, "duration_ms", None), final_response=_final_response_from_items(items), items=items, usage=usage)


async def run_codex(job_dir: Path, prompt: str, staged_names: list[str], *, model: str | None = None, timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES, api_key: str | None = None, fontconfig_file: Path | None = None, cjk_font: str | None = None, backend_root: Path = BACKEND_ROOT, codex_factory: Any = AsyncCodex, progress_callback: ProgressCallback | None = None, heartbeat_seconds: float = DEFAULT_PROGRESS_HEARTBEAT_SECONDS) -> tuple[Path, Path]:
    """Run one official AsyncCodex streamed turn and persist safe job-local audit data."""

    log_path = job_dir / "work" / "codex_result.json"
    final_message_path = job_dir / "work" / "final_message.txt"
    progress_path = job_dir / "work" / "progress.jsonl"
    sdk_inputs = build_skill_inputs(job_dir, staged_names, prompt)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text("", encoding="utf-8")
    lock = asyncio.Lock()
    sequence = 0
    started = asyncio.get_running_loop().time()
    stop_heartbeat = asyncio.Event()
    job_logger = logger.bind(job_id=job_dir.name)

    async def emit_progress(stage: str, message: str, *, sdk_event: str | None = None, heartbeat: bool = False) -> None:
        nonlocal sequence
        async with lock:
            sequence += 1
            event = {"sequence": sequence, "timestamp": datetime.now(timezone.utc).isoformat(), "stage": stage, "message": message, "heartbeat": heartbeat, "elapsed_seconds": round(asyncio.get_running_loop().time() - started, 1)}
            if sdk_event is not None:
                event["sdk_event"] = sdk_event
            with progress_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            job_logger.info("progress stage={} message={}", stage, message)
            if progress_callback is not None:
                try:
                    callback_result = progress_callback(event.copy())
                    if inspect.isawaitable(callback_result):
                        await callback_result
                except Exception as exc:
                    job_logger.warning("Progress callback failed and was ignored: {}: {}", type(exc).__name__, exc)

    async def heartbeat_loop() -> None:
        while heartbeat_seconds > 0:
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=heartbeat_seconds)
                return
            except TimeoutError:
                if not stop_heartbeat.is_set():
                    await emit_progress("heartbeat", "Presentation generation is still running", heartbeat=True)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        await emit_progress("starting", "Initializing Codex presentation generation")
        async with codex_factory(CodexConfig(env=build_job_environment(backend_root, fontconfig_file=fontconfig_file, cjk_font=cjk_font))) as codex:
            if not api_key:
                raise JobError("codex_sdk", "OpenAI API key is required")
            await codex.login_api_key(api_key)
            thread = await codex.thread_start(cwd=str(job_dir), model=model, sandbox=Sandbox.full_access, approval_mode=ApprovalMode.deny_all, ephemeral=True)
            await emit_progress("agent", "Codex thread is ready")
            async def execute_turn() -> TurnResult:
                handle = await thread.turn(sdk_inputs)
                return await _collect_streamed_turn(handle, emit_progress)

            turn = await asyncio.wait_for(execute_turn(), timeout=timeout_minutes * 60)
    except TimeoutError as exc:
        await emit_progress("failed", f"Codex timed out after {timeout_minutes} minutes")
        raise JobError("codex_sdk", f"Codex timed out after {timeout_minutes} minutes") from exc
    except JobError:
        raise
    except Exception as exc:
        await emit_progress("failed", "Codex presentation generation failed")
        raise JobError("codex_sdk", f"{type(exc).__name__}: {exc}") from exc
    finally:
        stop_heartbeat.set()
        await heartbeat_task
    final_response = turn.final_response or ""
    final_message_path.write_text(final_response, encoding="utf-8")
    audit = {"thread_id": thread.id, "turn_id": turn.id, "status": _json_value(turn.status), "error": _json_value(turn.error), "duration_ms": turn.duration_ms, "usage": _json_value(turn.usage), "item_count": len(turn.items), "final_response": final_response}
    log_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    await emit_progress("completed", "Codex artifacts are ready for release validation")
    return log_path, final_message_path
