from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.legislative_qa import QaRevision
from app.services.agentic.contracts import DeterministicValidationError, ReviewFinding, ReviewOutcome
from app.services.chat.skills.legislative_qa import QaError
from app.services.legislative_qa.document import Paragraph, QaDocQuestion, QaDocument, QaIssue, Section
from app.services.legislative_qa.numeric import numeric_issues
from app.services.legislative_qa.review import checker_issues, draft_shape, outcome_issues, to_outcome, numeric_found
from app.services.legislative_qa.revision import (
    ai_from_document, build_revised_document, in_scope, restore_out_of_scope, revision_note, validate_scope,
)
from app.services.legislative_qa.writer import AiOutput


def _scope(**values) -> QaRevision:
    base = {"base_artifact_id": uuid4(), "question_no": 1, "part": "short", "instruction": "x"}
    return QaRevision(**{**base, **values})


def _doc() -> QaDocument:
    def paragraphs(prefix: str, cite: int) -> list[Paragraph]:
        return [Paragraph(text=f"{prefix}一 1.5", cites=[cite]), Paragraph(text=f"{prefix}二", cites=[])]

    def question(no: int, first: int, second: int) -> QaDocQuestion:
        return QaDocQuestion(
            no=no, text=f"題{no}",
            short=[Section(heading="摘要", paragraphs=paragraphs(f"{no}短", first))],
            detail=[Section(heading="背景說明", paragraphs=paragraphs(f"{no}背", second)),
                    Section(heading="未來", paragraphs=paragraphs(f"{no}未", first))],
        )

    return QaDocument(kind="ai", title="t", questions=[question(1, 1, 2), question(2, 3, 3)], sources=["甲", "乙", "丙"],
                      issues=[QaIssue(question_no=2, part="short", kind="style", message="舊")])


def _input() -> dict:
    def spec(no: int, sources: list[str]) -> dict:
        return {
            "no": no, "text": f"題{no}", "short_points": [], "detail": [{"title": "背景說明", "points": []}, {"title": "未來", "points": []}],
            "evidence": [{"id": f"q{no}-e{i}", "field": "figures", "text": "t", "source": name, "quote": "1.5"}
                         for i, name in enumerate(sources, 1)],
        }

    return {"session_label": None, "questions": [spec(1, ["甲", "乙"]), spec(2, ["丙"])]}


def test_the_base_becomes_writer_output_with_evidence_ids():
    ai = ai_from_document(_doc(), _input())
    assert ai.questions[0].short[0].paragraphs[0].cites == ["q1-e1"]
    assert ai.questions[0].detail[0].paragraphs[0].cites == ["q1-e2"]
    assert ai.questions[1].detail[1].paragraphs[0].cites == ["q2-e1"]
    stale = _input()
    stale["questions"][0]["detail"][0]["title"] = "改過的標題"
    with pytest.raises(QaError) as error:
        ai_from_document(_doc(), stale)
    assert error.value.code == "qa_revision_stale"


def test_everything_out_of_scope_is_restored():
    base = ai_from_document(_doc(), _input())
    written = base.model_copy(deep=True)
    written.questions[0].short[0].paragraphs[0].text = "新短答"
    written.questions[0].detail[0].paragraphs[0].text = "亂改詳答"
    written.questions[1].short[0].paragraphs[0].text = "亂改第二題"
    restored = restore_out_of_scope(base, written, _scope())
    assert restored.questions[0].short[0].paragraphs[0].text == "新短答"
    assert restored.questions[0].detail == base.questions[0].detail
    assert restored.questions[1] == base.questions[1]
    both = restore_out_of_scope(base, written, _scope(part="both"))
    assert both.questions[0].detail[0].paragraphs[0].text == "亂改詳答" and both.questions[1] == base.questions[1]


def test_a_section_scope_restores_other_sections_and_refuses_a_changed_count():
    base = ai_from_document(_doc(), _input())
    written = base.model_copy(deep=True)
    written.questions[0].detail[0].paragraphs[0].text = "改"
    written.questions[0].detail[1].paragraphs[0].text = "不該改"
    restored = restore_out_of_scope(base, written, _scope(part="detail", section=0))
    assert restored.questions[0].detail[0].paragraphs[0].text == "改"
    assert restored.questions[0].detail[1] == base.questions[0].detail[1]
    written.questions[0].detail.pop()
    with pytest.raises(DeterministicValidationError):
        restore_out_of_scope(base, written, _scope(part="detail", section=0))


