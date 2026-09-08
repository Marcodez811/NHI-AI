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
import shutil
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from pydantic import SecretStr
from loguru import logger
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox, SkillInput, TextInput

from .contracts import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentPhase,
    AgentReasoningEffort,
    AgentRunner,
    ProgressCallback,
    TurnAudit,
    TurnRequest,
)


class WorkflowExecutionError(RuntimeError):
    """Operational failure with a message safe to expose to callers."""


class WorkflowTimeoutError(WorkflowExecutionError):
    """The total workflow deadline elapsed."""


class ProcessIsolationError(WorkflowExecutionError):
    """Raised when a requested provider process isolation boundary is unavailable."""


def bwrap_available() -> bool:
    """Return whether the Linux bubblewrap executable is available."""

    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def build_bwrap_launch_args(
    workspace: Path,
    *,
    codex_bin: Path,
    writable: bool,
    backend_root: Path | None = None,
    skill_names: Sequence[str] = (),
    hidden_paths: Sequence[Path] = (),
    read_only_paths: Sequence[Path] = (),
    writable_paths: Sequence[Path] = (),
    restrict_workspace: bool = False,
) -> tuple[str, ...]:
    """Build a closed launch command for the Codex app-server.

    Bubblewrap receives the provider command directly through
    ``CodexConfig.launch_args_override``.  The base filesystem is read-only,
    while only the stage workspace is writable for author/extraction turns.
    Reviewer turns use a read-only workspace and mask source/history paths.
    A missing executable or an out-of-workspace hidden path is rejected by the
    caller instead of silently falling back to ``full_access``.
    """

    workspace = workspace.resolve(strict=True)
    backend = Path(backend_root).resolve(strict=True) if backend_root is not None else None

    def contained(path: Path, root: Path, label: str) -> Path:
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ProcessIsolationError(f"{label} escapes the workflow workspace") from exc
        return resolved

    args: list[str] = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]

    # Hide common host data roots before selectively remounting the backend
    # runtime and this activation's allowlisted workspace paths. This prevents
    # a stage from discovering sibling jobs or the shared document volume.
    masked_roots = ("/home", "/root", "/data", "/mnt", "/media", "/workspace", "/tmp", "/run")
    for root in masked_roots:
        if Path(root).exists():
            args.extend(("--tmpfs", root))
    args.extend(("--dir", "/tmp/codex-home"))

    if backend is not None:
        args.extend(("--dir", str(backend.parent)))
        args.extend(("--ro-bind", str(backend), str(backend)))
        skill_root = backend / ".agents"
        if skill_root.exists():
            args.extend(("--tmpfs", str(skill_root)))

    if restrict_workspace:
        # The workspace directory itself is an empty mount point. Only the
        # paths listed below become visible inside the namespace.
        args.extend(("--dir", str(workspace.parent)))
        args.extend(("--tmpfs", str(workspace)))
        for raw_path in read_only_paths:
            path = contained(Path(raw_path), workspace, "read-only stage path")
            args.extend(("--dir", str(path.parent)))
            args.extend(("--ro-bind", str(path), str(path)))
        for raw_path in writable_paths:
            path = contained(Path(raw_path), workspace, "writable stage path")
            args.extend(("--dir", str(path.parent)))
            args.extend(("--bind", str(path), str(path)))
        for name in skill_names:
            skill_path = contained(workspace / ".agents" / "skills" / name, workspace, "skill path")
            args.extend(("--dir", str(skill_path.parent)))
            args.extend(("--ro-bind", str(skill_path), str(skill_path)))
    elif writable:
        args.extend(("--bind", str(workspace), str(workspace)))
    else:
        args.extend(("--ro-bind", str(workspace), str(workspace)))

    if not restrict_workspace:
        for raw_path in hidden_paths:
            path = Path(raw_path).resolve()
            if not path.exists():
                continue
            # Masking a directory avoids accidentally exposing new files created
            # between policy construction and process launch.
            if path.is_dir():
                args.extend(("--tmpfs", str(path)))
            else:
                args.extend(("--ro-bind", "/dev/null", str(path)))
    if not restrict_workspace:
        for raw_path in read_only_paths:
            path = Path(raw_path).resolve()
            if not path.exists():
                continue
            # A later mount overrides the writable workspace bind, protecting
            # immutable evidence and extracted assets from author revisions.
            args.extend(("--ro-bind", str(path), str(path)))
    args.extend((str(codex_bin), "app-server", "--listen", "stdio://"))
    return tuple(args)


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


