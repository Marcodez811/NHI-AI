"""Normalize Responses API file annotations into public chat citations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any
from uuid import UUID

from app.models.chat import Citation


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    return {
        name: getattr(value, name)
        for name in ("type", "text", "file_id", "filename", "start_index", "end_index", "page")
        if hasattr(value, name)
    }


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _annotation_items(response: Any) -> Iterable[Any]:
    for output_item in _value(response, "output", ()) or ():
        for block in _value(output_item, "content", ()) or ():
            annotations = _value(block, "annotations", ()) or ()
            yield from annotations


def normalize_citations(
    response: Any,
    *,
    document_id_for_file: Callable[[str], UUID | None] | None = None,
) -> list[Citation]:
    """Extract, de-duplicate, and validate file citations from a response."""

    result: list[Citation] = []
    seen: set[tuple[str, str, int | None, int | None]] = set()
    for annotation in _annotation_items(response):
        nested = _value(annotation, "file_citation")
        data = _as_mapping(nested if nested is not None else annotation)
        file_id = str(data.get("file_id") or "") or None
        filename = str(data.get("filename") or "") or None
        if not file_id and not filename:
            continue
        start = data.get("start_index")
        end = data.get("end_index")
        try:
            start = int(start) if start is not None else None
        except (TypeError, ValueError):
            start = None
        try:
            end = int(end) if end is not None else None
        except (TypeError, ValueError):
            end = None
        key = (file_id or "", filename or "", start, end)
        if key in seen:
            continue
        seen.add(key)
        text = str(_value(annotation, "text", "") or "")
        page = data.get("page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        document_id = document_id_for_file(file_id) if file_id and document_id_for_file else None
        result.append(
            Citation(
                text=text or (f"【{filename}】" if filename else "Source"),
                filename=filename,
                file_id=file_id,
                document_id=document_id,
                start_index=start,
                end_index=end,
                page=page,
            )
        )
    return result

