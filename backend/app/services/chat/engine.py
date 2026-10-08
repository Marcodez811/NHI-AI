"""The chat engine: one Agents SDK turn per user message, streamed as events.

``ChatEngine`` is the seam the plan calls for
(docs/9_29_chat_core_and_attachments_plan.md): routes and storage depend only
on this interface so the harness can be swapped later. ``AgentsSdkChatEngine``
is backed by ``Runner.run_streamed`` with two tools
(``search_knowledge_base``, ``read_attachment``). Model construction mirrors
``app.services.agentic.sdk_runner`` exactly and reuses its LiteLLM run-config
helper rather than duplicating the key-wiring.

Event vocabulary yielded by ``run_turn`` (in order, for one turn):
``reasoning_delta``*, ``text_delta``* and ``tool_started``/``tool_finished``* interleaved as the
model streams, then one ``sources`` event, then one internal ``usage`` event
(the route persists it but never forwards it -- it is not part of the public
SSE vocabulary in the plan's table) -- or, on any failure, a single ``error``
event in place of everything after the point of failure.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from agents import Agent, RunConfig, Runner
from agents.model_settings import ModelSettings, Reasoning
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent, StreamEvent
from agents.tool import FunctionTool, function_tool
from agents.tool_context import ToolContext
from loguru import logger
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.models.chat import (
    ChatAttachmentKind,
    ChatAttachmentSource,
    ChatAttachmentStatus,
    ChatAttachmentView,
    ChatMessage,
    ChatMessageRole,
)
from app.models.documents import DocumentCategory, DocumentStatus
from app.services.agentic.sdk_runner import build_litellm_run_config
from app.services.chat.attachments import AttachmentError, ChatAttachmentStorage
from app.services.chat.skills import legislative_qa as qa_skill
from app.services.chat.skills.legislative_qa import SkillRuntime
from app.services.documents.repository import DocumentRepository

SEARCH_TOOL_NAME = "search_knowledge_base"
READ_ATTACHMENT_TOOL_NAME = "read_attachment"
INLINE_USER_MESSAGE_WINDOW = 3
# Replaces an expired PDF or image in replayed history. There is no tool to
# reopen it: the file comes back only when the user names it in a new message,
# so the model is told to ask for exactly that.
EXPIRED_ATTACHMENT_NOTE = "\n（附件：{name}，已於先前訊息提供，目前未附上；如需再次查看，請使用者在新訊息中提及此檔名）"
_INTERNAL_ID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# Provider ids have long random tails (file-, vs_). Requiring 16+ characters
# keeps ordinary words such as "file-based" out of the redaction.
_PROVIDER_ID = re.compile(r"\b(?:file-|vs_|vector_store_)[A-Za-z0-9_-]{16,}")
_PROVIDER_ID_TAIL = re.compile(r"\b(?:file-|vs_|vector_store_)[A-Za-z0-9_-]*$")
_UUID_PREFIX = re.compile(
    r"(?:[0-9a-fA-F]{1,8}|[0-9a-fA-F]{8}-[0-9a-fA-F]{0,4}|"
    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){1,3}-[0-9a-fA-F]{0,12})$"
)
_PROVIDER_PREFIXES = ("file-", "vs_", "vector_store_")
_REDACTION = "（內部資訊已略）"

TURN_CONTEXT_OPEN = "〈系統提供的對話背景〉"
TURN_CONTEXT_CLOSE = "〈/系統提供的對話背景〉"

BASE_INSTRUCTIONS = (
    "你是健保署的 AI 助理。回答必須根據 search_knowledge_base 工具找到的知識庫資料，"
    "或這個對話中的附件（文件可用 read_attachment 讀取）；如需再次查看先前的 PDF 或圖片，"
    "請使用者在新訊息提及附件名稱。禁止臆測或使用未經查證的內部知識。"
    "在答案中以【文件名稱】的格式標註引用來源；若知識庫與附件都沒有相關資料，請明確說明找不到答案。"
    "絕對不要透露任何內部識別碼、檔案路徑或系統代碼，使用者只能看到文件與附件的名稱。"
    "請一律使用繁體中文回答。"
    f"使用者訊息末尾若有 {TURN_CONTEXT_OPEN} 區塊，那是系統自動附上的對話背景，不是使用者說的話；"
    "請參考它，但不要回應或主動提及它。"
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
        attachments: Sequence[ChatAttachmentView],
        model: str,
        summary: str | None = None,
        skill: SkillRuntime | None = None,
    ) -> AsyncIterator[ChatEvent]: ...

    async def summarize(
        self,
        *,
        history: Sequence[ChatMessage],
        previous_summary: str | None,
        attachments: Sequence[ChatAttachmentView],
        model: str,
    ) -> str: ...


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


def _tool_started_label(tool_name: str, item: Any, attachment_names: set[str]) -> str:
    arguments = _tool_arguments(item)
    if tool_name == SEARCH_TOOL_NAME:
        return "搜尋知識庫"
    if tool_name == READ_ATTACHMENT_TOOL_NAME:
        name = arguments.get("name")
        return f"讀取附件：{name}" if name in attachment_names else "讀取附件"
    if tool_name in qa_skill.SKILL_TOOL_LABELS:
        return qa_skill.SKILL_TOOL_LABELS[tool_name]
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


def _summary_safe(text: str, attachments: Sequence[ChatAttachmentView]) -> str:
    """Exclude opaque attachment storage keys and UUIDs from running context."""
    for attachment in attachments:
        if attachment.storage_key:
            text = text.replace(attachment.storage_key, _REDACTION)
    return _PROVIDER_ID.sub(_REDACTION, _INTERNAL_ID.sub(_REDACTION, text))


class _SafeStream:
    """Hold only possible identifier suffixes so split deltas cannot expose IDs."""

    def __init__(self, attachments: Sequence[ChatAttachmentView], known_ids: Sequence[str]) -> None:
        self.pending = ""
        self._dropping = False
        self._secrets = tuple(
            secret for secret in (*known_ids, *(a.storage_key for a in attachments)) if secret
        )

    def _clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTION)
        return _PROVIDER_ID.sub(_REDACTION, _INTERNAL_ID.sub(_REDACTION, text))

    def feed(self, chunk: str, *, final: bool = False) -> str:
        if self._dropping:
            suffix = re.match(r"[A-Za-z0-9_-]*", chunk)
            chunk = chunk[suffix.end():] if suffix else chunk
            if not chunk:
                if final:
                    self._dropping = False
                return ""
            self._dropping = False
        self.pending += chunk
        if final:
            safe = self._clean(self.pending)
            self.pending = ""
            return safe
        hold = 0
        for secret in (*self._secrets, *_PROVIDER_PREFIXES):
            for size in range(1, min(len(secret), len(self.pending)) + 1):
                if self.pending.endswith(secret[:size]):
                    hold = max(hold, size)
        uuid_match = _UUID_PREFIX.search(self.pending)
        if uuid_match:
            hold = max(hold, len(uuid_match.group()))
        # A trailing prefix-plus-tail may still grow into a full provider id,
        # so hold it whatever its length; _clean decides once it is complete.
        provider_match = _PROVIDER_ID_TAIL.search(self.pending)
        if provider_match:
            hold = max(hold, len(provider_match.group()))
        # Retain complete identifiers until a delimiter arrives, then scrub
        # them before emission. Limit unbounded provider tokens as they grow.
        if hold > 256:
            prefix = self.pending[:-hold]
            self.pending = ""
            self._dropping = True
            return self._clean(prefix) + _REDACTION
        ready = self.pending[:-hold] if hold else self.pending
        self.pending = self.pending[-hold:] if hold else ""
        return self._clean(ready)


def _is_pdf(attachment: ChatAttachmentView) -> bool:
    return attachment.mime_type == "application/pdf"


def _has_readable_text(attachment: ChatAttachmentView) -> bool:
    # Knowledge-base text is extracted lazily, so its length is not known up front.
    return attachment.source == ChatAttachmentSource.KNOWLEDGE_BASE.value or (attachment.text_chars or 0) > 0


def _attachment_note(attachment: ChatAttachmentView) -> str:
    if attachment.kind == ChatAttachmentKind.IMAGE.value:
        return "圖片"
    if _is_pdf(attachment):
        return "PDF，最近訊息可直接查看，較早附件請在新訊息提及名稱"
    if _has_readable_text(attachment):
        return "文件，可用 read_attachment 讀取內容"
    return "文件，沒有可讀取的文字內容"


def _build_turn_context(
    attachments: Sequence[ChatAttachmentView], summary: str | None, skill_context: str | None = None
) -> str | None:
    """Return the per-turn context block, or ``None`` when there is nothing to say.

    It rides on the newest user message (for prompt caching), so it is fenced
    and labelled as system-supplied; unfenced, models read it as something the
    user wrote and start answering it ("目前這段對話沒有附件").
    """

    lines = []
    if summary:
        lines.extend(("先前對話摘要：", summary))
    if attachments:
        lines.append("這個對話目前的附件：")
        lines.extend(
            f"- {attachment.display_name}（{'知識庫文件；' if attachment.source == ChatAttachmentSource.KNOWLEDGE_BASE.value else ''}{_attachment_note(attachment) if attachment.status == ChatAttachmentStatus.READY.value else '無法讀取'}；"
            f"{'可使用' if attachment.status == ChatAttachmentStatus.READY.value else '處理失敗'}）"
            for attachment in attachments
        )
    if skill_context:
        lines.append(skill_context)
    if not lines:
        return None
    return "\n".join((TURN_CONTEXT_OPEN, *lines, TURN_CONTEXT_CLOSE))


async def _message_to_input_item(
    message: ChatMessage,
    attachments_by_id: Mapping[UUID, ChatAttachmentView],
    storage: ChatAttachmentStorage,
    *,
    inline: bool = True,
) -> dict[str, Any]:
    """Rebuild one stored turn as an Agents SDK input item.

    Only the final text is replayed for every turn (decision 3 in the plan --
    earlier tool calls are not replayed). Image attachments sent with a user
    turn are attached only while eligible. Older ones use a stable text
    placeholder. The provider reads a PDF natively, scanned pages included;
    DOCX/TXT/MD are read through ``read_attachment`` instead.
    """

    role = "user" if message.role == ChatMessageRole.USER.value else "assistant"
    inline_attachments: list[ChatAttachmentView] = []
    expired: list[ChatAttachmentView] = []
    if role == "user":
        for raw_id in message.attachment_ids:
            attachment = attachments_by_id.get(UUID(raw_id))
            if (
                attachment is not None
                and attachment.status == ChatAttachmentStatus.READY.value
                and (attachment.kind == ChatAttachmentKind.IMAGE.value or _is_pdf(attachment))
            ):
                (inline_attachments if inline else expired).append(attachment)
    if not inline_attachments and not expired:
        return {"role": role, "content": message.content}

    text = message.content + "".join(
        EXPIRED_ATTACHMENT_NOTE.format(name=attachment.display_name)
        for attachment in expired
    )
    if not inline_attachments:
        return {"role": role, "content": text}
    parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    for attachment in inline_attachments:
        path = storage.resolve(attachment.storage_key)
        data = await asyncio.to_thread(path.read_bytes)
        data_url = f"data:{attachment.mime_type};base64,{base64.b64encode(data).decode('ascii')}"
        if _is_pdf(attachment):
            parts.append({"type": "input_file", "file_data": data_url, "filename": attachment.display_name})
        else:
            parts.append({"type": "input_image", "image_url": data_url})
    return {"role": role, "content": parts}


_OPENAI_REASONING_MODELS = ("gpt-5", "gpt-6", "o1", "o3", "o4")
# Keys that can appear inside provider error text; never let them reach the log.
_SECRET_PATTERN = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,})")


def _model_settings(model: str) -> ModelSettings:
    """Ask every provider for reasoning so the UI can show 「思考過程」.

    OpenAI models stream reasoning *summaries*. LiteLLM models (Gemini, Claude)
    take a scalar effort instead: the SDK forwards it as ``reasoning_effort``, and
    LiteLLM turns it into the provider's thinking setting with thoughts included
    (``includeThoughts`` for Gemini). The SDK then turns the returned
    ``reasoning_content`` into the same reasoning events.

    "medium", not "low": verified live on 2026-09-29, Gemini 3.8 Flash at
    ``thinkingLevel: low`` returns no thoughts at all, while LiteLLM 1.83 maps
    "medium" to ``thinkingLevel: high`` and the thoughts come back.
    """

    if model.startswith(_OPENAI_REASONING_MODELS):
        return ModelSettings(reasoning=Reasoning(summary="auto"))
    if model.startswith("litellm/"):
        return ModelSettings(reasoning=Reasoning(effort="medium"))
    return ModelSettings()


def _log_provider_error(model: str, exc: Exception) -> None:
    """Record why a turn failed; the user only ever sees the generic message."""

    detail = _SECRET_PATTERN.sub("[redacted]", str(exc))[:500]
    logger.warning("chat turn failed on {}: {}: {}", model, type(exc).__name__, detail)


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
                snippet = _summary_safe(" ".join(
                    part.text for part in result.content if getattr(part, "text", None)
                ).strip(), ())
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

    async def _search_documents(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """Knowledge-base hits as ``{document_id, name, snippet}``, ready and retrieval-enabled only."""

        vector_store_id = self._vector_store_id_provider()
        if not vector_store_id:
            return []
        client = await self._search_client()
        try:
            page = await client.vector_stores.search(
                vector_store_id,
                query=query,
                filters=_search_filters(None),
                max_num_results=max(1, min(20, max_results)),
            )
        except Exception:
            return []
        hits: list[dict[str, Any]] = []
        for result in page.data:
            try:
                document_id = UUID(str((result.attributes or {}).get("document_id")))
            except ValueError:
                continue
            document = await self._document_repository.get_document(document_id)
            if document is None or document.status != DocumentStatus.READY.value or not document.retrieval_enabled:
                continue
            snippet = " ".join(part.text for part in result.content if getattr(part, "text", None)).strip()
            hits.append({"document_id": document_id, "name": document.display_name, "snippet": snippet})
        return hits

    async def _read_document_text(
        self, attachment: ChatAttachmentView, *, start: int, max_chars: int
    ) -> tuple[str, int]:
        """Read a slice of a DOCX/TXT/MD attachment's text.

        Knowledge-base documents are extracted on first use and cached beside
        the stored document.
        """

        storage = self._attachment_storage
        path = storage.resolve(attachment.storage_key)
        text_path = None
        if attachment.source == ChatAttachmentSource.KNOWLEDGE_BASE.value:
            text_path = await storage.ensure_kb_text(path)
        return await asyncio.to_thread(
            storage.read_text, path, start=start, max_chars=max_chars, text_path=text_path,
        )

    def _read_attachment_tool(
        self,
        attachments_by_id: Mapping[UUID, ChatAttachmentView],
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
                    current turn context.
                start: Character offset to start from, for paging through a
                    long document.
                max_chars: Maximum number of characters to return.
            """

            attachment = by_name.get(name)
            if attachment is None or attachment.kind != ChatAttachmentKind.DOCUMENT.value:
                tool_labels[ctx.tool_call_id] = "找不到此附件"
                return "找不到這個附件。"
            if attachment.status != ChatAttachmentStatus.READY.value:
                tool_labels[ctx.tool_call_id] = "附件無法讀取"
                return "這個附件目前無法讀取。"
            if _is_pdf(attachment):
                tool_labels[ctx.tool_call_id] = f"讀取附件：{name}"
                return f"（{name} 是 PDF；若目前訊息中沒有該檔案，請使用者在新訊息提及附件名稱以再次查看。）"
            try:
                text, total = await self._read_document_text(
                    attachment, start=max(0, start), max_chars=max(1, min(20_000, max_chars)),
                )
            except AttachmentError:
                tool_labels[ctx.tool_call_id] = "附件無法讀取"
                return "這個附件目前無法讀取。"
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
        attachments: Sequence[ChatAttachmentView],
        model: str,
        summary: str | None = None,
        skill: SkillRuntime | None = None,
    ) -> AsyncIterator[ChatEvent]:
        attachments_by_id = {attachment.id: attachment for attachment in attachments}
        try:
            session_attachments = tuple(attachments_by_id.values())
            eligible = {
                message.id
                for message in [
                    item
                    for item in (*history, user_message)
                    if item.role == ChatMessageRole.USER.value
                ][-INLINE_USER_MESSAGE_WINDOW:]
            }
            input_items = [
                await _message_to_input_item(
                    message, attachments_by_id, self._attachment_storage,
                    inline=message.id in eligible,
                )
                for message in (*history, user_message)
            ]
            # When the newest request names an older attachment, provide the
            # original file on this turn only. The SDK's structured tool output
            # can retain file_data, but provider-specific LiteLLM tool-message
            # transformations are not guaranteed to preserve multimodal parts.
            already_attached = set(user_message.attachment_ids)
            for attachment in session_attachments:
                if (
                    attachment.status == ChatAttachmentStatus.READY.value
                    and (attachment.kind == ChatAttachmentKind.IMAGE.value or _is_pdf(attachment))
                    and attachment.display_name in user_message.content
                    and str(attachment.id) not in already_attached
                ):
                    file_item = await _message_to_input_item(
                        ChatMessage(
                            session_id=user_message.session_id,
                            role=ChatMessageRole.USER.value,
                            content="",
                            attachment_ids=[str(attachment.id)],
                        ),
                        attachments_by_id,
                        self._attachment_storage,
                    )
                    parts = file_item["content"]
                    if isinstance(parts, list):
                        current = input_items[-1]["content"]
                        if isinstance(current, str):
                            current = [{"type": "input_text", "text": current}]
                        input_items[-1]["content"] = [*current, *parts[1:]]
            skill_context = None
            if skill is not None:
                skill_context = qa_skill.render_context(await skill.repository.get_qa_workspace(skill.session_id))
            context_text = _build_turn_context(
                session_attachments,
                _summary_safe(summary, session_attachments) if summary else None,
                skill_context,
            )
            if context_text is not None:
                turn_context = {"type": "input_text", "text": context_text}
                current_content = input_items[-1]["content"]
                input_items[-1]["content"] = (
                    [*current_content, turn_context]
                    if isinstance(current_content, list)
                    else [{"type": "input_text", "text": current_content}, turn_context]
                )
        except Exception:
            yield ChatEvent("error", {"code": "chat_attachment_unavailable", "message": "附件目前無法讀取，請稍後再試。"})
            return

        collected_sources: list[dict[str, str]] = []
        tool_labels: dict[str, str] = {}
        tool_call_names: dict[str, str] = {}
        attachment_names = {attachment.display_name for attachment in session_attachments}
        known_ids = [
            str(item.id) for item in (*history, user_message, *session_attachments)
        ] + [str(user_message.session_id)]
        text_stream = _SafeStream(session_attachments, known_ids)
        reasoning_stream = _SafeStream(session_attachments, known_ids)
        tools = [
            self._search_tool(collected_sources, tool_labels),
            self._read_attachment_tool(attachments_by_id, tool_labels),
        ]
        instructions = BASE_INSTRUCTIONS
        pending_cards: list[ChatEvent] = []
        if skill is not None:
            instructions = f"{BASE_INSTRUCTIONS}\n\n{qa_skill.load_instructions()}"
            tools.extend(qa_skill.build_tools(
                skill,
                session_attachments,
                self._search_documents,
                lambda kind, data: pending_cards.append(ChatEvent("skill_card", {"kind": kind, "data": data})),
                tool_labels,
            ))
        agent = Agent(
            name="健保署 AI 助理",
            instructions=instructions,
            model=model,
            model_settings=_model_settings(model),
            tools=tools,
        )
        try:
            run_config = build_litellm_run_config(model, self._litellm_api_keys) or RunConfig()
        except Exception as exc:
            _log_provider_error(model, exc)
            yield ChatEvent("error", {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"})
            return

        try:
            streamed = self._runner_factory.run_streamed(agent, input_items, run_config=run_config)
            async for event in streamed.stream_events():
                for chat_event in _translate_stream_event(
                    event, tool_labels, tool_call_names, attachment_names,
                ):
                    if chat_event.type in ("text_delta", "reasoning_delta"):
                        stream = text_stream if chat_event.type == "text_delta" else reasoning_stream
                        safe = stream.feed(chat_event.data["text"])
                        if safe:
                            yield ChatEvent(chat_event.type, {"text": safe})
                    else:
                        yield chat_event
                while pending_cards:
                    yield pending_cards.pop(0)
        except Exception as exc:
            _log_provider_error(model, exc)
            for event_type, stream in (("reasoning_delta", reasoning_stream), ("text_delta", text_stream)):
                if safe := stream.feed("", final=True):
                    yield ChatEvent(event_type, {"text": safe})
            yield ChatEvent("error", {"code": "chat_unavailable", "message": "對話服務暫時無法使用，請稍後再試。"})
            return

        for event_type, stream in (("reasoning_delta", reasoning_stream), ("text_delta", text_stream)):
            if safe := stream.feed("", final=True):
                yield ChatEvent(event_type, {"text": safe})
        while pending_cards:
            yield pending_cards.pop(0)
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

    async def summarize(
        self,
        *,
        history: Sequence[ChatMessage],
        previous_summary: str | None,
        attachments: Sequence[ChatAttachmentView],
        model: str,
    ) -> str:
        """Summarize only newly expired messages, without model-facing identifiers."""
        by_id = {attachment.id: attachment for attachment in attachments}
        lines = [
            "請用繁體中文整理以下較早對話，保留使用者目標與限制、已做決定、"
            "事實與數字及其來源文件名稱、附件名稱與內容、尚未回答的問題。"
            "不可包含內部識別碼、儲存位置或系統金鑰。",
        ]
        if previous_summary:
            lines.extend(("\n先前摘要：", previous_summary))
        lines.append("\n新增的較早訊息：")
        for message in history:
            role = "使用者" if message.role == ChatMessageRole.USER.value else "助理"
            lines.append(f"{role}：{message.content}")
            for raw_id in message.attachment_ids:
                attachment = by_id.get(UUID(raw_id))
                if attachment is None:
                    continue
                lines.append(f"附件名稱：{attachment.display_name}（{_attachment_note(attachment)}）")
                if (
                    attachment.kind == ChatAttachmentKind.DOCUMENT.value
                    and not _is_pdf(attachment)
                    and _has_readable_text(attachment)
                ):
                    excerpt, _ = await self._read_document_text(attachment, start=0, max_chars=2_000)
                    lines.append(f"附件內容摘錄：{excerpt}")
            for source in message.sources or []:
                lines.append(f"來源文件：{source['name']}；相關資料：{source['snippet']}")
        agent = Agent(name="對話摘要", instructions="只輸出繁體中文摘要；不得輸出內部識別碼。", model=model, tools=[])
        run_config = build_litellm_run_config(model, self._litellm_api_keys) or RunConfig()
        result = await self._runner_factory.run(
            agent, _summary_safe("\n".join(lines), attachments), run_config=run_config,
        )
        summary = _summary_safe(str(result.final_output or ""), attachments).strip()
        if not summary:
            raise ValueError("摘要服務未產生內容")
        return summary


def _translate_stream_event(
    event: StreamEvent,
    tool_labels: Mapping[str, str],
    tool_call_names: dict[str, str],
    attachment_names: set[str] | None = None,
) -> Iterator[ChatEvent]:
    if isinstance(event, RawResponsesStreamEvent):
        data = event.data
        if getattr(data, "type", None) == "response.reasoning_summary_text.delta":
            delta = getattr(data, "delta", "") or ""
            if delta:
                yield ChatEvent("reasoning_delta", {"text": delta})
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
        if tool_name not in (SEARCH_TOOL_NAME, READ_ATTACHMENT_TOOL_NAME, *qa_skill.SKILL_TOOL_NAMES) or not call_id:
            return
        tool_call_names[call_id] = tool_name
        yield ChatEvent(
            "tool_started",
            {"tool": tool_name, "label": _tool_started_label(tool_name, item, attachment_names or set())},
        )
        return
    if event.name == "tool_output":
        item = event.item
        call_id = item.call_id
        tool_name = tool_call_names.get(call_id or "")
        if not tool_name:
            return
        yield ChatEvent("tool_finished", {"tool": tool_name, "label": tool_labels.get(call_id or "", "完成")})


__all__ = ["ChatEngine", "ChatEvent", "AgentsSdkChatEngine", "SEARCH_TOOL_NAME", "READ_ATTACHMENT_TOOL_NAME"]
