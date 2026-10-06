"""Editable settings: registry, validation and the live resolver.

Resolution order for every key (first source with a value wins)::

    database override (settings page) > .env / environment > config.yaml > code default

Call sites read through :func:`value` / :func:`effective` at use time (never at
import) so a database override applies in every container without a restart.
Keys that are consumed once at worker/scheduler start-up are flagged
``restart_required``; their override is stored but only takes effect then.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.config import YAML_FIELD_MAP, Settings, config_file_path, load_yaml_values, settings
from app.models.app_settings import AppSettingOverride

logger = logging.getLogger(__name__)

KEY_TO_FIELD: dict[str, str] = {".".join(path): name for path, name in YAML_FIELD_MAP.items()}


class SettingValidationError(ValueError):
    """A submitted value is invalid; the message is user-facing Chinese."""


@dataclass(frozen=True)
class SettingDef:
    key: str
    type: str  # int | float | bool | enum | model | model_list
    level: str  # user | dev
    group: str
    label_zh: str
    description_zh: str
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] | None = None
    unit_zh: str | None = None
    restart_required: bool = False
    field_name: str | None = field(default=None)

    @property
    def constraints(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        if self.choices is not None:
            out["choices"] = list(self.choices)
        return out


GROUP_LABELS = {
    "chat": "對話",
    "chat_dev": "對話（進階）",
    "agents": "Agent 流程",
    "uploads": "上傳限制",
    "cleanup": "檔案清理",
}


def _d(key, type_, level, group, label, desc, **kw) -> SettingDef:
    return SettingDef(key, type_, level, group, label, desc, field_name=KEY_TO_FIELD.get(key), **kw)


_SEC = "秒"
_BYTES = "位元組"
REGISTRY: tuple[SettingDef, ...] = (
    _d("chat.default_model", "model", "user", "chat", "預設對話模型", "新對話預設使用的模型。"),
    _d("chat.hidden_models", "model_list", "user", "chat", "對話中可選的模型", "關閉的模型不會出現在對話的模型選單中。"),
    _d("chat.timeout_seconds", "float", "dev", "chat_dev", "對話逾時", "單次對話回覆的最長等待時間。", min=1, max=3600, unit_zh=_SEC),
    _d("chat.compaction_threshold", "float", "dev", "chat_dev", "壓縮門檻", "上下文使用量達模型視窗的此比例時，自動壓縮較早的訊息。", min=0.05, max=0.95),
    _d("agents.stages.planner.enabled", "bool", "dev", "agents", "啟用大綱規劃", "投影片流程是否先產生大綱並等待核准；各流程的模型設定頁可再個別覆蓋。"),
    _d("agents.limits.timeout_minutes", "int", "dev", "agents", "Agent 逾時", "單一 Agent 任務的最長執行時間。", min=1, max=1440, unit_zh="分鐘"),
    _d("agents.limits.heartbeat_seconds", "float", "dev", "agents", "心跳間隔", "Agent 執行期間回報進度的間隔。", min=1, max=300, unit_zh=_SEC),
    _d("agents.limits.max_author_attempts", "int", "dev", "agents", "撰寫次數上限", "作者階段在審查未通過時最多重寫的次數。", min=1, max=20),
    _d("agents.limits.review_stagnation_limit", "int", "dev", "agents", "審查停滯上限", "連續幾輪審查沒有進展即停止。", min=1, max=20),
    _d("agents.limits.planner_max_evidence_chars", "int", "dev", "agents", "規劃證據字數上限", "規劃階段可納入提示的證據文字總字數。", min=1000, max=2_000_000, unit_zh="字"),
    _d("agents.limits.awaiting_outline_ttl_seconds", "int", "dev", "agents", "大綱等待期限", "等待大綱核准的任務超過此時間即過期並回收工作區。", min=60, max=31_536_000, unit_zh=_SEC),
    _d("agents.limits.awaiting_outline_sweep_interval_seconds", "int", "dev", "agents", "大綱過期掃描間隔", "排程器多久檢查一次過期的大綱。", min=10, max=86_400, unit_zh=_SEC, restart_required=True),
    _d("agents.limits.event_retention_seconds", "int", "dev", "agents", "事件保留時間", "Agent 診斷事件在 Redis 中的保留時間。", min=1, max=2_592_000, unit_zh=_SEC, restart_required=True),
    _d("agents.limits.keep_workspace_on_failure", "bool", "dev", "agents", "失敗時保留工作區", "任務失敗後保留工作區以便除錯。"),
    _d("agents.limits.require_process_isolation", "bool", "dev", "agents", "要求程序隔離", "必須在隔離沙箱中執行 Agent。"),
    _d("uploads.max_upload_bytes", "int", "dev", "uploads", "知識庫文件大小上限", "單一知識庫文件的大小上限。", min=1024, max=10 * 1024**3, unit_zh=_BYTES),
    _d("uploads.chat.max_attachments", "int", "dev", "uploads", "對話附件數量上限", "單則訊息可附加的檔案數量。", min=1, max=50),
    _d("uploads.chat.max_message_chars", "int", "dev", "uploads", "訊息字數上限", "單則訊息的最大字數。", min=1, max=1_000_000, unit_zh="字"),
    _d("uploads.chat.max_document_bytes", "int", "dev", "uploads", "對話文件大小上限", "對話附件（文件）的大小上限。", min=1024, max=2 * 1024**3, unit_zh=_BYTES),
    _d("uploads.chat.max_pdf_bytes", "int", "dev", "uploads", "PDF 大小上限", "對話附件 PDF 的大小上限。", min=1024, max=2 * 1024**3, unit_zh=_BYTES),
    _d("uploads.chat.max_pdf_pages", "int", "dev", "uploads", "PDF 頁數上限", "對話附件 PDF 的頁數上限。", min=1, max=5000, unit_zh="頁"),
    _d("uploads.chat.max_image_bytes", "int", "dev", "uploads", "圖片大小上限", "對話附件圖片的大小上限。", min=1024, max=1024**3, unit_zh=_BYTES),
    _d("uploads.chat.max_image_edge", "int", "dev", "uploads", "圖片最長邊", "圖片超過此長度時會等比例縮小。", min=64, max=16384, unit_zh="像素"),
    _d("cleanup.lease_seconds", "int", "dev", "cleanup", "清理租約時間", "清理工作被領取後，多久未完成視為逾時。", min=1, max=86_400, unit_zh=_SEC),
    _d("cleanup.retry_base_seconds", "float", "dev", "cleanup", "重試起始間隔", "清理失敗後的第一次重試延遲，之後指數成長。", min=1, max=86_400, unit_zh=_SEC),
    _d("cleanup.retry_max_seconds", "float", "dev", "cleanup", "重試最長間隔", "清理重試延遲的上限。", min=1, max=604_800, unit_zh=_SEC),
    _d("cleanup.reconcile_interval_seconds", "int", "dev", "cleanup", "對帳間隔", "多久重新掃描一次待清理的資源。", min=10, max=86_400, unit_zh=_SEC, restart_required=True),
    _d("cleanup.reconcile_batch_size", "int", "dev", "cleanup", "對帳批次大小", "每次對帳最多處理的筆數。", min=1, max=10_000, unit_zh="筆"),
)
BY_KEY: dict[str, SettingDef] = {d.key: d for d in REGISTRY}


def get_def(key: str) -> SettingDef | None:
    return BY_KEY.get(key)


# ── Resolver ────────────────────────────────────────────────────────────────


def _env_names(field_name: str) -> list[str]:
    info: FieldInfo = Settings.model_fields[field_name]
    alias = info.validation_alias
    choices = getattr(alias, "choices", None)
    names = [str(c) for c in choices] if choices else [str(alias)] if isinstance(alias, str) else [field_name]
    return [n.upper() for n in names]


def _dotenv_keys() -> set[str]:
    try:
        from dotenv import dotenv_values

        path = Settings.model_config.get("env_file") or ".env"
        if Path(str(path)).is_file():
            return {k.upper() for k, v in dotenv_values(str(path)).items() if v is not None}
    except Exception:  # pragma: no cover - best effort
        pass
    return set()


def _env_is_set(field_name: str) -> bool:
    names = _env_names(field_name)
    upper_env = {k.upper() for k in os.environ}
    if any(n in upper_env for n in names):
        return True
    return any(n in _dotenv_keys() for n in names)


def _yaml_has(field_name: str) -> bool:
    try:
        return field_name in load_yaml_values(config_file_path())
    except ValueError:
        return False


def field_source(field_name: str) -> str:
    """env | config | default for a plain ``Settings`` field (no database layer)."""

    if _env_is_set(field_name):
        return "env"
    return "config" if _yaml_has(field_name) else "default"


def base(key: str, cfg: Settings | None = None) -> tuple[Any, str]:
    """The value below the database layer: env > config.yaml > code default."""

    d = BY_KEY[key]
    if d.field_name is None:  # chat.hidden_models has no env/yaml field
        return [], "default"
    cfg = cfg or settings
    current = getattr(cfg, d.field_name)
    if hasattr(current, "value"):  # enums
        current = current.value
    if d.key == "chat.default_model" and current is None:
        current = cfg.agent_default_model
    return current, field_source(d.field_name)


def _overrides(session: Session | None) -> dict[str, Any]:
    try:
        if session is not None:
            return {row.key: row.value for row in session.exec(select(AppSettingOverride)).all()}
        from app import db

        with Session(db.engine) as own:
            return {row.key: row.value for row in own.exec(select(AppSettingOverride)).all()}
    except SQLAlchemyError:
        # Schema not migrated (e.g. unit tests) -> behave as "no overrides",
        # but say so: a broken database must not silently disable overrides.
        logger.warning("app setting overrides unavailable; using .env/config.yaml values", exc_info=True)
        return {}


def effective(key: str, session: Session | None = None, cfg: Settings | None = None) -> tuple[Any, str]:
    """Return ``(value, source)`` with source in database|env|config|default."""

    overrides = _overrides(session)
    if key in overrides:
        return overrides[key], "database"
    return base(key, cfg)


def value(key: str, session: Session | None = None, cfg: Settings | None = None) -> Any:
    return effective(key, session, cfg)[0]


def effective_all(session: Session | None = None) -> dict[str, tuple[Any, str]]:
    overrides = _overrides(session)
    return {d.key: ((overrides[d.key], "database") if d.key in overrides else base(d.key)) for d in REGISTRY}


# ── Validation / persistence ────────────────────────────────────────────────


def _bad(msg: str) -> SettingValidationError:
    return SettingValidationError(msg)


def _chat_models():
    from app.services.chat import models as chat_models

    return chat_models.list_models()


def validate(d: SettingDef, raw: Any, session: Session | None = None) -> Any:
    """Coerce and validate ``raw`` against the registry; raise Chinese errors."""

    label = d.label_zh
    if d.type == "bool":
        if not isinstance(raw, bool):
            raise _bad(f"「{label}」必須是開或關。")
        return raw
    if d.type in ("int", "float"):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _bad(f"「{label}」必須是數字。")
        if d.type == "int":
            if isinstance(raw, float) and not raw.is_integer():
                raise _bad(f"「{label}」必須是整數。")
            number: int | float = int(raw)
        else:
            number = float(raw)
        if (d.min is not None and number < d.min) or (d.max is not None and number > d.max):
            raise _bad(f"「{label}」必須介於 {d.min:g} 到 {d.max:g} 之間。")
        return number
    if d.type == "enum":
        if raw not in (d.choices or ()):
            raise _bad(f"「{label}」不是有效的選項。")
        return raw
    models = {m.id: m for m in _chat_models()}
    hidden = list(value("chat.hidden_models", session))
    if d.type == "model":
        if not isinstance(raw, str) or raw not in models:
            raise _bad("找不到所選的模型。")
        if not models[raw].available:
            raise _bad("所選模型的提供者尚未設定 API 金鑰。")
        if raw in hidden:
            raise _bad("此模型已被隱藏，請先取消隱藏。")
        return raw
    if d.type == "model_list":
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise _bad("隱藏的模型必須是模型清單。")
        unknown = [x for x in raw if x not in models]
        if unknown:
            raise _bad("清單中包含不存在的模型。")
        raw = list(dict.fromkeys(raw))
        default = value("chat.default_model", session)
        if default in raw:
            raise _bad("無法隱藏目前的預設模型，請先更換預設模型。")
        if not any(m.available and m.id not in raw for m in models.values()):
            raise _bad("至少要保留一個可用的模型。")
        return raw
    raise _bad("不支援的設定類型。")  # pragma: no cover


def set_override(session: Session, d: SettingDef, new_value: Any) -> None:
    row = session.get(AppSettingOverride, d.key)
    if row is None:
        row = AppSettingOverride(key=d.key, value=new_value)
    row.value = new_value
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()


def clear_override(session: Session, d: SettingDef) -> None:
    row = session.get(AppSettingOverride, d.key)
    if row is not None:
        session.delete(row)
        session.commit()
