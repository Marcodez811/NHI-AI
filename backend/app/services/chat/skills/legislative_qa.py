"""立院QA skill, Phase 1: workspace rules, turn context, cards and the model's tools.

Everything that changes the workspace lives here as plain functions, so the
model's tools (``build_tools``) and the card-action routes share one set of
rules (docs/10_6_legislative_qa_skill_plan.md sections 2-3). The workspace
``stage`` is *derived* from its content by ``recompute_stage`` on every save,
so editing an earlier step moves the flow back and nothing can drift.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from agents.tool import FunctionTool, function_tool
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict

from app.models.chat import (
    ChatAttachmentKind,
    ChatAttachmentStatus,
    ChatAttachmentView,
    ChatQaWorkspace,
)

SKILL_ID = "legislative_qa"
INSTRUCTIONS_PATH = Path(__file__).resolve().parents[4] / "chat_skills" / "legislative_qa" / "SKILL.md"

STAGES = ("questions", "documents", "evidence", "outline", "ready")
STAGE_LABELS = {
    "questions": "需求解析（確認題目）",
    "documents": "資料輸入（挑選資料）",
    "evidence": "資訊整理（數據、目的、現況、爭議、後續）",
    "outline": "答題架構（確認大綱）",
    "ready": "大綱已全部確認（產製功能尚未開放）",
}
EVIDENCE_FIELDS = ("figures", "aim", "status", "dispute", "next_steps")
EVIDENCE_LABELS = {
    "figures": "數據",
    "aim": "政策目的",
    "status": "辦理現況",
    "dispute": "爭議",
    "next_steps": "後續工作",
}
DEFAULT_DETAIL_TITLES = ("背景說明", "目前辦理情形", "未來工作重點")
DISPUTE_TITLE = "爭議點"
MAX_QUESTIONS = 50
MAX_CANDIDATES_PER_QUESTION = 5

TOOL_SET_QUESTIONS = "set_questions"
TOOL_SUGGEST_DOCUMENTS = "suggest_documents"
TOOL_RECORD_EVIDENCE = "record_evidence"
TOOL_PROPOSE_OUTLINE = "propose_outline"
SKILL_TOOL_LABELS = {
    TOOL_SET_QUESTIONS: "整理題目清單",
    TOOL_SUGGEST_DOCUMENTS: "尋找可能有用的資料",
    TOOL_RECORD_EVIDENCE: "記錄整理結果",
    TOOL_PROPOSE_OUTLINE: "擬定答題大綱",
}
SKILL_TOOL_NAMES = tuple(SKILL_TOOL_LABELS)


class QaError(ValueError):
    """A rule violation; ``code`` is stable for the API, ``message`` is shown to the user/model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class SkillRuntime:
    """What the engine needs to run a skill turn: the skill id and where its state lives."""

    skill: str
    session_id: UUID
    repository: Any  # ChatRepository (kept loose to avoid an import cycle)


SearchFn = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
EmitFn = Callable[[str, dict[str, Any]], None]


# ── Inputs (shared by the model's tools and the card-action routes) ─────────


class QuestionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: int | None = None
    text: str
    note: str = ""


class ToolQuestion(BaseModel):
    text: str
    note: str


class EvidenceItemInput(BaseModel):
    field: Literal["figures", "aim", "status", "dispute", "next_steps"]
    text: str
    source_name: str
    quote: str


class DetailSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    points: list[str]


class OutlineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short: list[str]
    detail: list[DetailSection] = []
    dispute_requested: bool = False


# ── Workspace rules ─────────────────────────────────────────────────────────


def new_workspace(session_id: UUID) -> ChatQaWorkspace:
    return ChatQaWorkspace(session_id=session_id, documents={"confirmed": False, "used": []})


def clone(workspace: ChatQaWorkspace) -> ChatQaWorkspace:
    """A detached copy; JSON columns are replaced, never mutated in place."""

    return ChatQaWorkspace(
        session_id=workspace.session_id,
        stage=workspace.stage,
        questions=copy.deepcopy(workspace.questions),
        questions_confirmed=workspace.questions_confirmed,
        documents=copy.deepcopy(workspace.documents) or {"confirmed": False, "used": []},
        evidence=copy.deepcopy(workspace.evidence),
        outline=copy.deepcopy(workspace.outline),
        versions=copy.deepcopy(workspace.versions),
        base_version_id=workspace.base_version_id,
        updated_at=workspace.updated_at,
    )


