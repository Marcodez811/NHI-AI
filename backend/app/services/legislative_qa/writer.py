"""AI writer contract for the 立院QA document: frozen input, strict output check, document mapping.

Pure functions only (no model, database or file access) so the adapter stays thin and
slice C (checker / numeric check) can reuse the evidence ids carried in ``cites``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.chat import ChatQaWorkspace
from app.services.agentic.contracts import DeterministicValidationError
from app.services.chat.skills.legislative_qa import EVIDENCE_FIELDS, QaError
from app.services.legislative_qa.document import (
    SOURCE_TAG,
    Paragraph,
    QaDocQuestion,
    QaDocument,
    Section,
    SourceIndex,
    document_title,
)

HEADING_NUMBER = re.compile(r"^\s*[一二三四五六七八九十]+、\s*")
FORBIDDEN_TEXT = (
    (re.compile(r"[【】]"), "不可含【】來源標籤"),
    (re.compile(r"證據"), "不可提到「證據」"),
    (re.compile(r"\b\w[\w\-]*\.(?:pdf|docx?|xlsx?|pptx?|txt|md|json|csv)\b", re.I), "不可含檔名"),
    (re.compile(r"\bq\d+-e\d+\b"), "不可把證據編號寫進內文"),
)
MAX_SHORT_SECTIONS = 4
MAX_PROBLEMS = 8


class AiParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    cites: list[str] = Field(default_factory=list)


class AiSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    paragraphs: list[AiParagraph] = Field(default_factory=list)


class AiQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: int
    short: list[AiSection] = Field(default_factory=list)
    detail: list[AiSection] = Field(default_factory=list)


class AiOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[AiQuestion]


def _strip_tags(text: str) -> str:
    return " ".join(SOURCE_TAG.sub("", text).split())


def evidence_items(workspace: ChatQaWorkspace, no: int) -> list[dict[str, str]]:
    """The question's evidence flattened with stable ids ``q<no>-e<n>`` (field order, then entry order)."""

    entry = workspace.evidence.get(str(no), {})
    items: list[dict[str, str]] = []
    for field in EVIDENCE_FIELDS:
        for item in entry.get(field, []):
            items.append({
                "id": f"q{no}-e{len(items) + 1}", "field": field, "text": item["text"],
                "source": item["source"]["name"], "quote": item["quote"],
            })
    return items


def build_writer_input(workspace: ChatQaWorkspace, session_label: str | None) -> dict[str, Any]:
    """Frozen author input. Outline notes (備註／待補) and other questions' evidence never appear."""

    unconfirmed = [
        item["no"] for item in workspace.questions if not workspace.outline.get(str(item["no"]), {}).get("confirmed")
    ]
    if not workspace.questions or unconfirmed:
        raise QaError("qa_outline_unconfirmed", "大綱還沒有全部確認。")
    questions = []
    for item in workspace.questions:
        outline = workspace.outline[str(item["no"])]
        questions.append({
            "no": item["no"],
            "text": item["text"],
            "short_points": [_strip_tags(point) for point in outline.get("short", [])],
            "detail": [
                {"title": section["title"], "points": [_strip_tags(point) for point in section.get("points", [])]}
                for section in outline.get("detail", [])
            ],
            "dispute_requested": bool(outline.get("dispute_requested")),
            "evidence": evidence_items(workspace, item["no"]),
        })
    return {"session_label": (session_label or "").strip() or None, "questions": questions}


def _clean_heading(heading: str) -> str:
    return HEADING_NUMBER.sub("", heading.strip()).rstrip("：:").strip()


def _paragraph_problems(label: str, paragraph: AiParagraph, known: set[str]) -> list[str]:
    problems = []
    if not paragraph.text.strip():
        return [f"{label}有空白段落"]
    for pattern, message in FORBIDDEN_TEXT:
        if pattern.search(paragraph.text):
            problems.append(f"{label}{message}")
    unknown = [cite for cite in paragraph.cites if cite not in known]
    if unknown:
        problems.append(f"{label}引用了不存在的證據編號 {', '.join(unknown)}")
    if re.search(r"\d", paragraph.text) and not paragraph.cites:
        problems.append(f"{label}含數字但沒有引用任何證據編號")
    return problems


def _section_problems(label: str, section: AiSection, known: set[str]) -> list[str]:
    problems = [] if section.heading.strip() else [f"{label}沒有標題"]
    if not section.paragraphs:
        problems.append(f"{label}沒有段落")
    for number, paragraph in enumerate(section.paragraphs, 1):
        problems.extend(_paragraph_problems(f"{label}第 {number} 段", paragraph, known))
    return problems


def _question_problems(spec: dict[str, Any], answer: AiQuestion) -> list[str]:
    label = f"第 {spec['no']} 題"
    known = {item["id"] for item in spec["evidence"]}
    problems: list[str] = []
    if not 1 <= len(answer.short) <= MAX_SHORT_SECTIONS:
        problems.append(f"{label}簡答要有 1 到 {MAX_SHORT_SECTIONS} 個段落標題，目前 {len(answer.short)} 個")
    expected = [section["title"] for section in spec["detail"]]
    actual = [_clean_heading(section.heading) for section in answer.detail]
    if actual != expected:
        problems.append(f"{label}詳答標題必須依序為 {expected}，目前是 {actual}")
    for kind, sections in (("簡答", answer.short), ("詳答", answer.detail)):
        for number, section in enumerate(sections, 1):
            problems.extend(_section_problems(f"{label}{kind}第 {number} 節", section, known))
    return problems


def check_output(writer_input: dict[str, Any], raw: str) -> AiOutput:
    """Parse and strictly validate ``output/qa.json``; raises a correctable error naming every problem."""

    try:
        output = AiOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DeterministicValidationError(f"output/qa.json 格式不正確：{str(exc)[:300]}") from exc
    specs = writer_input["questions"]
    answers = {answer.no: answer for answer in output.questions}
    problems: list[str] = []
    if [answer.no for answer in output.questions] != [spec["no"] for spec in specs]:
        problems.append(f"questions 必須依序包含第 {[spec['no'] for spec in specs]} 題各一次")
    for spec in specs:
        if spec["no"] in answers:
            problems.extend(_question_problems(spec, answers[spec["no"]]))
    if problems:
        raise DeterministicValidationError("；".join(problems[:MAX_PROBLEMS]))
    return output


def document_section(section: AiSection, ids: dict[str, str], sources: SourceIndex) -> Section:
    paragraphs = []
    for paragraph in section.paragraphs:
        cites: list[int] = []
        for cite in paragraph.cites:
            number = sources.cite(ids[cite])
            if number not in cites:
                cites.append(number)
        paragraphs.append(Paragraph(text=paragraph.text.strip(), cites=cites))
    return Section(heading=_clean_heading(section.heading), paragraphs=paragraphs)


def build_ai_document(writer_input: dict[str, Any], output: AiOutput) -> QaDocument:
    """Map evidence-id cites to the document's global, de-duplicated source list (1-based cites)."""

    sources = SourceIndex()
    specs = {spec["no"]: spec for spec in writer_input["questions"]}
    questions = []
    for answer in output.questions:
        spec = specs[answer.no]
        ids = {item["id"]: item["source"] for item in spec["evidence"]}
        questions.append(QaDocQuestion(
            no=answer.no, text=spec["text"],
            short=[document_section(section, ids, sources) for section in answer.short],
            detail=[document_section(section, ids, sources) for section in answer.detail],
        ))
    label = writer_input.get("session_label")
    return QaDocument(
        title=document_title(label, len(questions)), session_label=label,
        questions=questions, sources=sources.names,
    )
