"""Numeric check (no model): every number in a written paragraph must appear in the quotes it cites.

Amounts are compared by value, so ``10.662億``, ``1,066,200千元`` and ``1,066,200,000元`` agree, full-width
digits and thousands separators are ignored, and a Western year in the evidence matches its ROC year.
Numbering markers, question numbers, names with attached letters (COVID-19) and the years, months and
quarters already stated in the question or the confirmed outline are not counted.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.legislative_qa.document import QaIssue

_WIDE = str.maketrans("０１２３４５６７８９．％", "0123456789.%")
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_TOKEN = re.compile(rf"(?P<yi>{_NUMBER})\s*億\s*(?:(?P<wan>{_NUMBER})\s*萬)?|(?P<num>{_NUMBER})\s*(?P<unit>百萬|兆|萬|千)?")
_UNITS = {"兆": Decimal(10) ** 12, "百萬": Decimal(10) ** 6, "萬": Decimal(10) ** 4, "千": Decimal(10) ** 3}
_MARKER = re.compile(r"^[ \t]*(?:[（(]\d{1,2}[)）]|\d{1,2}(?:[、]|[.．](?!\d)))[ \t]*", re.M)
_CALENDAR_UNIT = "年月日季度"
MAX_LISTED = 6


def _normalize(text: str) -> str:
    return text.translate(_WIDE)


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "")).normalize()
    except InvalidOperation:
        return None


def _scaled(match: re.Match[str]) -> Decimal | None:
    if match.group("yi") is not None:
        total = _decimal(match.group("yi"))
        if total is None:
            return None
        total *= Decimal(10) ** 8
        if match.group("wan") is not None:
            wan = _decimal(match.group("wan"))
            total += (wan or Decimal(0)) * Decimal(10) ** 4
        return total.normalize()
    value = _decimal(match.group("num"))
    unit = match.group("unit")
    return value if value is None or unit is None else (value * _UNITS[unit]).normalize()


def _tokens(text: str) -> list[tuple[str, Decimal, str, str]]:
    """(display form, value, previous character, next character) for every number in ``text``."""

    found = []
    for match in _TOKEN.finditer(text):
        value = _scaled(match)
        if value is None:
            continue
        before = text[max(match.start() - 1, 0):match.start()]
        if before == "-":  # COVID-19: the name's letters, not the dash, decide
            before = text[max(match.start() - 2, 0):match.start() - 1]
        after = text[match.end()] if match.end() < len(text) else ""
        found.append(("".join(match.group(0).split()), value, before, after))
    return found


def evidence_values(quotes: list[str]) -> set[Decimal]:
    """Every value the quotes state, including each Western year as its ROC year."""

    values: set[Decimal] = set()
    for quote in quotes:
        for _, value, _, after in _tokens(_normalize(quote)):
            values.add(value)
            if value > 1911 and after == "年":
                values.add(value - 1911)
    return values


def context_calendar_values(texts: list[str]) -> set[Decimal]:
    """Years, months, days and quarters stated in the question or confirmed outline."""

    allowed: set[Decimal] = set()
    for text in texts:
        for _, value, _, after in _tokens(_normalize(text)):
            if after and after in _CALENDAR_UNIT:
                allowed.add(value)
    return allowed


def unsupported_numbers(text: str, supported: set[Decimal]) -> list[str]:
    """Numbers of one written paragraph that no cited quote states, in order, without repeats."""

    body = _MARKER.sub("", _normalize(text))
    missing: list[str] = []
    for shown, value, before, after in _tokens(body):
        if value in supported or before.isascii() and before.isalpha() or after == "題" or shown in missing:
            continue
        missing.append(shown)
    return missing


def _label(part: str, heading: str, number: int) -> str:
    return f"{'簡答' if part == 'short' else '詳答'}「{heading or '（無標題）'}」第 {number} 段"


def _paragraph_issue(
    spec: dict[str, Any], part: str, section: Any, number: int, paragraph: Any, quotes: dict[str, str], allowed: set[Decimal],
) -> QaIssue | None:
    cited = [quotes[cite] for cite in paragraph.cites if cite in quotes]
    missing = unsupported_numbers(paragraph.text, evidence_values(cited) | allowed)
    if not missing:
        return None
    listed = "、".join(missing[:MAX_LISTED])
    return QaIssue(
        question_no=spec["no"], part=part, kind="numeric",
        message=f"{_label(part, section.heading, number)}的數字 {listed} 沒有出現在所引用的資料原文中，請改成原文的數字或刪除。",
    )


def numeric_issues(writer_input: dict[str, Any], output: Any) -> list[QaIssue]:
    """One issue per paragraph that states a number its cited quotes do not."""

    specs = {spec["no"]: spec for spec in writer_input["questions"]}
    issues: list[QaIssue] = []
    for answer in output.questions:
        spec = specs[answer.no]
        quotes = {item["id"]: item["quote"] for item in spec["evidence"]}
        outline_text = [spec["text"], *spec["short_points"]]
        for section in spec["detail"]:
            outline_text += [section["title"], *section["points"]]
        allowed = context_calendar_values(outline_text)
        for part, sections in (("short", answer.short), ("detail", answer.detail)):
            for section in sections:
                for number, paragraph in enumerate(section.paragraphs, 1):
                    issue = _paragraph_issue(spec, part, section, number, paragraph, quotes, allowed)
                    if issue is not None:
                        issues.append(issue)
    return issues