def _turn_phase(kind: str) -> AgentPhase:
    """Translate a labeled turn into the stable public phase vocabulary."""

    normalized = str(kind or "").strip().lower()
    if normalized in {"correction", "revision", "revising"}:
        return AgentPhase.REVISING
    if normalized in {"review", "reviewer", "reviewing"}:
        return AgentPhase.REVIEWING
    if normalized in {phase.value for phase in AgentPhase}:
        return AgentPhase(normalized)
    return AgentPhase.DRAFTING


class ProgressReporter:
    """Serialize sanitized progress and emit periodic heartbeats."""

    def __init__(self, *, callback: ProgressCallback | None = None, path: Path | None = None, heartbeat_seconds: float = 10.0):
        self.callback = callback
        self.path = path
        self.heartbeat_seconds = heartbeat_seconds
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._started = asyncio.get_running_loop().time()
        self._phase = AgentPhase.PREPARING

    async def emit(
        self,
        stage: str,
        message: str,
        *,
        phase: AgentPhase | str | None = None,
        sdk_event: str | None = None,
        heartbeat: bool = False,
    ) -> None:
        stage = stage if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(stage)) else "working"
        message = safe_error(message, "Workflow is in progress.")
        if phase is not None:
            try:
                self._phase = AgentPhase(phase)
            except ValueError:
                # Provider or adapter labels never become public phase values.
                pass
        async with self._lock:
            self._sequence += 1
            event: dict[str, Any] = {
                "sequence": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "message": message,
                "heartbeat": heartbeat,
                "event_type": "heartbeat" if heartbeat else "node_progress",
                "elapsed_seconds": round(asyncio.get_running_loop().time() - self._started, 1),
                "phase": self._phase.value,
            }
            if sdk_event:
                event["sdk_event"] = sdk_event
            if self.path is not None:
                await asyncio.to_thread(self._append_event, event)
            if self.callback is not None:
                try:
                    callback_value = self.callback
                    # SDK event names are audit details.  They are written to
                    # the server-side progress log but never sent through the
                    # public progress callback.
                    public_event = event.copy()
                    public_event.pop("sdk_event", None)
                    if inspect.iscoroutinefunction(callback_value):
                        await callback_value(public_event)
                    else:
                        result = await asyncio.to_thread(callback_value, public_event)
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

    def __init__(
        self,
        *,
        codex_factory: Any = AsyncCodex,
        model: str | None = None,
        reasoning_effort: AgentReasoningEffort | None = AgentReasoningEffort.HIGH,
        api_key: SecretStr | None = None,
        timeout_seconds: float = 2700.0,
        heartbeat_seconds: float = 10.0,
        backend_root: Path | None = None,
        require_process_isolation: bool = False,
    ):
        self.codex_factory = codex_factory
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.backend_root = backend_root or Path(__file__).resolve().parents[3]
        self.require_process_isolation = require_process_isolation

    async def run(
        self,
        workspace: Path,
        turns: Sequence[TurnRequest],
        *,
        model: str | None = None,
        reasoning_effort: AgentReasoningEffort | None = None,
        skill_names: Sequence[str] = (),
        progress_callback: ProgressCallback | None = None,
        audit_path: Path | None = None,
        turn_callback: Callable[[TurnAudit, str | None], Any] | None = None,
        hidden_paths: Sequence[Path] = (),
        read_only_paths: Sequence[Path] = (),
        writable_paths: Sequence[Path] = (),
        restrict_workspace: bool = False,
    ) -> CodexRunResult:
        if not turns:
            raise WorkflowExecutionError("workflow did not provide a prompt")
        reporter = ProgressReporter(callback=progress_callback, path=audit_path.with_name("progress.jsonl") if audit_path else None, heartbeat_seconds=self.heartbeat_seconds)
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(reporter.heartbeat_loop(stop_heartbeat))
        started = datetime.now(timezone.utc)

        async def execute() -> CodexRunResult:
            environment = os.environ.copy()
            # The API key is supplied through the explicit login call. Do not
            # inherit ambient credentials into a stage process.
            for key in tuple(environment):
                if any(token in key.upper() for token in ("API_KEY", "TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")):
                    environment.pop(key, None)
            environment["PYTHONPATH"] = os.pathsep.join(str(self.backend_root) for _ in [0]) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
            config_kwargs: dict[str, Any] = {"env": environment, "cwd": str(workspace)}
            if self.require_process_isolation:
                if not bwrap_available():
                    raise ProcessIsolationError("provider process isolation is unavailable")
                bundled = sorted(self.backend_root.glob(".venv/lib/python*/site-packages/codex_cli_bin/bin/codex"))
                codex_bin = bundled[0] if bundled else Path(shutil.which("codex") or "")
                if not codex_bin.is_file():
                    raise ProcessIsolationError("Codex provider binary is unavailable for isolated launch")
                config_kwargs["launch_args_override"] = build_bwrap_launch_args(
                    workspace,
                    codex_bin=codex_bin,
                    writable=turns[0].sandbox is not Sandbox.read_only,
                    backend_root=self.backend_root,
                    skill_names=skill_names,
                    hidden_paths=hidden_paths,
                    read_only_paths=read_only_paths,
                    writable_paths=writable_paths,
                    restrict_workspace=restrict_workspace,
                )
                environment["HOME"] = "/tmp/codex-home"
            async with self.codex_factory(CodexConfig(**config_kwargs)) as codex:
                if not self.api_key:
                    raise WorkflowExecutionError("Codex provider is not configured")
                api_key = self.api_key.get_secret_value()
                await codex.login_api_key(api_key)
                # The first turn determines the session's baseline capability.
                # Reviewer requests therefore start read-only sessions instead
                # of relying only on a per-turn hint.
                session_sandbox = turns[0].sandbox or Sandbox.workspace_write
                effective_model = model if model is not None else self.model
                effective_effort = reasoning_effort if reasoning_effort is not None else self.reasoning_effort
                thread_config = (
                    {"model_reasoning_effort": getattr(effective_effort, "value", effective_effort)}
                    if effective_effort is not None
                    else None
                )
                thread = await codex.thread_start(cwd=str(workspace), model=effective_model, config=thread_config, sandbox=session_sandbox, approval_mode=ApprovalMode.deny_all, ephemeral=True)
                audits: list[TurnAudit] = []
                response: str | None = None
                pending = list(turns)
                while pending:
                    turn_request = pending.pop(0)
                    turn_phase = _turn_phase(turn_request.kind)
                    await reporter.emit(
                        turn_phase.value,
                        f"Agent {turn_request.kind} step is running.",
                        phase=turn_phase,
                    )
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
                                error=None,
                                prompt=turn_request.prompt,
                            )
                        )
                        await self._persist_audits(audit_path, str(getattr(thread, "id", "unknown")), audits)
                        raise
                    except Exception as exc:
                        error_message = safe_error(str(exc), "Codex turn failed.")
                        audits.append(
                            TurnAudit(
                                turn_id=str(getattr(handle, "id", "unknown")),
                                kind=turn_request.kind,
                                status="failed",
                                duration_ms=round((asyncio.get_running_loop().time() - turn_started) * 1000),
                                response=None,
                                error=error_message,
                                prompt=turn_request.prompt,
                            )
                        )
                        await self._persist_audits(audit_path, str(getattr(thread, "id", "unknown")), audits)
                        if isinstance(exc, WorkflowExecutionError):
                            raise
                        raise WorkflowExecutionError(error_message) from exc
                    audit = audit.model_copy(update={"prompt": turn_request.prompt})
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
                return result

        try:
            return await asyncio.wait_for(execute(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            await reporter.emit(AgentPhase.FAILED.value, "Workflow timed out.", phase=AgentPhase.FAILED)
            raise WorkflowTimeoutError("Workflow timed out.") from exc
        except WorkflowExecutionError:
            await reporter.emit(AgentPhase.FAILED.value, "Workflow execution failed.", phase=AgentPhase.FAILED)
            raise
        except Exception as exc:
            await reporter.emit(AgentPhase.FAILED.value, "Workflow execution failed.", phase=AgentPhase.FAILED)
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
        turn_phase = _turn_phase(kind)
        async for event in handle.stream():
            method = str(getattr(event, "method", "unknown"))
            payload = getattr(event, "payload", None)
            progress = _safe_progress(method, payload)
            if progress:
                await reporter.emit(
                    turn_phase.value,
                    progress[1],
                    phase=turn_phase,
                    sdk_event=method,
                )
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
            provider_error = getattr(completed, "error", None)
            provider_message = getattr(provider_error, "message", None) or str(provider_error or "")
            raise WorkflowExecutionError(safe_error(provider_message, "Codex turn failed."))
        audit = TurnAudit(turn_id=str(getattr(completed, "id", getattr(handle, "id", "unknown"))), kind=kind, status=status, duration_ms=getattr(completed, "duration_ms", None), usage=_json_value(usage), response=_response(items), error=None)
        return audit, audit.response


class CodexAgentRunner:
    """Provider-neutral runner backed by the existing :class:`CodexRunner`.

    ``CodexRunner`` remains the compatibility/operational primitive and keeps
    its original ``run(workspace, turns, ...)`` API.  This adapter deliberately
    creates one Codex thread per ``AgentExecutionRequest`` so logical author,
    reviewer, and revision activations are independent provider sessions.
    """

    name = "codex"

    def __init__(self, codex_runner: CodexRunner | None = None, **runner_options: Any):
        self.codex_runner = codex_runner or CodexRunner(**runner_options)

    @property
    def timeout_seconds(self) -> float | None:
        return getattr(self.codex_runner, "timeout_seconds", None)

    async def run(
        self,
        request: AgentExecutionRequest,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> AgentExecutionResult:
        started = asyncio.get_running_loop().time()
        effective_model = request.model if request.model is not None else getattr(self.codex_runner, "model", None)
        effective_effort = request.reasoning_effort if request.reasoning_effort is not None else getattr(self.codex_runner, "reasoning_effort", None)
        turn = TurnRequest(
            kind=request.node_id if request.node_id else request.role,
            prompt=request.prompt,
            sandbox=request.sandbox,
            output_schema=request.output_schema,
        )

        async def forward_progress(event: dict[str, Any]) -> None:
            if progress_callback is None:
                return
            enriched = dict(event)
            enriched.setdefault("event_type", "heartbeat" if event.get("heartbeat") else "node_progress")
            enriched.update(
                {
                    "node_id": request.node_id,
                    "role": request.role,
                    "runner": self.name,
                    "model": effective_model,
                    "reasoning_effort": getattr(effective_effort, "value", effective_effort),
                    "attempt": request.attempt,
                }
            )
            result = progress_callback(enriched)
            if inspect.isawaitable(result):
                await result

        result = await self.codex_runner.run(
            request.workspace,
            [turn],
            model=request.model,
            reasoning_effort=request.reasoning_effort,
            skill_names=request.skill_names,
            progress_callback=forward_progress,
            audit_path=request.audit_path,
            hidden_paths=request.hidden_paths,
            read_only_paths=request.read_only_paths,
            writable_paths=request.writable_paths,
            restrict_workspace=request.restrict_workspace,
        )
        duration_ms = round((asyncio.get_running_loop().time() - started) * 1000)
        usage = result.last_turn.usage if result.last_turn is not None else None
        return AgentExecutionResult(
            provider_run_id=result.thread_id,
            response=result.response,
            duration_ms=duration_ms,
            usage=usage,
            audits=result.audits,
        )


class RunnerRegistryError(ValueError):
    """Base class for runner allowlist errors."""


class UnknownRunnerError(RunnerRegistryError):
    """Raised when a server-defined plan names an unavailable runner."""


class RunnerRegistry:
    """Explicit allowlist for provider-neutral agent runners."""

    def __init__(self, runners: dict[str, AgentRunner] | None = None):
        self._runners: dict[str, AgentRunner] = {}
        for name, runner in (runners or {}).items():
            self.register(name, runner)

    def register(self, name: str, runner: AgentRunner) -> AgentRunner:
        if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise RunnerRegistryError("runner names must be simple names")
        runner_name = getattr(runner, "name", name) or name
        if runner_name != name:
            raise RunnerRegistryError("runner name does not match runner name")
        if not callable(getattr(runner, "run", None)):
            raise RunnerRegistryError("runner must provide an async run method")
        if name in self._runners:
            raise RunnerRegistryError(f"runner is already registered: {name}")
        self._runners[name] = runner
        return runner

    def resolve(self, name: str) -> AgentRunner:
        try:
            return self._runners[name]
        except KeyError as exc:
            raise UnknownRunnerError(f"unknown runner: {name}") from exc

    def get(self, name: str) -> AgentRunner:
        return self.resolve(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._runners))

    def __contains__(self, name: object) -> bool:
        return name in self._runners


runner_registry = RunnerRegistry({"codex": CodexAgentRunner()})


async def run_codex_workflow(*args: Any, **kwargs: Any) -> CodexRunResult:
    """Functional convenience wrapper around :class:`CodexRunner`."""

    return await CodexRunner(**kwargs.pop("runner_options", {})).run(*args, **kwargs)


__all__ = [
    "AgentRunner", "CodexAgentRunner", "CodexRunner", "CodexRunResult",
    "ProgressReporter", "RunnerRegistry", "RunnerRegistryError",
    "UnknownRunnerError", "runner_registry", "WorkflowExecutionError",
    "WorkflowTimeoutError", "ProcessIsolationError", "bwrap_available",
    "build_bwrap_launch_args", "safe_error", "run_codex_workflow",
]
