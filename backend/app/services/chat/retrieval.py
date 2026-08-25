"""Retrieval-index request construction for grounded chat."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from app.models.chat import QaMode


def _eq(key: str, value: str) -> dict[str, str]:
    return {"type": "eq", "key": key, "value": value}


def build_file_search_filter(
    mode: QaMode,
    document_ids: Iterable[UUID] = (),
) -> dict[str, Any]:
    """Build an OpenAI metadata filter for one explicit chat scope.

    Every scope is constrained to the indexed application corpus.  The
    optional document restriction is combined with the scope using ``and``;
    OpenAI's filter grammar uses an ``or`` group for multiple IDs.
    """

    if mode is QaMode.BEI_CAN:
        scope = _eq("content_type", "markdown_wiki")
    else:
        scope = _eq("qa_set", mode.value)

    # This is an invariant, not a user-selectable mode. It prevents stale
    # source attachments from being reachable by any new chat request.
    filters: list[dict[str, Any]] = [_eq("is_news_source", "false"), scope]
    ids = [str(item) for item in document_ids]
    if ids:
        id_filters = [_eq("document_id", item) for item in ids]
        filters.append(id_filters[0] if len(id_filters) == 1 else {"type": "or", "filters": id_filters})
    return filters[0] if len(filters) == 1 else {"type": "and", "filters": filters}


def build_file_search_tool(
    vector_store_id: str,
    mode: QaMode,
    document_ids: Iterable[UUID] = (),
    max_num_results: int = 12,
) -> dict[str, Any]:
    return {
        "type": "file_search",
        "vector_store_ids": [vector_store_id],
        "max_num_results": max_num_results,
        "filters": build_file_search_filter(mode, document_ids),
    }