def _question(workspace: ChatQaWorkspace, no: int) -> dict[str, Any]:
    for item in workspace.questions:
        if item["no"] == no:
            return item
    raise QaError("qa_question_not_found", f"沒有第 {no} 題。")


def recompute_stage(workspace: ChatQaWorkspace) -> str:
    nos = [str(item["no"]) for item in workspace.questions]
    if not workspace.questions or not workspace.questions_confirmed:
        stage = "questions"
    elif not (workspace.documents or {}).get("confirmed"):
        stage = "documents"
    elif not any(no in workspace.outline for no in nos):
        stage = "evidence"
    elif all(workspace.outline.get(no, {}).get("confirmed") for no in nos):
        stage = "ready"
    else:
        stage = "outline"
    workspace.stage = stage
    return stage


def apply_questions(
    workspace: ChatQaWorkspace, edits: Sequence[QuestionEdit], *, confirmed: bool
) -> ChatQaWorkspace:
    if not edits:
        raise QaError("qa_questions_empty", "題目清單不可為空。")
    if len(edits) > MAX_QUESTIONS:
        raise QaError("qa_too_many_questions", f"題目最多 {MAX_QUESTIONS} 題。")
    if any(not edit.text.strip() for edit in edits):
        raise QaError("qa_question_blank", "題目內容不可空白。")
    given = [edit.no for edit in edits if edit.no is not None]
    if len(given) != len(set(given)) or any(no < 1 for no in given):
        raise QaError("qa_question_numbers", "題號必須是不重複的正整數。")
    next_no = max(given, default=0) + 1
    items: list[dict[str, Any]] = []
    for index, edit in enumerate(edits):
        if not given:
            no = index + 1
        elif edit.no is not None:
            no = edit.no
        else:
            no, next_no = next_no, next_no + 1
        items.append({"no": no, "text": edit.text.strip(), "note": edit.note.strip()})
    old_text = {item["no"]: item["text"] for item in workspace.questions}
    new_text = {item["no"]: item["text"] for item in items}
    # Evidence and outlines belong to a question's wording: drop them when it is removed or reworded.
    for bucket in (workspace.evidence, workspace.outline):
        for key in list(bucket):
            no = int(key)
            if no not in new_text or old_text.get(no) != new_text[no]:
                del bucket[key]
    workspace.questions = items
    workspace.questions_confirmed = confirmed
    return workspace


def usable_attachments(attachments: Sequence[ChatAttachmentView]) -> list[ChatAttachmentView]:
    return [
        item
        for item in attachments
        if item.kind == ChatAttachmentKind.DOCUMENT.value and item.status == ChatAttachmentStatus.READY.value
    ]


def confirm_documents(
    workspace: ChatQaWorkspace, attachments: Sequence[ChatAttachmentView], ids: Sequence[UUID]
) -> ChatQaWorkspace:
    by_id = {item.id: item for item in usable_attachments(attachments)}
    if not ids:
        raise QaError("qa_documents_empty", "請至少選一份資料。")
    unknown = [item for item in ids if item not in by_id]
    if unknown:
        raise QaError("qa_document_not_attached", "所選資料不在這個對話中，或尚無法使用。")
    workspace.documents = {
        "confirmed": True,
        "used": [
            {"id": str(by_id[item].id), "name": by_id[item].display_name, "source": by_id[item].source}
            for item in dict.fromkeys(ids)
        ],
    }
    return workspace


def allowed_sources(
    workspace: ChatQaWorkspace, attachments: Sequence[ChatAttachmentView]
) -> dict[str, ChatAttachmentView]:
    """Display name -> attachment that evidence may cite: confirmed documents, else any attached one."""

    candidates = usable_attachments(attachments)
    docs = workspace.documents or {}
    if docs.get("confirmed") and docs.get("used"):
        used = {item["id"] for item in docs["used"]}
        candidates = [item for item in candidates if str(item.id) in used]
    return {item.display_name: item for item in candidates}


