"""Injectable OpenAI Responses API adapter for grounded Q&A."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Callable, Iterable
from typing import Any, Protocol
from uuid import UUID

from app.config import settings
from app.models.chat import ChatRequest, ChatResponse, Citation, QaMode

from .citations import normalize_citations
from .retrieval import build_file_search_tool


INSUFFICIENT_EVIDENCE = "無法回答，因為無相關資料"
logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTIONS: dict[QaMode, str] = {
    QaMode.LEGISLATIVE_QA: (
        "你是健保署立院問答助理。只能依據 file_search 找到的資料回答，禁止使用模型內部知識、推測或上網搜尋。"
        "若資料不足，回答『無法回答，因為無相關資料』。數字、年份、百分比及表格值必須保持來源原樣。"
        "回答使用繁體中文，引用檔名與段落，且不得暴露技術識別碼。"
    ),
    QaMode.PUBLIC_OPINION: (
        "你是健保署輿情與政策資料助理。只能依據 file_search 找到的資料回答，禁止推測或使用模型內部知識。"
        "若資料不足，回答『無法回答，因為無相關資料』。保留來源中的數字、期間與表格格式，使用繁體中文並清楚引用來源。"
    ),
    QaMode.BEI_CAN: (
        "你是健保署備參資料查詢助理。只能依據 file_search 找到的備參 Markdown 資料回答，禁止推測或使用模型內部知識。"
        "若資料不足，回答『無法回答，因為無相關資料』。使用繁體中文、清楚引用檔名與段落，不得暴露技術識別碼。"
    ),
}


class ResponsesClient(Protocol):
    responses: Any


class AsyncResponsesClient(Protocol):
    responses: Any


class ChatServiceError(RuntimeError):
    """A safe, user-facing chat service failure."""


async def _default_async_client() -> Any:
    """Create an async client lazily using settings rather than os.getenv."""

    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        raise ChatServiceError("Chat provider is not configured.")
    try:
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=api_key)
    except Exception as exc:  # pragma: no cover - depends on local provider setup
        raise ChatServiceError("Chat provider is not configured.") from exc


def _default_vector_store_id() -> str | None:
    # Legacy path: reads from settings (which may itself have read from env).
    # Production wiring in main.py overrides this via the registry.
    return getattr(settings, "openai_vector_store_id", None) or None


def _default_model() -> str:
    return getattr(settings, "openai_chat_model", "gpt-5.6-luna")


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


def _event_type(event: Any) -> str:
    return str(event.get("type") if isinstance(event, dict) else getattr(event, "type", ""))


def _event_delta(event: Any) -> str:
    return str(event.get("delta", "") if isinstance(event, dict) else getattr(event, "delta", ""))


def _filter_citations(
    citations: list[Citation],
    allowlist: list[UUID] | None,
) -> list[Citation]:
    """Discard citations whose document_id is not in the allowlist.

    When ``allowlist`` is ``None`` no filtering is applied (legacy/test paths
    where the allowlist is not yet resolved).  An empty allowlist keeps no
    citations — every result is discarded.
    """

    if allowlist is None:
        return citations
    allowed = set(allowlist)
    return [c for c in citations if c.document_id is not None and c.document_id in allowed]


class ResponseService:
    """Build and execute source-grounded requests.

    ``client``, ``vector_store_id_provider`` and ``model_provider`` are
    injectable to make tests deterministic and let the application wire its
    settings without importing OpenAI at module import time.

    All provider calls are async.  Callers must ``await`` both ``answer()``
    and ``answer_stream()``.
    """

    def __init__(
        self,
        client: AsyncResponsesClient | ResponsesClient | None = None,
        *,
        vector_store_id: str | None = None,
        vector_store_id_provider: Callable[[], str | None] | None = None,
        model: str | None = None,
        model_provider: Callable[[], str] | None = None,
        document_id_for_file: Callable[[str], Any] | None = None,
    ) -> None:
        self._client = client
        self._vector_store_id = vector_store_id
        self._vector_store_id_provider = vector_store_id_provider or _default_vector_store_id
        self._model = model
        self._model_provider = model_provider or _default_model
        self._document_id_for_file = document_id_for_file

    @property
    async def _async_client(self) -> Any:
        if self._client is None:
            self._client = await _default_async_client()
        return self._client

    def resolve_vector_store_id(self) -> str:
        value = self._vector_store_id or self._vector_store_id_provider()
        if not value:
            raise ChatServiceError("Chat retrieval index is not configured.")
        return value

    @staticmethod
    def _validated_document_allowlist(
        document_id_allowlist: Iterable[UUID] | None,
    ) -> list[UUID]:
        if document_id_allowlist is None:
            raise ChatServiceError("Chat document scope is not configured.")
        allowed_ids = list(document_id_allowlist)
        if not allowed_ids:
            raise ChatServiceError("Chat document scope is not configured.")
        return allowed_ids

    def build_request(
        self,
        request: ChatRequest,
        document_id_allowlist: Iterable[UUID] | None = None,
    ) -> dict[str, Any]:
        allowed_ids = self._validated_document_allowlist(document_id_allowlist)
        vector_store_id = self.resolve_vector_store_id()
        return {
            "model": self._model or self._model_provider(),
            "instructions": SYSTEM_INSTRUCTIONS[request.mode],
            "input": request.question,
            "tools": [
                build_file_search_tool(
                    vector_store_id,
                    request.mode,
                    allowed_ids,
                    request.max_num_results,
                )
            ],
            **({
                "include": ["file_search_call.results"]
            } if request.include_search_results else {}),
        }

    async def answer(
        self,
        request: ChatRequest,
        *,
        document_id_allowlist: Iterable[UUID] | None = None,
    ) -> ChatResponse:
        try:
            allowed_ids = self._validated_document_allowlist(document_id_allowlist)
            client = await self._async_client
            response = await client.responses.create(
                **self.build_request(request, allowed_ids)
            )
        except ChatServiceError:
            raise
        except Exception as exc:
            raise ChatServiceError("Chat provider is temporarily unavailable.") from exc
        raw_citations = normalize_citations(
            response, document_id_for_file=self._document_id_for_file
        )
        citations = _filter_citations(raw_citations, allowed_ids)
        answer = str(
            getattr(response, "output_text", None)
            or (response.get("output_text") if isinstance(response, dict) else "")
            or ""
        )
        if not citations:
            answer = INSUFFICIENT_EVIDENCE
        return ChatResponse(answer=answer, mode=request.mode, citations=citations)

    async def answer_stream(
        self,
        request: ChatRequest,
        *,
        document_id_allowlist: Iterable[UUID] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield grounded ``text_delta`` events followed by one ``done``.

        Responses can emit answer text before the final file annotations are
        available.  Buffering those deltas until the response closes prevents
        an ungrounded answer from reaching the client when no citation is
        ultimately returned.
        """

        try:
            yield _sse("status", {"phase": "preparing"})
            allowed_ids = self._validated_document_allowlist(document_id_allowlist)
            client = await self._async_client
            yield _sse("status", {"phase": "searching"})
            stream = client.responses.stream(
                **self.build_request(request, allowed_ids)
            )
            text_chunks: list[str] = []
            drafting_started = False
            async with stream as active:
                async for event in active:
                    if _event_type(event) == "response.output_text.delta":
                        if not drafting_started:
                            drafting_started = True
                            yield _sse("status", {"phase": "drafting"})
                        text_chunks.append(_event_delta(event))
                final = await active.get_final_response()
            yield _sse("status", {"phase": "validating"})
            raw_citations = normalize_citations(
                final, document_id_for_file=self._document_id_for_file
            )
            citations = _filter_citations(raw_citations, allowed_ids)
            if citations:
                for chunk in text_chunks:
                    yield _sse("text_delta", {"text": chunk})
            else:
                yield _sse("text_delta", {"text": INSUFFICIENT_EVIDENCE})
            yield _sse(
                "done",
                {
                    "mode": request.mode.value,
                    "citations": [citation.model_dump(mode="json") for citation in citations],
                    "grounded": bool(citations),
                },
            )
        except ChatServiceError as exc:
            logger.warning("Chat service unavailable: %s", exc)
            yield _sse(
                "error",
                {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"},
            )
        except Exception:
            logger.exception("Unexpected streaming chat failure")
            yield _sse(
                "error",
                {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"},
            )
