"""Operator API for persisted agent model settings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models.agent_settings import AgentStageSettings
from app.services import app_settings
from app.services.agentic.contracts import AgentReasoningEffort
from app.services.agentic.events import _safe_model_name
from app.services.agentic.model_settings import resolve_all_stage_settings
from app.services.agentic.registry import UnknownWorkflowError, workflow_registry

router = APIRouter(prefix="/agent-settings", tags=["agent settings"])


class StoredSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner: str | None = Field(max_length=32)
    model: str | None = Field(max_length=160)
    reasoning_effort: AgentReasoningEffort | None
    planner_enabled: StrictBool | None


def _providers() -> dict[str, bool]:
    return {
        "openai": bool(settings.openai_api_key.get_secret_value()),
        "gemini": bool(settings.gemini_api_key and settings.gemini_api_key.get_secret_value()),
        "anthropic": bool(settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value()),
    }


def _stages_for_workflow(workflow: str) -> tuple[str, ...]:
    """Resolve the declared model-setting stages for a registered workflow, or 404."""

    try:
        adapter = workflow_registry.get(workflow)
    except UnknownWorkflowError as exc:
        raise HTTPException(404, "找不到指定的工作流程。") from exc
    return tuple(getattr(adapter, "model_setting_stages", ()))


def _effective_settings(session: Session, workflow: str, stage_names: tuple[str, ...]) -> dict:
    resolved = resolve_all_stage_settings(session, workflow, stage_names)
    stages = {}
    rows = {
        row.stage: row
        for row in session.exec(
            select(AgentStageSettings).where(AgentStageSettings.workflow == workflow)
        ).all()
    }
    for stage in stage_names:
        value = resolved[stage]
        row = rows.get(stage)
        stored = {
            "runner": row.runner if row else None,
            "model": row.model if row else None,
            "reasoning_effort": row.reasoning_effort if row else None,
            "planner_enabled": row.planner_enabled if row and stage == "planner" else None,
        }
        effective = {
            "runner": {"value": value.runner, "source": "database" if stored["runner"] is not None else _source(stage, "runner")},
            "model": {"value": value.model, "source": "database" if stored["model"] is not None else _source(stage, "model")},
            "reasoning_effort": {
                "value": value.reasoning_effort.value,
                "source": "database" if stored["reasoning_effort"] is not None else _source(stage, "reasoning_effort"),
            },
        }
        if stage == "planner":
            effective["planner_enabled"] = {
                "value": value.planner_enabled,
                "source": "database" if stored["planner_enabled"] is not None else app_settings.effective("agents.stages.planner.enabled", session)[1],
            }
        stages[stage] = {
            "stored": stored,
            "effective": effective,
            "allowed_runners": ["codex", "agents"] if stage == "planner" else ["codex"],
        }
    return {"stages": stages, "providers": _providers()}


def _source(stage: str, field: str) -> str:
    value = getattr(settings, f"agent_{stage}_{field}")
    if field in ("model", "reasoning_effort") and value is None:
        return "default"
    return app_settings.field_source(f"agent_{stage}_{field}")


def _environment_value(stage: str, field: str):
    value = getattr(settings, f"agent_{stage}_{field}")
    if field == "model":
        return value or settings.agent_default_model
    if field == "reasoning_effort":
        return value or settings.agent_default_reasoning_effort
    return value


def _validate_pair(stage: str, runner: str, model: str) -> None:
    if runner not in ({"codex", "agents"} if stage == "planner" else {"codex"}):
        raise HTTPException(422, "此階段不允許所選執行器。")
    if _safe_model_name(model) is None:
        raise HTTPException(422, "模型名稱格式無效。")
    if runner == "codex":
        if model.startswith("litellm/"):
            raise HTTPException(422, "Codex 執行器不支援 LiteLLM 模型。")
        return
    if not model.startswith("litellm/"):
        raise HTTPException(422, "Agents 執行器必須使用 LiteLLM 模型。")
    provider = model.removeprefix("litellm/").partition("/")[0]
    key = {
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
        "anthropic": settings.anthropic_api_key,
    }.get(provider)
    if key is None or not key.get_secret_value():
        raise HTTPException(422, "所選模型提供者尚未設定 API 金鑰。")


@router.get("/{workflow}")
def get_agent_settings(workflow: str, session: Session = Depends(get_session)):
    stage_names = _stages_for_workflow(workflow)
    return _effective_settings(session, workflow, stage_names)


@router.put("/{workflow}/{stage}")
def put_agent_settings(workflow: str, stage: str, body: Any = Body(None), session: Session = Depends(get_session)):
    # TODO(auth): restrict model-setting changes to authenticated operators.
    stage_names = _stages_for_workflow(workflow)
    if stage not in stage_names:
        raise HTTPException(422, "設定階段無效。")
    try:
        submitted = StoredSettings.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, "設定欄位或推理程度無效，請檢查後重試。") from exc
    if stage != "planner" and submitted.planner_enabled is not None:
        raise HTTPException(422, "只有規劃階段可以設定規劃開關。")
    runner = submitted.runner if submitted.runner is not None else _environment_value(stage, "runner")
    model = submitted.model if submitted.model is not None else _environment_value(stage, "model")
    _validate_pair(stage, runner, model)
    row = session.get(AgentStageSettings, (workflow, stage))
    if row is None:
        row = AgentStageSettings(workflow=workflow, stage=stage)
        session.add(row)
    row.runner = submitted.runner
    row.model = submitted.model
    row.reasoning_effort = submitted.reasoning_effort.value if submitted.reasoning_effort is not None else None
    row.planner_enabled = submitted.planner_enabled if stage == "planner" else None
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return _effective_settings(session, workflow, stage_names)