def apply_evidence(
    workspace: ChatQaWorkspace,
    question_no: int,
    items: Sequence[EvidenceItemInput],
    attachments: Sequence[ChatAttachmentView],
) -> ChatQaWorkspace:
    _question(workspace, question_no)
    if not items:
        raise QaError("qa_evidence_empty", "沒有可記錄的內容。")
    sources = allowed_sources(workspace, attachments)
    problems: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        text, quote = item.text.strip(), item.quote.strip()
        source = sources.get(item.source_name.strip())
        if source is None:
            problems.append(f"來源「{item.source_name}」不是這個對話中可用的資料")
        elif not text or not quote:
            problems.append("每一項都必須有內容與出處原文")
        else:
            grouped.setdefault(item.field, []).append(
                {"text": text, "source": {"id": str(source.id), "name": source.display_name}, "quote": quote}
            )
    if problems:
        raise QaError("qa_evidence_rejected", "；".join(dict.fromkeys(problems)) + "。整批未記錄。")
    entry = {field: [] for field in EVIDENCE_FIELDS}
    entry.update(workspace.evidence.get(str(question_no), {}))
    entry.update(grouped)  # fields present in this call are replaced, the rest are kept
    workspace.evidence[str(question_no)] = entry
    return workspace


def _clean_outline(workspace: ChatQaWorkspace, outline: OutlineInput) -> dict[str, Any]:
    short = [point.strip() for point in outline.short if point.strip()]
    if not short:
        raise QaError("qa_outline_short_empty", "簡答至少要有一個要點。")
    detail = [
        {"title": section.title.strip(), "points": [p.strip() for p in section.points if p.strip()]}
        for section in outline.detail
        if section.title.strip()
    ] or [{"title": title, "points": []} for title in DEFAULT_DETAIL_TITLES]
    has_dispute = any(section["title"] == DISPUTE_TITLE for section in detail)
    if has_dispute and not outline.dispute_requested:
        raise QaError("qa_outline_dispute", "使用者沒有要求時，詳答不放「爭議點」。")
    if outline.dispute_requested and not has_dispute:
        detail.append({"title": DISPUTE_TITLE, "points": []})
    return {"short": short, "detail": detail, "dispute_requested": outline.dispute_requested, "confirmed": False}


def apply_outline(
    workspace: ChatQaWorkspace, question_no: int, outline: OutlineInput, *, confirmed: bool = False
) -> ChatQaWorkspace:
    _question(workspace, question_no)
    entry = _clean_outline(workspace, outline)
    entry["confirmed"] = confirmed
    workspace.outline[str(question_no)] = entry
    return workspace


def confirm_outline(workspace: ChatQaWorkspace, question_no: int) -> ChatQaWorkspace:
    _question(workspace, question_no)
    entry = workspace.outline.get(str(question_no))
    if entry is None:
        raise QaError("qa_outline_missing", f"第 {question_no} 題還沒有大綱。")
    entry["confirmed"] = True
    return workspace


def confirm_all_outlines(workspace: ChatQaWorkspace) -> ChatQaWorkspace:
    if not workspace.questions_confirmed or not (workspace.documents or {}).get("confirmed"):
        raise QaError("qa_steps_unconfirmed", "請先確認題目與資料。")
    missing = [item["no"] for item in workspace.questions if str(item["no"]) not in workspace.outline]
    if missing:
        raise QaError("qa_outline_missing", "第 " + "、".join(map(str, missing)) + " 題還沒有大綱。")
    for entry in workspace.outline.values():
        entry["confirmed"] = True
    return workspace


# ── Read model and cards ────────────────────────────────────────────────────


def to_read(workspace: ChatQaWorkspace) -> dict[str, Any]:
    docs = workspace.documents or {}
    return {
        "stage": workspace.stage,
        "questions": {"items": copy.deepcopy(workspace.questions), "confirmed": workspace.questions_confirmed},
        "documents": {"confirmed": bool(docs.get("confirmed")), "used": copy.deepcopy(docs.get("used", []))},
        "evidence": copy.deepcopy(workspace.evidence),
        "outline": copy.deepcopy(workspace.outline),
        "versions": copy.deepcopy(workspace.versions),
        "base_version_id": workspace.base_version_id,
        "updated_at": workspace.updated_at,
    }


def questions_card(workspace: ChatQaWorkspace) -> dict[str, Any]:
    return {
        "kind": "questions",
        "data": {"items": copy.deepcopy(workspace.questions), "confirmed": workspace.questions_confirmed},
    }


def evidence_card(workspace: ChatQaWorkspace, question_no: int) -> dict[str, Any]:
    question = _question(workspace, question_no)
    return {
        "kind": "evidence",
        "data": {
            "question_no": question_no,
            "question_text": question["text"],
            "evidence": copy.deepcopy(workspace.evidence.get(str(question_no), {})),
        },
    }


def outline_card(workspace: ChatQaWorkspace, question_no: int) -> dict[str, Any]:
    question = _question(workspace, question_no)
    return {
        "kind": "outline",
        "data": {
            "question_no": question_no,
            "question_text": question["text"],
            "outline": copy.deepcopy(workspace.outline[str(question_no)]),
        },
    }


