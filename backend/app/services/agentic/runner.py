"""Codex execution primitives for generic agentic workflows.

This module owns operational concerns only.  Workflow policy remains in an
adapter and no task input is ever interpreted as a Python import or command.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox, SkillInput, TextInput

from .contracts import ProgressCallback, TurnAudit, TurnRequest


class WorkflowExecutionError(RuntimeError):
    """Operational failure with a message safe to expose to callers."""


class WorkflowTimeoutError(WorkflowExecutionError):
    """The total workflow deadline elapsed."""


_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|(?:api[_ -]?key|authorization|password|passwd|token|credential)\s*[:=]\s*\S+)",
    re.I,
)
_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:tmp|app|home|workspace)(?:[\\/]|$)|(?:^|[\s(])\.\.?[\\/]|(?:^|[\s(])[^\s]+[\\/][^\s]+)")


def safe_error(value: object, fallback: str = "Workflow execution failed.") -> str:
    """Return a short failure suitable for task results and progress."""

    text = str(value or "").strip()
    if (
        not text
        or _SECRET.search(text)
        or _PATH.search(text)
        or "/" in text
        or "\\" in text
        or ".." in text
        or "traceback" in text.lower()
    ):
        return fallback
    text = text.replace("\n", " ").replace("\r", " ")
    return text[:240] or fallback


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return str(value)


def _thread_item_root(item: Any) -> Any:
    return getattr(item, "root", item)


def _response(items: list[Any]) -> str | None:
    fallback = None
    for item in reversed(items):
        root = _thread_item_root(item)
        if getattr(root, "type", None) != "agentMessage":
            continue
        value = getattr(root, "text", None)
        if not isinstance(value, str):
            continue
        phase = _json_value(getattr(root, "phase", None))
        if phase == "final_answer":
            return value
        if phase is None and fallback is None:
            fallback = value
    return fallback


def _safe_progress(method: str, payload: Any) -> tuple[str, str] | None:
    """Map SDK event names to a fixed, non-sensitive vocabulary."""

    mapping = {
        "turn/started": ("agent", "Codex started a workflow turn"),
        "turn/plan/updated": ("planning", "Codex updated the workflow plan"),
        "item/started": ("working", "Codex started a workflow work step"),
        "item/completed": ("working", "Codex completed a workflow work step"),
        "item/mcpToolCall/progress": ("working", "A Codex tool-assisted step is still in progress"),
        "thread/compacted": ("agent", "Codex compacted its context and is continuing"),
        "warning": ("warning", "Codex reported a warning"),
        "configWarning": ("warning", "Codex reported a configuration warning"),
        "guardianWarning": ("warning", "Codex reported a safety warning"),
        "error": ("warning", "Codex reported a recoverable runtime error"),
        "turn/completed": ("agent", "Codex completed a workflow turn"),
    }
    return mapping.get(method)


class ProgressReporter:
    """Serialize sanitized progress and emit periodic heartbeats."""

    def __init__(self, *, callback: ProgressCallback | None = None, path: Path | None = None, heartbeat_seconds: float = 60.0):
        self.callback = callback
        self.path = path
        self.heartbeat_seconds = heartbeat_seconds
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._started = asyncio.get_running_loop().time()

    async def emit(self, stage: str, message: str, *, sdk_event: str | None = None, heartbeat: bool = False) -> None:
        stage = stage if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(stage)) else "working"
        message = safe_error(message, "Workflow is in progress.")
        async with self._lock:
            self._sequence += 1
            event: dict[str, Any] = {
                "sequence": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "message": message,
                "heartbeat": heartbeat,
                "elapsed_seconds": round(asyncio.get_running_loop().time() - self._started, 1),
            }
            if sdk_event:
                event["sdk_event"] = sdk_event
            if self.path is not None:
                await asyncio.to_thread(self._append_event, event)
            if self.callback is not None:
                try:
                    callback_value = self.callback
                    if inspect.iscoroutinefunction(callback_value):
                        await callback_value(event.copy())
                    else:
                        result = await asyncio.to_thread(callback_value, event.copy())
                        if inspect.isawaitable(result):
                            await result
                except Exception:
                    # Telemetry is advisory and must not alter workflow
                    # outcome (or expose callback/provider details).
                    logger.debug("agentic progress callback failed")

    def _append_event(self, event: dict[str, Any]) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def heartbeat_loop(self, stop: asyncio.Event) -> None:
        while self.heartbeat_seconds > 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
                return
            except TimeoutError:
                if not stop.is_set():
                    await self.emit("heartbeat", "Workflow is still running.", heartbeat=True)


class CodexRunResult:
    """Result of one or more turns, retaining the full labeled audit."""

    def __init__(self, *, thread_id: str, audits: list[TurnAudit], response: str | None):
        self.thread_id = thread_id
        self.audits = audits
        self.response = response

    @property
    def last_turn(self) -> TurnAudit | None:
        return self.audits[-1] if self.audits else None


class CodexRunner:
    """Run labeled streamed turns under one total workflow deadline."""

    def __init__(self, *, codex_factory: Any = AsyncCodex, model: str | None = None, api_key: str | None = None, timeout_seconds: float = 2700.0, heartbeat_seconds: float = 60.0, backend_root: Path | None = None):
        self.codex_factory = codex_factory
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.backend_root = backend_root or Path(__file__).resolve().parents[3]

    async def run(self, workspace: Path, turns: Sequence[TurnRequest], *, skill_names: Sequence[str] = (), progress_callback: ProgressCallback | None = None, audit_path: Path | None = None, turn_callback: Callable[[TurnAudit, str | None], Any] | None = None) -> CodexRunResult:
        if not turns:
            raise WorkflowExecutionError("workflow did not provide a prompt")
        reporter = ProgressReporter(callback=progress_callback, path=audit_path.with_name("progress.jsonl") if audit_path else None, heartbeat_seconds=self.heartbeat_seconds)
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(reporter.heartbeat_loop(stop_heartbeat))
        started = datetime.now(timezone.utc)

        async def execute() -> CodexRunResult:
            await reporter.emit("starting", "Initializing Codex workflow execution")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(str(self.backend_root) for _ in [0]) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
            async with self.codex_factory(CodexConfig(env=environment, cwd=str(workspace))) as codex:
                if not self.api_key:
                    raise WorkflowExecutionError("Codex provider is not configured")
                await codex.login_api_key(self.api_key)
                thread = await codex.thread_start(cwd=str(workspace), model=self.model, sandbox=Sandbox.workspace_write, approval_mode=ApprovalMode.deny_all, ephemeral=True)
                await reporter.emit("agent", "Codex workflow thread is ready")
                audits: list[TurnAudit] = []
                response: str | None = None
                pending = list(turns)
                while pending:
                    turn_request = pending.pop(0)
                    turn_stage = turn_request.kind if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", turn_request.kind) else "working"
                    await reporter.emit(turn_stage, f"Codex {turn_request.kind} turn is running")
                    inputs: list[Any] = [SkillInput(name=name, path=str(workspace / ".agents" / "skills" / name / "SKILL.md")) for name in skill_names]
                    inputs.append(TextInput(turn_request.prompt))
                    # New SDKs accept per-turn sandbox/output schema.  The
                    # fallback keeps lightweight integrations built against
                    # the old single-argument test double compatible.
                    turn_kwargs: dict[str, Any] = {}
                    if turn_request.sandbox is not None:
                        turn_kwargs["sandbox"] = turn_request.sandbox
                    if turn_request.output_schema is not None:
                        turn_kwargs["output_schema"] = turn_request.output_schema
                    handle = None
                    turn_started = asyncio.get_running_loop().time()
                    try:
                        try:
                            handle = await thread.turn(inputs, **turn_kwargs)
                        except TypeError:
                            if turn_kwargs:
                                handle = await thread.turn(inputs)
                            else:
                                raise
                        audit, response = await self._collect_turn(handle, turn_request.kind, reporter)
                    except asyncio.CancelledError:
                        # ``wait_for`` cancels the running turn on deadline.
                        # Retain a failed record for the attempted turn before
                        # propagating cancellation to the workflow deadline
                        # handler.
                        audits.append(
                            TurnAudit(
                                turn_id=str(getattr(handle, "id", "unknown")),
                                kind=turn_request.kind,
                                status="failed",
                                duration_ms=round((asyncio.get_running_loop().time() - turn_started) * 1000),
                                response=None,
                            )
                        )
                        await self._persist_audits(audit_path, str(getattr(thread, "id", "unknown")), audits)
                        raise
                    except Exception as exc:
                        audits.append(
                            TurnAudit(
                                turn_id=str(getattr(handle, "id", "unknown")),
                                kind=turn_request.kind,
                                status="failed",
                                duration_ms=round((asyncio.get_running_loop().time() - turn_started) * 1000),
                                response=None,
                            )
                        )
                        await self._persist_audits(audit_path, str(getattr(thread, "id", "unknown")), audits)
                        raise WorkflowExecutionError("Codex turn failed.") from exc
                    audits.append(audit)
                    # Persist incrementally so a malformed review or a later
                    # SDK failure still leaves every completed turn audit for
                    # diagnosis and retention policy consumers.
                    await self._persist_audits(audit_path, str(getattr(thread, "id", "unknown")), audits)
                    if turn_callback is not None:
                        follow_up = turn_callback(audit, response)
                        if inspect.isawaitable(follow_up):
                            follow_up = await follow_up
                        if follow_up is not None:
                            if isinstance(follow_up, TurnRequest):
                                pending.append(follow_up)
                            else:
                                pending.extend(follow_up)
                result = CodexRunResult(thread_id=str(getattr(thread, "id", "unknown")), audits=audits, response=response)
                await self._persist_audits(audit_path, result.thread_id, audits)
                await reporter.emit("completed", "Codex workflow artifacts are ready")
                return result

        try:
            return await asyncio.wait_for(execute(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            await reporter.emit("failed", "Workflow timed out")
            raise WorkflowTimeoutError("Workflow timed out.") from exc
        except WorkflowExecutionError:
            await reporter.emit("failed", "Workflow execution failed")
            raise
        except Exception as exc:
            await reporter.emit("failed", "Workflow execution failed")
            raise WorkflowExecutionError("Workflow execution failed.") from exc
        finally:
            stop_heartbeat.set()
            await heartbeat

    @staticmethod
    async def _persist_audits(path: Path | None, thread_id: str, audits: list[TurnAudit]) -> None:
        if path is None:
            return
        payload = json.dumps(
            {"thread_id": thread_id, "turns": [item.model_dump(mode="json") for item in audits]},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, payload, encoding="utf-8")

    async def _collect_turn(self, handle: Any, kind: str, reporter: ProgressReporter) -> tuple[TurnAudit, str | None]:
        items: list[Any] = []
        usage = None
        completed = None
        async for event in handle.stream():
            method = str(getattr(event, "method", "unknown"))
            payload = getattr(event, "payload", None)
            progress = _safe_progress(method, payload)
            if progress:
                await reporter.emit(progress[0], progress[1], sdk_event=method)
            event_turn_id = getattr(payload, "turn_id", None)
            if method == "item/completed" and (event_turn_id is None or event_turn_id == getattr(handle, "id", None)):
                item = getattr(payload, "item", None)
                if item is not None:
                    items.append(item)
            elif method == "thread/tokenUsage/updated" and (event_turn_id is None or event_turn_id == getattr(handle, "id", None)):
                usage = getattr(payload, "token_usage", None)
            elif method == "turn/completed":
                candidate = getattr(payload, "turn", None)
                if candidate is None or getattr(candidate, "id", None) == getattr(handle, "id", None):
                    completed = candidate or payload
        if completed is None:
            raise WorkflowExecutionError("Codex turn did not complete")
        status = str(_json_value(getattr(completed, "status", None)))
        if status.split(".")[-1].lower() != "completed":
            raise WorkflowExecutionError("Codex turn failed")
        audit = TurnAudit(turn_id=str(getattr(completed, "id", getattr(handle, "id", "unknown"))), kind=kind, status=status, duration_ms=getattr(completed, "duration_ms", None), usage=_json_value(usage), response=_response(items))
        return audit, audit.response


async def run_codex_workflow(*args: Any, **kwargs: Any) -> CodexRunResult:
    """Functional convenience wrapper around :class:`CodexRunner`."""

    return await CodexRunner(**kwargs.pop("runner_options", {})).run(*args, **kwargs)


__all__ = ["CodexRunner", "CodexRunResult", "ProgressReporter", "WorkflowExecutionError", "WorkflowTimeoutError", "safe_error", "run_codex_workflow"]
