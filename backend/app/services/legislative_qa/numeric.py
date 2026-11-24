"""Numeric check (no model): every number in a written paragraph must appear in the quotes it cites.

Amounts are compared by value, so ``10.662億``, ``1,066,200千元`` and ``1,066,200,000元`` agree, full-width
digits and thousands separators are ignored, and a Western year in the evidence matches its ROC year.
Numbering markers, question numbers, names with attached letters (COVID-19) and the years, months and
quarters already stated in the question or the confirmed outline are not counted.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple

from app.services.legislative_qa.document import QaIssue

_WIDE = str.maketrans("０１２３４５６７８９．％", "0123456789.%")
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_TOKEN = re.compile(rf"(?P<yi>{_NUMBER})\s*億\s*(?:(?P<wan>{_NUMBER})\s*萬)?|(?P<num>{_NUMBER})\s*(?P<scale>百萬|兆|萬|千)?")
_SCALES = {"兆": Decimal(10) ** 12, "百萬": Decimal(10) ** 6, "萬": Decimal(10) ** 4, "千": Decimal(10) ** 3}
_YI = Decimal(10) ** 8
_MARKER = re.compile(r"^[ \t]*(?:[（(]\d{1,2}[)）]|\d{1,2}(?:[、]|[.．](?!\d)))[ \t]*", re.M)
_CALENDAR_UNIT = "年月日季度"
# the measure word right after a number; 點 is not one in 點值/點數
_UNIT = re.compile(r"[ \t]?(個月|人次|年度|點(?![值數])|[家個所人名件次床元年月日季歲項筆張倍天例場科%])")
_RANGE_JOIN = re.compile(r"\s*(?:至|到|~|～|-|－|、|及|和|或)\s*第?")
_UNIT_ALIAS = {"所": "家", "名": "人", "年度": "年"}
_CALENDAR = {"年", "月", "日", "季", "歲", "個月"}
_NOT_COUNT = _CALENDAR | {"元", "%"}
ANY_UNIT = "*"
MAX_LISTED = 6

Pair = tuple[Decimal, str | None]


class _Tok(NamedTuple):
    shown: str
    value: Decimal
    before: str
    after: str
    unit: str | None


def _normalize(text: str) -> str:
    return text.translate(_WIDE)


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "")).normalize()
    except InvalidOperation:
        return None


def _value(match: re.Match[str], inherited: Decimal | None) -> Decimal | None:
    if match.group("yi") is not None:
        total = _decimal(match.group("yi"))
        if total is None:
            return None
        total *= _YI
        if match.group("wan") is not None:
            total += (_decimal(match.group("wan")) or Decimal(0)) * Decimal(10) ** 4
        return total.normalize()
    value = _decimal(match.group("num"))
    scale = _SCALES[match.group("scale")] if match.group("scale") else inherited
    return value if value is None or scale is None else (value * scale).normalize()


def _measure(text: str, end: int) -> str | None:
    match = _UNIT.match(text, end)
    return _UNIT_ALIAS.get(match.group(1), match.group(1)) if match else None


def _tokens(text: str) -> list[_Tok]:
    """Every number of ``text`` with its measure word. In ``20至41萬元`` or ``第1至第3季`` the unit
    stated after the last number of a range or list belongs to the others too."""

    matches = list(_TOKEN.finditer(text))
    found: list[_Tok | None] = [None] * len(matches)
    scale: Decimal | None = None
    unit: str | None = None
    for index in range(len(matches) - 1, -1, -1):
        match = matches[index]
        own_scale = _SCALES[match.group("scale")] if match.group("scale") else (_YI if match.group("yi") and not match.group("wan") else None)
        own_unit = _measure(text, match.end())
        joined = index + 1 < len(matches) and _RANGE_JOIN.fullmatch(text, match.end(), matches[index + 1].start()) is not None
        inherited_scale = scale if joined and own_scale is None and own_unit is None else None
        inherited_unit = unit if joined and own_unit is None and own_scale is None else None
        scale, unit = own_scale or inherited_scale, own_unit or inherited_unit
        value = _value(match, inherited_scale)
        if value is None:
            continue
        before = text[max(match.start() - 1, 0):match.start()]
        if before == "-":  # COVID-19: the name's letters, not the dash, decide
            before = text[max(match.start() - 2, 0):match.start() - 1]
        after = text[match.end()] if match.end() < len(text) else ""
        found[index] = _Tok("".join(match.group(0).split()), value, before, after, unit)
    return [token for token in found if token is not None]


def evidence_values(quotes: list[str]) -> set[Pair]:
    """Every (value, unit) the quotes state, including each Western year as its ROC year.

    A scaled amount without a unit (``10.662億``) stands for that amount in any unit."""

    values: set[Pair] = set()
    for quote in quotes:
        for token in _tokens(_normalize(quote)):
            unit = token.unit
            if unit is None and token.shown.endswith(("億", "萬", "千", "兆")):
                unit = ANY_UNIT
            values.add((token.value, unit))
            if token.value > 1911 and token.after == "年":
                values.add((token.value - 1911, "年"))
    return values


def context_calendar_values(texts: list[str]) -> set[Pair]:
    """Years, months, days and quarters stated in the question or confirmed outline."""

    allowed: set[Pair] = set()
    for text in texts:
        for token in _tokens(_normalize(text)):
            if token.after and token.after in _CALENDAR_UNIT:
                allowed.add((token.value, token.unit))
    return allowed


def _compatible(draft: str, evidence: str | None) -> bool:
    if evidence == ANY_UNIT or draft == evidence:
        return True
    if evidence is None:  # a bare figure (table cell, 點值 0.9594) may carry any unit but a date's
        return draft not in _CALENDAR
    if draft == "個":  # a generic counter fits any counted thing, never a date, money or percentage
        return evidence not in _NOT_COUNT
    return evidence == "個" and draft not in _NOT_COUNT


def _supported(token: _Tok, supported: set[Pair]) -> bool:
    if token.unit is None:
        return any(value == token.value for value, _ in supported)
    return any(value == token.value and _compatible(token.unit, unit) for value, unit in supported)


def unsupported_numbers(text: str, supported: set[Pair]) -> list[str]:
    """Numbers of one written paragraph that no cited quote states, in order, without repeats.

    A number with a measure word (家, 個, 元, 年, ...) needs the same value with a compatible one."""

    body = _MARKER.sub("", _normalize(text))
    missing: list[str] = []
    for token in _tokens(body):
        if _supported(token, supported) or token.before.isascii() and token.before.isalpha() or token.after == "題" or token.shown in missing:
            continue
        missing.append(token.shown)
    return missing


def _label(part: str, heading: str, number: int) -> str:
    return f"{'簡答' if part == 'short' else '詳答'}「{heading or '（無標題）'}」第 {number} 段"


def _paragraph_issue(
    spec: dict[str, Any], part: str, place: tuple[int, Any, int, Any], quotes: dict[str, str], allowed: set[Pair],
) -> QaIssue | None:
    section_index, section, number, paragraph = place
    cited = [quotes[cite] for cite in paragraph.cites if cite in quotes]
    missing = unsupported_numbers(paragraph.text, evidence_values(cited) | allowed)
    if not missing:
        return None
    listed = "、".join(missing[:MAX_LISTED])
    return QaIssue(
        question_no=spec["no"], part=part, kind="numeric", section=section_index, paragraph=number - 1,
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
            for section_index, section in enumerate(sections):
                for number, paragraph in enumerate(section.paragraphs, 1):
                    issue = _paragraph_issue(spec, part, (section_index, section, number, paragraph), quotes, allowed)
                    if issue is not None:
                        issues.append(issue)
    return issues
