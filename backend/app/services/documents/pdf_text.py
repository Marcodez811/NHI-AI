"""Table-aware PDF text extraction.

Ruled tables are rendered as Markdown tables (merged header cells forward-filled
and multi-row headers combined) so a model can tell which column a number
belongs to. Per-page failures fall back to pypdf text.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_NUMERIC = re.compile(r"^[\s\d.,%\-+()/$NA]*$")


def _clean(cell: object) -> str:
    text = " ".join(str(cell or "").split())
    return text.replace("|", "\\|")


def _is_numeric(cell: str) -> bool:
    return bool(cell) and bool(_NUMERIC.match(cell)) and any(ch.isdigit() for ch in cell)


def _is_header_row(row: list[str]) -> bool:
    filled = [c for c in row if c]
    if not filled:
        return False
    return sum(_is_numeric(c) for c in filled) / len(filled) < 0.3


def _build_header(rows: list[list[str]]) -> tuple[list[str], int]:
    """Return (column names, number of header rows consumed)."""

    count = 0
    while count < min(2, len(rows) - 1) and _is_header_row(rows[count]):
        count += 1
    if count == 0:
        return [], 0
    levels: list[list[str]] = []
    for row in rows[:count]:
        filled: list[str] = []
        last = ""
        has_gap = any(not c for c in row) and any(row)
        for cell in row:
            if cell:
                last = cell
            filled.append(cell or (last if has_gap and count > 1 else ""))
        levels.append(filled)
    names: list[str] = []
    for col in range(len(rows[0])):
        parts: list[str] = []
        for level in levels:
            value = level[col]
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        names.append(" ".join(parts))
    return names, count


def _markdown(header: list[str], body: list[list[str]], width: int) -> str:
    header = (header + [""] * width)[:width]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in body:
        row = (row + [""] * width)[:width]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_table(raw: list[list[object]], carry_header: list[str] | None) -> tuple[str, list[str] | None]:
    """Render one table; returns (markdown, header used for continuation)."""

    rows = [[_clean(c) for c in row] for row in raw if row and any(_clean(c) for c in row)]
    if not rows:
        return "", carry_header
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, used = _build_header(rows)
    if used:
        body = rows[used:]
    elif carry_header and len(carry_header) == width:
        header, body = carry_header, rows
    else:
        header, body = [f"欄{i + 1}" for i in range(width)], rows
        return _markdown(header, body, width), None
    return _markdown(header, body, width), header


def _pypdf_page_text(path: Path, index: int) -> str:
    from pypdf import PdfReader

    try:
        return (PdfReader(str(path)).pages[index].extract_text() or "").strip()
    except Exception:
        return ""


def _inside(obj: dict, bboxes: list[tuple[float, float, float, float]]) -> bool:
    cx = (obj["x0"] + obj["x1"]) / 2
    cy = (obj["top"] + obj["bottom"]) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in bboxes)


def _page_markdown(page, carry: list[str] | None) -> tuple[str, list[str] | None]:
    tables = page.find_tables()
    tables = [t for t in tables if t.bbox]
    if not tables:
        return (page.extract_text() or "").strip(), None
    bboxes = [t.bbox for t in tables]
    others = page.filter(lambda o: o.get("object_type") != "char" or not _inside(o, bboxes))
    items: list[tuple[float, str, bool]] = [
        (ln["top"], ln["text"], False) for ln in others.extract_text_lines()
    ]
    use_carry, carry = carry, None
    for table in sorted(tables, key=lambda t: t.bbox[1]):
        md, carry = render_table(table.extract(), use_carry)
        use_carry = None
        if md:
            items.append((table.bbox[1], md, True))
    items.sort(key=lambda i: i[0])
    blocks: list[str] = []
    run: list[str] = []
    for _top, text, is_table in items:
        if is_table:
            if run:
                blocks.append("\n".join(run))
                run = []
            blocks.append(text)
        else:
            run.append(text)
    if run:
        blocks.append("\n".join(run))
    return "\n\n".join(blocks), carry


def extract_pdf_text(path: Path | str) -> str:
    """Extract a PDF as Markdown-ish text with page markers; "" if no text."""

    import pdfplumber

    path = Path(path)
    out: list[str] = []
    carry: list[str] | None = None
    try:
        pdf = pdfplumber.open(str(path))
    except Exception:
        logger.warning("pdfplumber could not open %s", path.name, exc_info=True)
        return _fallback_all(path)
    with pdf:
        for index, page in enumerate(pdf.pages):
            try:
                body, carry = _page_markdown(page, carry)
            except Exception:
                logger.warning("pdfplumber failed on page %d of %s", index + 1, path.name, exc_info=True)
                body, carry = _pypdf_page_text(path, index), None
            finally:
                page.close() if hasattr(page, "close") else None
            if body.strip():
                out.append(f"— 第 {index + 1} 頁 —\n{body.strip()}")
    return "\n\n".join(out)


def _fallback_all(path: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return ""
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except Exception:
        return ""
    return "\n\n".join(f"— 第 {i + 1} 頁 —\n{t}" for i, t in enumerate(pages) if t)