# ── Instructions and turn context ───────────────────────────────────────────


def load_instructions() -> str:
    return INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()


def render_context(workspace: ChatQaWorkspace | None) -> str:
    """The workspace for the model, compact and free of identifiers."""

    workspace = workspace or new_workspace(UUID(int=0))
    stage = recompute_stage(clone(workspace))
    lines = [f"【立院QA 工作區】目前階段：{STAGE_LABELS[stage]}"]
    if not workspace.questions:
        lines.append("題目：尚未設定。")
    else:
        state = "已確認" if workspace.questions_confirmed else "尚未確認（等使用者在卡片上確認）"
        lines.append(f"題目（{state}）：")
        for item in workspace.questions:
            note = f"（備註：{item['note']}）" if item.get("note") else ""
            lines.append(f"{item['no']}. {item['text']}{note}")
    docs = workspace.documents or {}
    if docs.get("confirmed"):
        lines.append("資料：已確認使用 " + "、".join(f"【{item['name']}】" for item in docs.get("used", [])))
    else:
        lines.append("資料：使用者尚未確認要用哪些資料。")
    for item in workspace.questions:
        entry = workspace.evidence.get(str(item["no"]))
        if entry:
            lines.append(f"第 {item['no']} 題整理：")
            for field in EVIDENCE_FIELDS:
                for evidence in entry.get(field, []):
                    lines.append(f"- {EVIDENCE_LABELS[field]}：{evidence['text']}（【{evidence['source']['name']}】）")
        outline = workspace.outline.get(str(item["no"]))
        if outline:
            lines.append(f"第 {item['no']} 題大綱（{'已確認' if outline['confirmed'] else '待確認'}）：")
            lines.append("簡答：" + "；".join(outline["short"]))
            for section in outline["detail"]:
                lines.append(f"詳答／{section['title']}：" + "；".join(section["points"]))
    return "\n".join(lines)


# ── The model's tools ───────────────────────────────────────────────────────


