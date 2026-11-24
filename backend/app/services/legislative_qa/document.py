"""立院QA document: the structured content model and the deterministic outline -> content builder.

The same shape is what the later AI writer will produce
(docs/10_6_legislative_qa_skill_plan.md sections 4, 5 and 7).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.models.chat import ChatQaWorkspace
from app.services.chat.skills.legislative_qa import QaError

SOURCE_TAG = re.compile(r"【([^【】]+)】")


class Paragraph(BaseModel):
    text: str
    # 1-based indexes into ``QaDocument.sources``.
    cites: list[int] = Field(default_factory=list)


class Section(BaseModel):
    heading: str | None = None
    paragraphs: list[Paragraph] = Field(default_factory=list)


class QaDocQuestion(BaseModel):
    no: int
    text: str
    short: list[Section] = Field(default_factory=list)
    detail: list[Section] = Field(default_factory=list)


class QaDocument(BaseModel):
    # "outline" = deterministic outline wording; "ai" = written by the legislative_qa workflow.
    kind: str = "outline"
    title: str
    session_label: str | None = None
    questions: list[QaDocQuestion] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


def document_title(session_label: str | None, count: int) -> str:
    label = (session_label or "").strip()
    return f"{label} 立法院QA（{count} 題）" if label else f"立法院QA（{count} 題）"


class SourceIndex:
    """De-duplicated global source list; ``cite`` returns the 1-based number."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def cite(self, name: str) -> int:
        if name not in self.names:
            self.names.append(name)
        return self.names.index(name) + 1


def _paragraph(raw: str, sources: SourceIndex) -> Paragraph | None:
    names = [part.strip() for part in SOURCE_TAG.findall(raw)]
    text = " ".join(SOURCE_TAG.sub("", raw).split())
    if not text:
        return None
    cites: list[int] = []
    for name in filter(None, names):
        number = sources.cite(name)
        if number not in cites:
            cites.append(number)
    return Paragraph(text=text, cites=cites)


def _paragraphs(points: list[str], sources: SourceIndex) -> list[Paragraph]:
    return [item for item in (_paragraph(point, sources) for point in points) if item is not None]


def _question(item: dict[str, Any], outline: dict[str, Any], sources: SourceIndex) -> QaDocQuestion:
    short = [Section(heading=None, paragraphs=_paragraphs(outline.get("short", []), sources))]
    detail = [
        Section(heading=section["title"], paragraphs=_paragraphs(section.get("points", []), sources))
        for section in outline.get("detail", [])
    ]
    return QaDocQuestion(no=item["no"], text=item["text"], short=short, detail=detail)


def build_document(workspace: ChatQaWorkspace, session_label: str | None = None) -> QaDocument:
    """Confirmed outlines -> QaDocument. Outline notes (備註／待補) are never copied."""

    if not workspace.questions:
        raise QaError("qa_outline_missing", "還沒有題目。")
    unconfirmed = [
        item["no"] for item in workspace.questions if not workspace.outline.get(str(item["no"]), {}).get("confirmed")
    ]
    if unconfirmed:
        raise QaError("qa_outline_unconfirmed", "第 " + "、".join(map(str, unconfirmed)) + " 題的大綱還沒有確認。")
    sources = SourceIndex()
    questions = [_question(item, workspace.outline[str(item["no"])], sources) for item in workspace.questions]
    label = (session_label or "").strip() or None
    return QaDocument(
        title=document_title(label, len(questions)),
        session_label=label,
        questions=questions,
        sources=sources.names,
    )