def test_the_revised_document_keeps_the_base_and_drops_unused_sources():
    base_doc = _doc()
    base = ai_from_document(base_doc, _input())
    written = base.model_copy(deep=True)
    # the new short answer cites only 乙; 甲 was cited by question 1's short and 未來 sections
    written.questions[0].short[0].paragraphs[0].cites = ["q1-e2"]
    revised = build_revised_document(base_doc, _input(), restore_out_of_scope(base, written, _scope()), _scope())
    assert revised.questions[0].short[0].paragraphs[0].cites == [2]
    assert revised.questions[0].detail == base_doc.questions[0].detail and revised.questions[1] == base_doc.questions[1]
    assert revised.sources == base_doc.sources
    # now nothing cites 乙 any more except through question 1's background: dropping it renumbers
    written.questions[0].short[0].paragraphs[0].cites = ["q1-e1"]
    written.questions[0].detail[0].paragraphs[0].cites = ["q1-e1"]
    revised = build_revised_document(
        base_doc, _input(), restore_out_of_scope(base, written, _scope(part="both")), _scope(part="both"))
    assert revised.sources == ["甲", "丙"] and revised.questions[1].short[0].paragraphs[0].cites == [2]


def test_scope_must_exist_in_the_base():
    doc = _doc()
    validate_scope(doc, _scope(part="detail", section=1))
    for bad in (_scope(question_no=9), _scope(part="detail", section=2), _scope(part="both", section=0)):
        with pytest.raises(QaError):
            validate_scope(doc, bad)


def test_issues_in_scope():
    short = QaIssue(question_no=1, part="short", kind="style", message="m")
    assert in_scope(short, _scope()) and not in_scope(short, _scope(part="detail"))
    assert in_scope(short.model_copy(update={"kind": "inconsistent"}), _scope(part="detail"))
    assert not in_scope(short.model_copy(update={"question_no": 2}), _scope(part="both"))
    sec = short.model_copy(update={"part": "detail", "section": 1})
    assert in_scope(sec, _scope(part="detail", section=1)) and not in_scope(sec, _scope(part="detail", section=0))


def test_revision_note_is_short():
    assert revision_note("  改得\n肯定  一點 ") == "改得 肯定 一點"
    assert len(revision_note("字" * 300)) == 100


def _finding(location: str, key: str = "q1:short:style:x") -> ReviewFinding:
    return ReviewFinding(finding_id="f", category="style", issue_key=key, locations=(location,), description="d")


def test_checker_locations_are_one_based_and_checked_against_the_draft():
    output = AiOutput.model_validate({"questions": [
        {"no": 1, "short": [{"heading": "a", "paragraphs": [{"text": "x", "cites": []}]}], "detail": []}]})
    shape = draft_shape(output)
    review = ReviewOutcome(summary="s", findings=[
        _finding("q1:short:s1:p1", "q1:short:s1:p1:style:a"), _finding("q1:short:s1:p5", "k2"),
        _finding("q1:short:s4", "k3"), _finding("q1:short", "k4"), _finding("q1:short:p1", "k5"),
    ])
    found = [(i.section, i.paragraph) for i, _ in checker_issues(review, {1}, shape)]
    assert found == [(0, 0), (0, None), (None, None), (None, None), (None, None)]
    assert [(i.section, i.paragraph) for i, _ in checker_issues(review, {1})][:3] == [(0, 0), (0, 4), (3, None)]


def test_locations_survive_the_review_round_trip():
    issue = QaIssue(question_no=2, part="detail", kind="numeric", message="m", section=1, paragraph=0)
    outcome = to_outcome("s", numeric_found([issue, issue.model_copy(update={"section": None, "paragraph": None, "message": "n"})]))
    assert outcome.findings[0].locations == ("q2:detail:s2:p1",) and outcome.findings[1].locations == ("q2:detail",)
    assert outcome_issues(outcome) == [issue, issue.model_copy(update={"section": None, "paragraph": None, "message": "n"})]


def test_numeric_issues_know_their_section_and_paragraph():
    output = AiOutput.model_validate({"questions": [{"no": 1, "short": [
        {"heading": "a", "paragraphs": [{"text": "共 5 人", "cites": ["q1-e1"]}]},
        {"heading": "b", "paragraphs": [{"text": "好", "cites": []}, {"text": "共 7 人", "cites": ["q1-e1"]}]}], "detail": []}]})
    frozen = {"questions": [{"no": 1, "text": "題", "short_points": [], "detail": [],
                              "evidence": [{"id": "q1-e1", "quote": "共 5 人"}]}]}
    [issue] = numeric_issues(frozen, output)
    assert (issue.part, issue.section, issue.paragraph) == ("short", 1, 1)
