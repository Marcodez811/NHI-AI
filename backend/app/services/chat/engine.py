"""The chat engine: one Agents SDK turn per user message, streamed as events.

``ChatEngine`` is the seam the plan calls for
(docs/9_29_chat_core_and_attachments_plan.md): routes and storage depend only
on this interface so the harness can be swapped later. ``AgentsSdkChatEngine``
is the only implementation, backed by ``Runner.run_streamed`` with two tools
(``search_knowledge_base``, ``read_attachment``). Model construction mirrors
``app.services.agentic.sdk_runner`` exactly and reuses its LiteLLM run-config
helper rather than duplicating the key-wiring.

Event vocabulary yielded by ``run_turn`` (in order, for one turn):
``text_delta``* and ``tool_started``/``tool_finished``* interleaved as the
model streams, then one ``sources`` event, then one internal ``usage`` event
(the route persists it but never forwards it -- it is not part of the public
SSE vocabulary in the plan's table) -- or, on any failure, a single ``error``
event in place of everything after the point of failure.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from agents import Agent, RunConfig, Runner
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent, StreamEvent
from agents.tool import FunctionTool, function_tool
from agents.tool_context import ToolContext
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.models.chat import (
    ChatAttachment,
    ChatAttachmentKind,
    ChatAttachmentStatus,
    ChatMessage,
    ChatMessageRole,
)
from app.models.documents import DocumentCategory, DocumentStatus
from app.services.agentic.sdk_runner import build_litellm_run_config
from app.services.chat.attachments import ChatAttachmentStorage
from app.services.documents.repository import DocumentRepository

SEARCH_TOOL_NAME = "search_knowledge_base"
READ_ATTACHMENT_TOOL_NAME = "read_attachment"

BASE_INSTRUCTIONS = (
    "你是健保署的 AI 助理。回答必須根據 search_knowledge_base 工具找到的知識庫資料，"
    "或這個對話中的附件（用 read_attachment 讀取），禁止臆測或使用未經查證的內部知識。"
    "在答案中以【文件名稱】的格式標註引用來源；若知識庫與附件都沒有相關資料，請明確說明找不到答案。"
    "絕對不要透露任何內部識別碼、檔案路徑或系統代碼，使用者只能看到文件與附件的名稱。"
    "請一律使用繁體中文回答。"
)


@dataclass
class ChatEvent:
    type: str
    data: dict[str, Any]


class ChatEngine(Protocol):
    def run_turn(
        self,
        *,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        attachments: Sequence[ChatAttachment],
        model: str,
    ) -> AsyncIterator[ChatEvent]: ...


def _search_filters(category: str | None) -> dict[str, Any]:
    """Mirror the old ``build_file_search_filter``'s attribute grammar exactly.

    ``is_news_source`` is written as the string ``"false"``, not a JSON
    boolean -- that is how the ingestion pipeline stores it on vector-store
    file attributes, and this filter must match it byte for byte.
    """

    filters: list[dict[str, Any]] = [{"type": "eq", "key": "is_news_source", "value": "false"}]
    if category in {item.value for item in DocumentCategory}:
        filters.append({"type": "eq", "key": "qa_set", "value": category})
    return filters[0] if len(filters) == 1 else {"type": "and", "filters": filters}


def _tool_arguments(item: Any) -> dict[str, Any]:
    raw_item = item.raw_item
    raw_args = raw_item.get("arguments") if isinstance(raw_item, dict) else getattr(raw_item, "arguments", None)
    if not raw_args:
        return {}
    try:
        parsed = json.loads(raw_args)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_started_label(tool_name: str, item: Any) -> str:
    arguments = _tool_arguments(item)
    if tool_name == SEARCH_TOOL_NAME:
        return f"搜尋知識庫：{arguments.get('query', '')}"
    if tool_name == READ_ATTACHMENT_TOOL_NAME:
        return f"讀取附件：{arguments.get('name', '')}"
    return "執行工具"


def _dedupe_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for source in sources:
        if source["name"] in seen:
            continue
        seen.add(source["name"])
        deduped.append(source)
    return deduped


def _is_pdf(attachment: ChatAttachment) -> bool:
    return attachment.mime_type == "application/pdf"


def _attachment_note(attachment: ChatAttachment) -> str:
    if attachment.kind == ChatAttachmentKind.IMAGE.value:
        return "圖片"
    if _is_pdf(attachment):
        return "PDF，已直接附在傳送它的訊息中"
    if (attachment.text_chars or 0) > 0:
        return "文件，可用 read_attachment 讀取內容"
    return "文件，沒有可讀取的文字內容"


def _build_instructions(attachments: Sequence[ChatAttachment]) -> str:
    ready = [a for a in attachments if a.status == ChatAttachmentStatus.READY.value]
    if not ready:
        return BASE_INSTRUCTIONS
    lines = [BASE_INSTRUCTIONS, "", "這個對話目前的附件："]
    lines.extend(f"- {attachment.display_name}（{_attachment_note(attachment)}）" for attachment in ready)
    return "\n".join(lines)


async def _message_to_input_item(
    message: ChatMessage,
    attachments_by_id: Mapping[UUID, ChatAttachment],
    storage: ChatAttachmentStorage,
) -> dict[str, Any]:
    """Rebuild one stored turn as an Agents SDK input item.

    Only the final text is replayed for every turn (decision 3 in the plan --
    earlier tool calls are not replayed). Image attachments sent with a user
    turn are re-attached as ``input_image`` parts, and PDFs as ``input_file``
    parts, every time that turn is replayed, matching the plan's "re-sent with
    that message in later turns". The provider reads a PDF natively, scanned
    pages included; DOCX/TXT/MD are read through ``read_attachment`` instead.
    """

    role = "user" if message.role == ChatMessageRole.USER.value else "assistant"
    inline: list[ChatAttachment] = []
    if role == "user":
        for raw_id in message.attachment_ids:
            attachment = attachments_by_id.get(UUID(raw_id))
            if (
                attachment is not None
                and attachment.status == ChatAttachmentStatus.READY.value
                and (attachment.kind == ChatAttachmentKind.IMAGE.value or _is_pdf(attachment))
            ):
                inline.append(attachment)
    if not inline:
        return {"role": role, "content": message.content}

    parts: list[dict[str, Any]] = [{"type": "input_text", "text": message.content}]
    for attachment in inline:
        path = storage.resolve(message.session_id, attachment.storage_key)
        data = await asyncio.to_thread(path.read_bytes)
        data_url = f"data:{attachment.mime_type};base64,{base64.b64encode(data).decode('ascii')}"
        if _is_pdf(attachment):
            parts.append({"type": "input_file", "file_data": data_url, "filename": attachment.display_name})
        else:
            parts.append({"type": "input_image", "image_url": data_url})
    return {"role": role, "content": parts}


class AgentsSdkChatEngine:
    """``ChatEngine`` backed by the OpenAI Agents SDK.

    ``client``/``litellm_api_keys`` are injectable so tests never reach the
    network; production wiring (``app/main.py``) supplies the same
    ``RetrievalIndexRegistry``-backed vector store id and provider keys the
    rest of the app already uses.
    """

    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        vector_store_id_provider: Any,
        attachment_storage: ChatAttachmentStorage,
        openai_api_key: SecretStr | None = None,
        litellm_api_keys: Mapping[str, SecretStr | None] | None = None,
        client: Any | None = None,
        runner_factory: Any = Runner,
    ) -> None:
        self._document_repository = document_repository
        self._vector_store_id_provider = vector_store_id_provider
        self._attachment_storage = attachment_storage
        self._openai_api_key = openai_api_key
        self._litellm_api_keys = litellm_api_keys or {}
        self._client = client
        self._runner_factory = runner_factory

    async def _search_client(self) -> Any:
        if self._client is None:
            key = self._openai_api_key.get_secret_value() if self._openai_api_key else None
            self._client = AsyncOpenAI(api_key=key) if key else AsyncOpenAI()
        return self._client

    def _search_tool(
        self,
        collected_sources: list[dict[str, str]],
        tool_labels: dict[str, str],
    ) -> FunctionTool:
        async def search_knowledge_base(
            ctx: ToolContext,
            query: str,
            category: str | None = None,
            max_results: int = 8,
        ) -> str:
            """Search the NHI knowledge base for passages relevant to a query.

            Args:
                query: What to search for.
                category: Narrow the search to one scope: "legislative_qa",
                    "public_opinion", or "bei_can". Omit to search everything.
                max_results: Maximum number of results to return (1-20).
            """

            vector_store_id = self._vector_store_id_provider()
            if not vector_store_id:
                tool_labels[ctx.tool_call_id] = "知識庫目前無法使用"
                return "知識庫目前無法使用。"
            client = await self._search_client()
            try:
                page = await client.vector_stores.search(
                    vector_store_id,
                    query=query,
                    filters=_search_filters(category),
                    max_num_results=max(1, min(20, max_results)),
                )
            except Exception:
                tool_labels[ctx.tool_call_id] = "搜尋知識庫時發生錯誤"
                return "搜尋知識庫時發生錯誤，請稍後再試。"

            kept: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for result in page.data:
                attributes = result.attributes or {}
                raw_document_id = attributes.get("document_id")
                if not raw_document_id:
                    continue
                try:
                    document_id = UUID(str(raw_document_id))
                except ValueError:
                    continue
                document = await self._document_repository.get_document(document_id)
                if (
                    document is None
                    or document.status != DocumentStatus.READY.value
                    or not document.retrieval_enabled
                ):
                    # Post-filter: only a currently ready, retrieval-enabled
                    # document may be cited (docs/9_29_chat_core_and_attachments_plan.md).
                    continue
                snippet = " ".join(
                    part.text for part in result.content if getattr(part, "text", None)
                ).strip()
                # Several passages from one document are all useful to the
                # model; only exact repeats are dropped. The sources shown to
                # the user are deduplicated by name separately.
                passage = (document.display_name, snippet[:800])
                if not snippet or passage in seen:
                    continue
                seen.add(passage)
                kept.append(passage)

            tool_labels[ctx.tool_call_id] = f"找到 {len(kept)} 筆資料" if kept else "沒有找到相關資料"
            if not kept:
                return "沒有找到符合的資料。"
            collected_sources.extend({"name": name, "snippet": snippet} for name, snippet in kept)
            return "\n".join(f"{index + 1}. 【{name}】{snippet}" for index, (name, snippet) in enumerate(kept))

        return function_tool(
            search_knowledge_base,
            name_override=SEARCH_TOOL_NAME,
            description_override="搜尋健保署知識庫，尋找與查詢相關的資料段落（不含新聞來源）。",
        )

    def _read_attachment_tool(
        self,
        attachments_by_id: Mapping[UUID, ChatAttachment],
        tool_labels: dict[str, str],
    ) -> FunctionTool:
        by_name = {attachment.display_name: attachment for attachment in attachments_by_id.values()}

        async def read_attachment(
            ctx: ToolContext,
            name: str,
            start: int = 0,
            max_chars: int = 20_000,
        ) -> str:
            """Read a slice of one of this session's document attachments.

            Args:
                name: The attachment's display name, exactly as listed in the
                    instructions.
                start: Character offset to start from, for paging through a
                    long document.
                max_chars: Maximum number of characters to return.
            """

            attachment = by_name.get(name)
            if attachment is None or attachment.kind != ChatAttachmentKind.DOCUMENT.value:
                tool_labels[ctx.tool_call_id] = "找不到此附件"
                return "找不到這個附件。"
            if _is_pdf(attachment):
                tool_labels[ctx.tool_call_id] = f"讀取附件：{name}"
                return f"（{name} 是 PDF，已直接附在傳送它的訊息中，請直接閱讀該檔案。）"
            if attachment.status != ChatAttachmentStatus.READY.value:
                tool_labels[ctx.tool_call_id] = "附件無法讀取"
                return "這個附件目前無法讀取。"
            path = self._attachment_storage.resolve(attachment.session_id, attachment.storage_key)
            text, total = await asyncio.to_thread(
                self._attachment_storage.read_text,
                path,
                start=max(0, start),
                max_chars=max(1, min(20_000, max_chars)),
            )
            tool_labels[ctx.tool_call_id] = f"讀取附件：{name}"
            if not text:
                return f"（{name} 沒有可讀取的文字內容，共 {total} 字元。）"
            return f"（{name} 第 {start}-{start + len(text)} 字，共 {total} 字元）\n{text}"

        return function_tool(
            read_attachment,
            name_override=READ_ATTACHMENT_TOOL_NAME,
            description_override="讀取這個對話中某個文件附件的內容片段。",
        )

    async def run_turn(
        self,
        *,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        attachments: Sequence[ChatAttachment],
        model: str,
    ) -> AsyncIterator[ChatEvent]:
        attachments_by_id = {attachment.id: attachment for attachment in attachments}
        try:
            input_items = [
                await _message_to_input_item(message, attachments_by_id, self._attachment_storage)
                for message in (*history, user_message)
            ]
        except Exception:
            yield ChatEvent("error", {"code": "chat_attachment_unavailable", "message": "附件目前無法讀取，請稍後再試。"})
            return

        collected_sources: list[dict[str, str]] = []
        tool_labels: dict[str, str] = {}
        tool_call_names: dict[str, str] = {}
        agent = Agent(
            name="健保署 AI 助理",
            instructions=_build_instructions(attachments),
            model=model,
            tools=[
                self._search_tool(collected_sources, tool_labels),
                self._read_attachment_tool(attachments_by_id, tool_labels),
            ],
        )
        try:
            run_config = build_litellm_run_config(model, self._litellm_api_keys) or RunConfig()
        except Exception:
            yield ChatEvent("error", {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"})
            return

        try:
            streamed = self._runner_factory.run_streamed(agent, input_items, run_config=run_config)
            async for event in streamed.stream_events():
                for chat_event in _translate_stream_event(event, tool_labels, tool_call_names):
                    yield chat_event
        except Exception:
            yield ChatEvent("error", {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"})
            return

        yield ChatEvent("sources", {"sources": _dedupe_sources(collected_sources)})
        usage = streamed.context_wrapper.usage
        yield ChatEvent(
            "usage",
            {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            },
        )


def _translate_stream_event(
    event: StreamEvent,
    tool_labels: Mapping[str, str],
    tool_call_names: dict[str, str],
) -> Iterator[ChatEvent]:
    if isinstance(event, RawResponsesStreamEvent):
        data = event.data
        if getattr(data, "type", None) == "response.output_text.delta":
            delta = getattr(data, "delta", "") or ""
            if delta:
                yield ChatEvent("text_delta", {"text": delta})
        return
    if not isinstance(event, RunItemStreamEvent):
        return
    if event.name == "tool_called":
        item = event.item
        tool_name = item.tool_name
        call_id = item.call_id
        if tool_name not in (SEARCH_TOOL_NAME, READ_ATTACHMENT_TOOL_NAME) or not call_id:
            return
        tool_call_names[call_id] = tool_name
        yield ChatEvent("tool_started", {"tool": tool_name, "label": _tool_started_label(tool_name, item)})
        return
    if event.name == "tool_output":
        item = event.item
        call_id = item.call_id
        tool_name = tool_call_names.get(call_id or "")
        if not tool_name:
            return
        yield ChatEvent("tool_finished", {"tool": tool_name, "label": tool_labels.get(call_id or "", "完成")})


__all__ = ["ChatEngine", "ChatEvent", "AgentsSdkChatEngine", "SEARCH_TOOL_NAME", "READ_ATTACHMENT_TOOL_NAME"]
