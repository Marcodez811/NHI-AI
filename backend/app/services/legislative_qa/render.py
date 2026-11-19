"""QaDocument -> .docx, following the real 立院QA sample (A4 landscape, 標楷體, 26pt / 16pt)."""

from __future__ import annotations

import io

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn
from docx.shared import Cm, Emu, Pt
from docx.text.paragraph import Paragraph as DocxParagraph

from app.services.legislative_qa.document import Paragraph, QaDocQuestion, QaDocument, Section

LATIN_FONT = "Times New Roman"
EAST_ASIA_FONT = "標楷體"
SHORT_SIZE = 26
DETAIL_SIZE = 16
TITLE_SIZE = 20
NUMERALS = "零一二三四五六七八九十"


def chinese_number(n: int) -> str:
    if n <= 10:
        return NUMERALS[n]
    if n < 20:
        return "十" + NUMERALS[n - 10]
    tens, ones = divmod(n, 10)
    return NUMERALS[tens] + "十" + (NUMERALS[ones] if ones else "")


def _run(paragraph: DocxParagraph, text: str, size: int, bold: bool = False) -> None:
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.bold = bold
    run.font.name = LATIN_FONT
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:ascii"), LATIN_FONT)
    fonts.set(qn("w:hAnsi"), LATIN_FONT)
    fonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)


def _para(doc, size: int, left: float = 0, hanging: float = 0, after: float = 0) -> DocxParagraph:
    paragraph = doc.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.left_indent = Cm(left)
    fmt.first_line_indent = Cm(-hanging) if hanging else Emu(0)
    fmt.space_after = Pt(after)
    return paragraph


def _setup_page(doc) -> None:
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Cm(29.7), Cm(21.0)
    section.left_margin = section.right_margin = Cm(1.0)
    section.top_margin = section.bottom_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = LATIN_FONT
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), EAST_ASIA_FONT)


def _cite_text(paragraph: Paragraph) -> str:
    return paragraph.text + "".join(f"[{n}]" for n in paragraph.cites)


def _point(doc, paragraph: Paragraph, label: str, size: int, left: float, hanging: float) -> None:
    out = _para(doc, size, left, hanging)
    _run(out, f"{label} ", size)
    _run(out, _cite_text(paragraph), size)


def _section(doc, section: Section, index: int, size: int) -> None:
    wide = size == SHORT_SIZE
    head_left, head_hang = (1.83, 1.83) if wide else (1.0, 1.0)
    item_left, item_hang = (2.7, 1.5) if wide else (1.6, 1.0)
    if section.heading is None:
        for number, paragraph in enumerate(section.paragraphs, 1):
            _point(doc, paragraph, f"{chinese_number(number)}、", size, head_left, head_hang)
        return
    heading = _para(doc, size, head_left, head_hang)
    _run(heading, f"{chinese_number(index)}、{section.heading}：", size, bold=True)
    for number, paragraph in enumerate(section.paragraphs, 1):
        _point(doc, paragraph, f"({chinese_number(number)})", size, item_left, item_hang)


def _block(doc, sections: list[Section], size: int) -> None:
    for index, section in enumerate(sections, 1):
        _section(doc, section, index, size)


def _question(doc, question: QaDocQuestion) -> None:
    head = _para(doc, SHORT_SIZE, after=6)
    _run(head, f"{question.no}. {question.text}", SHORT_SIZE, bold=True)
    _block(doc, question.short, SHORT_SIZE)
    doc.add_page_break()
    again = _para(doc, DETAIL_SIZE, after=6)
    _run(again, question.text, DETAIL_SIZE, bold=True)
    _block(doc, question.detail, DETAIL_SIZE)


def _sources(doc, sources: list[str]) -> None:
    if not sources:
        return
    doc.add_page_break()
    head = _para(doc, DETAIL_SIZE, after=6)
    _run(head, "參考資料", DETAIL_SIZE, bold=True)
    for number, name in enumerate(sources, 1):
        _run(_para(doc, DETAIL_SIZE, 1.0, 1.0), f"[{number}] {name}", DETAIL_SIZE)


def render_docx(document: QaDocument) -> bytes:
    doc = Document()
    _setup_page(doc)
    title = _para(doc, TITLE_SIZE, after=12)
    _run(title, document.title, TITLE_SIZE, bold=True)
    for question in document.questions:
        _question(doc, question)
        if question is not document.questions[-1]:
            doc.add_page_break()
    _sources(doc, document.sources)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