def build_tools(
    runtime: SkillRuntime,
    attachments: Sequence[ChatAttachmentView],
    search: SearchFn,
    emit: EmitFn,
    tool_labels: dict[str, str],
) -> list[FunctionTool]:
    repository = runtime.repository
    session_id = runtime.session_id

    async def load() -> ChatQaWorkspace:
        stored = await repository.get_qa_workspace(session_id)
        return clone(stored) if stored is not None else new_workspace(session_id)

    async def save(workspace: ChatQaWorkspace) -> None:
        recompute_stage(workspace)
        await repository.save_qa_workspace(workspace)

    async def set_questions(ctx: ToolContext, questions: list[ToolQuestion]) -> str:
        """Record the list of questions the user may be asked, replacing the previous list.

        Args:
            questions: Every question in order, each with its text and an optional note
                (empty string when none). Numbers are assigned automatically.
        """

        try:
            workspace = apply_questions(
                await load(), [QuestionEdit(text=q.text, note=q.note) for q in questions], confirmed=False
            )
        except QaError as error:
            tool_labels[ctx.tool_call_id] = error.message
            return error.message
        await save(workspace)
        emit("questions", questions_card(workspace)["data"])
        tool_labels[ctx.tool_call_id] = f"整理出 {len(workspace.questions)} 題"
        return f"已列出 {len(workspace.questions)} 題，題目清單卡片已顯示；請使用者確認或修改後再往下。"

    async def suggest_documents(ctx: ToolContext, question_nos: list[int]) -> str:
        """Find knowledge-base documents that may help answer the questions. Does not attach anything.

        Args:
            question_nos: The question numbers to search for; empty means every question.
        """

        workspace = await load()
        if not workspace.questions_confirmed:
            tool_labels[ctx.tool_call_id] = "題目尚未確認"
            return "題目還沒有被使用者確認，請先請使用者確認題目清單。"
        wanted = [q for q in workspace.questions if not question_nos or q["no"] in question_nos]
        if not wanted:
            tool_labels[ctx.tool_call_id] = "找不到指定的題目"
            return "沒有符合的題號。"
        attached_ids = {item.id for item in attachments}
        card_items: list[dict[str, Any]] = []
        lines: list[str] = []
        for question in wanted:
            candidates: dict[Any, dict[str, Any]] = {}
            for hit in await search(question["text"], 10):
                if hit["document_id"] in candidates:
                    continue
                candidates[hit["document_id"]] = {
                    "id": str(hit["document_id"]),
                    "name": hit["name"],
                    "snippet": hit["snippet"][:200],
                    "attached": hit["document_id"] in attached_ids,
                }
                if len(candidates) >= MAX_CANDIDATES_PER_QUESTION:
                    break
            card_items.append(
                {"question_no": question["no"], "question_text": question["text"], "candidates": list(candidates.values())}
            )
            names = "、".join(f"【{c['name']}】" for c in candidates.values()) or "（沒有找到相關資料）"
            lines.append(f"第 {question['no']} 題：{names}")
        emit("documents", {"items": card_items, "confirmed": bool((workspace.documents or {}).get("confirmed"))})
        tool_labels[ctx.tool_call_id] = f"為 {len(wanted)} 題找資料"
        return "\n".join(lines) + "\n資料挑選卡片已顯示；請使用者勾選、上傳並確認。"

    async def record_evidence(ctx: ToolContext, question_no: int, items: list[EvidenceItemInput]) -> str:
        """Record organised evidence for one question. Every item needs its source and the supporting quote.

        Args:
            question_no: The question number.
            items: Evidence items. field is one of figures (數據), aim (政策目的),
                status (辦理現況), dispute (爭議), next_steps (後續工作). source_name must be
                the exact name of a document attached to this conversation; quote is the
                supporting text copied from that document. Fields included here replace
                what was recorded before for those fields.
        """

        workspace = await load()
        try:
            apply_evidence(workspace, question_no, items, attachments)
        except QaError as error:
            tool_labels[ctx.tool_call_id] = "未記錄：" + error.message[:40]
            return error.message
        await save(workspace)
        emit("evidence", evidence_card(workspace, question_no)["data"])
        tool_labels[ctx.tool_call_id] = f"記錄第 {question_no} 題整理"
        return f"第 {question_no} 題的整理已記錄（{len(items)} 項），卡片已顯示。"

    async def propose_outline(
        ctx: ToolContext,
        question_no: int,
        short: list[str],
        detail: list[DetailSection],
        dispute_requested: bool,
    ) -> str:
        """Propose the answer outline for one question; the user confirms it on the card.

        Args:
            question_no: The question number.
            short: 簡答 points (short answer), the key points in order.
            detail: 詳答 sections with their points; pass an empty list to use the defaults
                背景說明 / 目前辦理情形 / 未來工作重點.
            dispute_requested: True only if the user asked for a 爭議點 section.
        """

        workspace = await load()
        try:
            apply_outline(
                workspace, question_no, OutlineInput(short=short, detail=detail, dispute_requested=dispute_requested)
            )
        except QaError as error:
            tool_labels[ctx.tool_call_id] = "未記錄：" + error.message[:40]
            return error.message
        await save(workspace)
        emit("outline", outline_card(workspace, question_no)["data"])
        tool_labels[ctx.tool_call_id] = f"第 {question_no} 題大綱"
        return f"第 {question_no} 題大綱已提出，等使用者確認；目前還不會開始產製。"

    descriptions = {
        TOOL_SET_QUESTIONS: ("set_questions", set_questions, "把使用者要準備的題目整理成清單（會取代舊清單），並顯示題目清單卡片。"),
        TOOL_SUGGEST_DOCUMENTS: ("suggest_documents", suggest_documents, "針對每一題搜尋知識庫，列出可能有用的資料供使用者勾選；不會自動附加。"),
        TOOL_RECORD_EVIDENCE: ("record_evidence", record_evidence, "記錄某一題整理出的數據、政策目的、辦理現況、爭議與後續工作，每項都要附來源文件與原文。"),
        TOOL_PROPOSE_OUTLINE: ("propose_outline", propose_outline, "提出某一題的簡答與詳答大綱，讓使用者確認。"),
    }
    return [
        function_tool(fn, name_override=name, description_override=description, strict_mode=False)
        for name, fn, description in (
            (tool_name, entry[1], entry[2]) for tool_name, entry in descriptions.items()
        )
    ]


__all__ = [name for name in dir() if not name.startswith("_") and name.isupper()] + [
    "QaError", "SkillRuntime", "QuestionEdit", "EvidenceItemInput", "DetailSection", "OutlineInput",
    "apply_questions", "confirm_documents", "apply_evidence", "apply_outline", "confirm_outline",
    "confirm_all_outlines", "recompute_stage", "to_read", "render_context", "build_tools",
    "new_workspace", "clone", "load_instructions", "usable_attachments",
]
