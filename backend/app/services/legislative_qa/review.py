"""Verification loop glue for the 立院QA writer: checker findings, numeric issues and revision feedback.

Everything here is pure. Issues travel through the generic agentic review contract
(``ReviewOutcome``) so the existing stagnation logic can tell, round to round, which issues were
resolved; their identity is the stable ``issue_key``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.agentic.contracts import ReviewFinding, ReviewOutcome
from app.services.legislative_qa.document import ISSUE_KINDS, QaIssue

CHECKER_KINDS = tuple(kind for kind in ISSUE_KINDS if kind != "numeric")
KIND_LABELS = {
    "numeric": "數字與原文不符",
    "inconsistent": "簡答與詳答不一致",
    "unsupported": "資料中沒有依據",
    "off_question": "偏離題目",
    "outline": "與已確認的大綱不符",
    "style": "格式或語氣",
}
REVIEW_MARK = "[QA-REVIEW]"
# ``q2:detail`` optionally followed by ``:s2:p1`` (1-based section and paragraph, as the checker reads them).
_LOCATION = re.compile(r"q(\d+)\s*[:：]\s*(short|detail)(?:\s*[:：]\s*s(\d+))?(?:\s*[:：]\s*p(\d+))?", re.I)
_KEY_PREFIX = re.compile(r"^q\d+:(?:short|detail):(?:s\d+:)?(?:p\d+:)?", re.I)

Found = tuple[QaIssue, str]
# (question no, part) -> paragraph count of every section; lets a located issue be checked against the draft.
Shape = dict[tuple[int, str], list[int]]


def _issue_key(issue: QaIssue, raw: str) -> str:
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"q{issue.question_no}:{issue.part}:{issue.kind}:{digest}"


def _locate(finding: ReviewFinding) -> tuple[int, str, int | None, int | None] | None:
    for place in (*finding.locations, finding.issue_key):
        match = _LOCATION.search(place or "")
        if match:
            section, paragraph = (int(group) - 1 if group and int(group) > 0 else None for group in match.group(3, 4))
            return int(match.group(1)), match.group(2).lower(), section, paragraph
    return None


def _inside(shape: Shape | None, place: tuple[int, str, int | None, int | None]) -> tuple[int | None, int | None]:
    """The section and paragraph of a location, dropped when the draft has no such place."""

    number, part, section, paragraph = place
    if shape is None:
        return section, paragraph
    counts = shape.get((number, part), [])
    if section is None or section >= len(counts):
        return None, None
    return section, paragraph if paragraph is not None and paragraph < counts[section] else None


def checker_issues(review: ReviewOutcome, question_numbers: set[int], shape: Shape | None = None) -> list[Found]:
    """The checker's findings as issues; those with an unknown kind, question or place are dropped."""

    found: list[Found] = []
    seen: set[str] = set()
    for finding in review.findings:
        place = _locate(finding)
        kind = finding.category.strip().lower()
        if place is None or kind not in CHECKER_KINDS or place[0] not in question_numbers:
            continue
        message = finding.description.strip() or finding.reason.strip()
        if not message:
            continue
        if finding.correction.strip():
            message = f"{message}（建議：{finding.correction.strip()}）"
        section, paragraph = _inside(shape, place)
        issue = QaIssue(
            question_no=place[0], part=place[1], kind=kind, message=message, section=section, paragraph=paragraph
        )
        key = _issue_key(issue, _KEY_PREFIX.sub("", finding.issue_key.strip()) or message)
        if key not in seen:
            seen.add(key)
            found.append((issue, key))
    return found


def numeric_found(issues: list[QaIssue]) -> list[Found]:
    return [(issue, _issue_key(issue, issue.message)) for issue in issues]


def _place(issue: QaIssue) -> str:
    place = f"q{issue.question_no}:{issue.part}"
    if issue.section is not None:
        place += f":s{issue.section + 1}"
        if issue.paragraph is not None:
            place += f":p{issue.paragraph + 1}"
    return place


def to_outcome(summary: str, found: list[Found]) -> ReviewOutcome:
    """Numeric first, then the checker's; ids are the stable keys so rounds can be compared."""

    findings = [
        ReviewFinding(
            finding_id=key[:120], category=issue.kind, issue_key=key, locations=(_place(issue),),
            description=issue.message,
        )
        for issue, key in found
    ]
    return ReviewOutcome(summary=summary[:4000], findings=findings)


def outcome_issues(review: ReviewOutcome) -> list[QaIssue]:
    issues = []
    for finding in review.findings:
        place = _locate(finding)
        if place is not None:
            issues.append(QaIssue(
                question_no=place[0], part=place[1], kind=finding.category, message=finding.description,
                section=place[2], paragraph=place[3] if place[2] is not None else None,
            ))
    return issues


def draft_shape(output: Any) -> Shape:
    """The draft's sections and paragraph counts (``output`` is a validated writer output)."""

    return {
        (answer.no, part): [len(section.paragraphs) for section in sections]
        for answer in output.questions
        for part, sections in (("short", answer.short), ("detail", answer.detail))
    }


def part_label(part: str) -> str:
    return "簡答" if part == "short" else "詳答"


def revision_feedback(issues: list[QaIssue]) -> str:
    """The text the writer receives; the marker tells the prompt builder to revise only these parts."""

    lines = [
        f"- 第 {issue.question_no} 題{part_label(issue.part)}［{KIND_LABELS.get(issue.kind, issue.kind)}］{issue.message}"
        for issue in issues
    ]
    return REVIEW_MARK + "\n" + "\n".join(lines)


def problem_counts(history: list[dict[str, Any]]) -> list[int]:
    return [len(item.get("review", {}).get("findings", [])) for item in history]
