"""Settings-page API: list, override and reset editable settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.services import app_settings as svc

router = APIRouter(prefix="/app-settings", tags=["app settings"])


def _serialize(d: svc.SettingDef, current: tuple[Any, str], session: Session) -> dict[str, Any]:
    default_value, _ = svc.base(d.key)
    return {
        "key": d.key,
        "label_zh": d.label_zh,
        "description_zh": d.description_zh,
        "type": d.type,
        "constraints": d.constraints,
        "unit_zh": d.unit_zh,
        "value": current[0],
        "source": current[1],
        "default_value": default_value,
        "restart_required": d.restart_required,
        "level": d.level,
    }


def _visible(d: svc.SettingDef) -> bool:
    return d.level == "user" or settings.enable_dev_settings


def _definition(key: str) -> svc.SettingDef:
    d = svc.get_def(key)
    if d is None:
        raise HTTPException(404, "找不到這個設定。")
    if not _visible(d):
        raise HTTPException(403, "開發者設定未啟用。")
    return d


def _listing(session: Session) -> dict[str, Any]:
    current = svc.effective_all(session)
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in svc.REGISTRY:
        if _visible(d):
            groups.setdefault(d.group, []).append(_serialize(d, current[d.key], session))
    return {
        "dev_enabled": settings.enable_dev_settings,
        "groups": [
            {"id": gid, "label_zh": svc.GROUP_LABELS[gid], "level": items[0]["level"], "settings": items}
            for gid, items in groups.items()
        ],
    }


@router.get("")
def list_app_settings(session: Session = Depends(get_session)):
    return _listing(session)


@router.put("/{key}")
def put_app_setting(key: str, body: Any = Body(None), session: Session = Depends(get_session)):
    # TODO(auth): restrict setting changes to authenticated operators.
    d = _definition(key)
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(422, "請求內容必須包含 value。")
    try:
        new_value = svc.validate(d, body["value"], session)
    except svc.SettingValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    svc.set_override(session, d, new_value)
    return _serialize(d, svc.effective(key, session), session)


@router.delete("/{key}")
def delete_app_setting(key: str, session: Session = Depends(get_session)):
    d = _definition(key)
    svc.clear_override(session, d)
    return _serialize(d, svc.effective(key, session), session)
