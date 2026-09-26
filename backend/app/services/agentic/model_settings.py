"""Resolve, persist, and snapshot per-stage agent model policy."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.config import settings
from app.models.agent_settings import AgentStageSettings
from app.services.agentic.contracts import AgentReasoningEffort

# Every stage name any workflow may declare. A given workflow only resolves,
# stores, and snapshots the subset it declares via its adapter's
# ``model_setting_stages`` -- see ``app.services.agentic.registry``.
STAGES = ("extraction", "planner", "author", "reviewer")
_SNAPSHOT = Path("work") / "model_settings.json"


@dataclass(frozen=True)
class StageModelSettings:
    runner: str
    model: str
    reasoning_effort: AgentReasoningEffort
    planner_enabled: bool | None


def _base_effective(stage: str) -> dict[str, tuple[Any, str]]:
    runner = getattr(settings, f"agent_{stage}_runner")
    configured_model = getattr(settings, f"agent_{stage}_model")
    model = configured_model or settings.agent_default_model
    configured_effort = getattr(settings, f"agent_{stage}_reasoning_effort")
    effort = configured_effort or settings.agent_default_reasoning_effort
    result = {
        "runner": (runner, "env"),
        "model": (
            model,
            "env" if configured_model is not None else "default",
        ),
        "reasoning_effort": (
            effort,
            "env" if configured_effort is not None else "default",
        ),
    }
    if stage == "planner":
        result["planner_enabled"] = (
            settings.agent_planner_enabled,
            "env",
        )
    return result


def resolve_all_stage_settings(
    session: Session, workflow: str, stages: Sequence[str]
) -> dict[str, StageModelSettings]:
    """Resolve DB overrides over environment settings and built-in defaults.

    Only the given workflow's ``(workflow, stage)`` rows are consulted, so
    e.g. a slides author override never affects the news author stage.
    """

    stored_rows = {
        row.stage: row
        for row in session.exec(
            select(AgentStageSettings).where(AgentStageSettings.workflow == workflow)
        ).all()
        if row.stage in stages
    }
    resolved: dict[str, StageModelSettings] = {}
    for stage in stages:
        base = _base_effective(stage)
        row = stored_rows.get(stage)
        values = {
            key: (getattr(row, key), "database") if row is not None and getattr(row, key) is not None else value
            for key, value in base.items()
        }
        resolved[stage] = StageModelSettings(
            runner=str(values["runner"][0]),
            model=str(values["model"][0]),
            reasoning_effort=AgentReasoningEffort(values["reasoning_effort"][0]),
            planner_enabled=bool(values["planner_enabled"][0]) if stage == "planner" else None,
        )
    return resolved


def write_snapshot(workspace: Path, values: dict[str, StageModelSettings]) -> None:
    """Atomically write the resolved settings in a JSON-safe format."""

    destination = workspace / _SNAPSHOT
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        stage: {
            "runner": value.runner,
            "model": value.model,
            "reasoning_effort": value.reasoning_effort.value,
            "planner_enabled": value.planner_enabled,
        }
        for stage, value in values.items()
    }
    descriptor, temporary = tempfile.mkstemp(prefix="model_settings.json.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_snapshot(workspace: Path, stages: Sequence[str]) -> dict[str, StageModelSettings] | None:
    """Load a prior settings snapshot; malformed or incomplete data is rejected.

    ``stages`` is the resuming job's workflow's declared stages. An older
    snapshot written when every workflow shared one 4-stage shape may carry
    extra stages the current workflow does not declare (e.g. a news job
    resuming a snapshot written before news had its own stage list); those
    extras are ignored rather than rejected.
    """

    path = workspace / _SNAPSHOT
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not set(stages) <= set(raw):
            raise ValueError("Invalid agent settings snapshot stages")
        values = {}
        for stage in stages:
            value = raw[stage]
            if not isinstance(value, dict) or set(value) != {
                "runner", "model", "reasoning_effort", "planner_enabled"
            }:
                raise ValueError("Invalid agent settings snapshot shape")
            if (
                not isinstance(value["runner"], str)
                or not isinstance(value["model"], str)
                or not isinstance(value["reasoning_effort"], str)
                or (value["planner_enabled"] is not None and not isinstance(value["planner_enabled"], bool))
                or (stage != "planner" and value["planner_enabled"] is not None)
            ):
                raise ValueError("Invalid agent settings snapshot values")
            values[stage] = StageModelSettings(
                runner=value["runner"],
                model=value["model"],
                reasoning_effort=AgentReasoningEffort(value["reasoning_effort"]),
                planner_enabled=value["planner_enabled"],
            )
        return values
    except (json.JSONDecodeError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise ValueError("Malformed agent settings snapshot") from exc
