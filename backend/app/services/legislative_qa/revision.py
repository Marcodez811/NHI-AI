"""Scoped revision of one 立院QA version: seed the writer with the base, restore everything out of scope.

Pure functions. The base is a stored ``QaDocument``; the writer works on the same ``qa.json`` shape as
for a first draft (cites are evidence ids), and the final document is assembled from the base so that
every paragraph outside the scope is carried over verbatim.
"""

from __future__ import annotations

from typing import Any

from app.models.legislative_qa import QaRevision
from app.services.agentic.contracts import DeterministicValidationError
from app.services.chat.skills.legislative_qa import QaError
from app.services.legislative_qa.document import Paragraph, QaDocQuestion, QaDocument, QaIssue, Section, SourceIndex
from app.services.legislative_qa.writer import AiOutput, AiParagraph, AiQuestion, AiSection, document_section

NOTE_LIMIT = 100


def scoped_parts(scope: QaRevision) -> tuple[str, ...]:
    return ("short", "detail") if scope.part == "both" else (scope.part,)


def revision_note(instruction: str) -> str:
    note = " ".join(instruction.split())
    return note if len(note) <= NOTE_LIMIT else note[: NOTE_LIMIT - 1] + "…"


def in_scope(issue: QaIssue, scope: QaRevision) -> bool:
    """Whether the revision is responsible for ``issue`` (a disagreement of 簡答 and 詳答 always is)."""

    if issue.question_no != scope.question_no:
        return False
    if issue.kind == "inconsistent":
        return True
    if issue.part not in scoped_parts(scope):
        return False
    return scope.section is None or issue.section in (None, scope.section)


def validate_scope(base: QaDocument, scope: QaRevision) -> None:
    """The scope must point at a question, part and section the base really has."""

    question = next((item for item in base.questions if item.no == scope.question_no), None)
    if question is None:
        raise QaError("qa_question_not_found", f"沒有第 {scope.question_no} 題。")
    if scope.section is not None:
        if scope.part == "both":
            raise QaError("qa_revision_scope", "指定段落時要選擇簡答或詳答其中之一。")
        if scope.section >= len(getattr(question, scope.part)):
            raise QaError("qa_revision_scope", f"第 {scope.question_no} 題沒有第 {scope.section + 1} 個段落。")


def _evidence_ids(writer_input: dict[str, Any], no: int) -> dict[str, list[str]]:
    spec = next(item for item in writer_input["questions"] if item["no"] == no)
    by_source: dict[str, list[str]] = {}
    for item in spec["evidence"]:
        by_source.setdefault(item["source"], []).append(item["id"])
    return by_source


def _ai_section(section: Section, names: list[str], ids: dict[str, list[str]]) -> AiSection:
    paragraphs = []
    for paragraph in section.paragraphs:
        cites: list[str] = []
        for number in paragraph.cites:
            cites += [cite for cite in ids.get(names[number - 1], []) if cite not in cites]
        paragraphs.append(AiParagraph(text=paragraph.text, cites=cites))
    return AiSection(heading=section.heading or "", paragraphs=paragraphs)


def ai_from_document(base: QaDocument, writer_input: dict[str, Any]) -> AiOutput:
    """The base as the writer's ``qa.json``: each source cite becomes that question's evidence ids of the source."""

    specs = writer_input["questions"]
    if [question.no for question in base.questions] != [spec["no"] for spec in specs] or any(
        [section.heading for section in question.detail] != [section["title"] for section in spec["detail"]]
        for question, spec in zip(base.questions, specs)
    ):
        raise QaError("qa_revision_stale", "題目或大綱已經和這個版本不同，請重新產生文件。")
    questions = []
    for question in base.questions:
        ids = _evidence_ids(writer_input, question.no)
        questions.append(AiQuestion(
            no=question.no,
            short=[_ai_section(section, base.sources, ids) for section in question.short],
            detail=[_ai_section(section, base.sources, ids) for section in question.detail],
        ))
    return AiOutput(questions=questions)


def _restored(base: list[AiSection], new: list[AiSection], scope: QaRevision) -> list[AiSection]:
    if scope.section is None:
        return new
    if len(new) != len(base):
        raise DeterministicValidationError(f"只修改第 {scope.section + 1} 個段落時，不可增減段落數量（應為 {len(base)} 個）。")
    return [new[index] if index == scope.section else base[index] for index in range(len(base))]


def restore_out_of_scope(base: AiOutput, output: AiOutput, scope: QaRevision) -> AiOutput:
    """``output`` with every question, part and section outside the scope replaced by the base's."""

    originals = {question.no: question for question in base.questions}
    parts = scoped_parts(scope)
    questions = []
    for answer in output.questions:
        original = originals.get(answer.no)
        if original is None or answer.no != scope.question_no:
            questions.append(original or answer)
            continue
        questions.append(AiQuestion(
            no=answer.no,
            short=_restored(original.short, answer.short, scope) if "short" in parts else original.short,
            detail=_restored(original.detail, answer.detail, scope) if "detail" in parts else original.detail,
        ))
    return AiOutput(questions=questions)


def _used_remap(questions: list[QaDocQuestion], names: list[str]) -> tuple[list[str], dict[int, int]]:
    used = sorted({cite for question in questions for part in (question.short, question.detail)
                   for section in part for paragraph in section.paragraphs for cite in paragraph.cites})
    remap = {old: new for new, old in enumerate(used, 1)}
    return [names[old - 1] for old in used], remap


def build_revised_document(
    base: QaDocument, writer_input: dict[str, Any], output: AiOutput, scope: QaRevision
) -> QaDocument:
    """The base with only the scoped sections replaced by the writer's; unused sources are dropped."""

    sources = SourceIndex()
    sources.names = list(base.sources)
    answers = {answer.no: answer for answer in output.questions}
    parts = scoped_parts(scope)
    questions: list[QaDocQuestion] = []
    for question in base.questions:
        copy = question.model_copy(deep=True)
        if question.no == scope.question_no:
            ids = {item["id"]: item["source"] for item in next(
                spec for spec in writer_input["questions"] if spec["no"] == question.no)["evidence"]}
            for part in parts:
                written = getattr(answers[question.no], part)
                current = getattr(copy, part)
                indexes = range(len(written)) if scope.section is None else [scope.section]
                rebuilt = {index: document_section(written[index], ids, sources) for index in indexes}
                setattr(copy, part, [rebuilt[index] for index in indexes] if scope.section is None
                        else [rebuilt.get(index, current[index]) for index in range(len(current))])
        questions.append(copy)
    names, remap = _used_remap(questions, sources.names)
    for question in questions:
        for part in (question.short, question.detail):
            for section in part:
                section.paragraphs = [
                    Paragraph(text=paragraph.text, cites=[remap[cite] for cite in paragraph.cites])
                    for paragraph in section.paragraphs
                ]
    return base.model_copy(update={"questions": questions, "sources": names})
