from __future__ import annotations

import io
from uuid import uuid4

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn

from app.models.chat import ChatQaWorkspace
from app.services.chat.skills.legislative_qa import QaError
from app.services.legislative_qa.document import build_document
from app.services.legislative_qa.render import render_docx

A = "【113總額分配表.pdf】"
B = "【乙資料.docx】"


def _workspace(confirmed: bool = True) -> ChatQaWorkspace:
    return ChatQaWorkspace(
        session_id=uuid4(),
        questions=[{"no": 1, "text": "甲題"}, {"no": 2, "text": "乙題"}],
        outline={
            "1": {
                "short": [f"點值達 1.0{A}", f"第二點{A}{B}", "無來源"],
                "detail": [
                    {"title": "背景說明", "points": [f"背景一{B}"]},
                    {"title": "目前辦理情形", "points": ["辦理一", "辦理二"]},
                ],
                "notes": ["待補：這是備註"],
                "confirmed": confirmed,
            },
            "2": {"short": ["乙簡答"], "detail": [{"title": "背景說明", "points": []}], "confirmed": True},
        },
    )


def test_builder_strips_tags_into_deduplicated_cites():
    doc = build_document(_workspace(), "11-5")
    assert doc.title == "11-5 立法院QA（2 題）"
    assert doc.sources == ["113總額分配表.pdf", "乙資料.docx"]
    short = doc.questions[0].short[0]
    assert short.heading is None
    assert [(p.text, p.cites) for p in short.paragraphs] == [
        ("點值達 1.0", [1]), ("第二點", [1, 2]), ("無來源", []),
    ]
    detail = doc.questions[0].detail
    assert [s.heading for s in detail] == ["背景說明", "目前辦理情形"]
    assert detail[0].paragraphs[0].cites == [2]


def test_builder_excludes_notes_and_handles_missing_label():
    doc = build_document(_workspace())
    assert doc.title == "立法院QA（2 題）" and doc.session_label is None
    assert "備註" not in doc.model_dump_json()


def test_builder_refuses_unconfirmed_outlines():
    with pytest.raises(QaError) as error:
        build_document(_workspace(confirmed=False))
    assert error.value.code == "qa_outline_unconfirmed" and "1" in error.value.message


def _open(doc_model):
    return Document(io.BytesIO(render_docx(doc_model)))


def test_renderer_page_fonts_sizes_and_citations():
    docx = _open(build_document(_workspace(), "11-5"))
    section = docx.sections[0]
    assert section.orientation == WD_ORIENT.LANDSCAPE
    assert round(section.page_width.cm, 1) == 29.7 and round(section.page_height.cm, 1) == 21.0
    paragraphs = docx.paragraphs
    texts = [p.text for p in paragraphs]
    assert texts[0] == "11-5 立法院QA（2 題）"
    assert "1. 甲題" in texts
    assert any(t.startswith("一、 點值達 1.0[1]") for t in texts)
    assert any("第二點[1][2]" in t for t in texts)
    assert any(t.startswith("一、背景說明：") for t in texts)
    assert any(t.startswith("(一) 辦理一") for t in texts)
    assert texts[-3:] == ["參考資料", "[1] 113總額分配表.pdf", "[2] 乙資料.docx"]
    sizes = {p.text: p.runs[-1].font.size.pt for p in paragraphs if p.runs and p.text}
    assert sizes["1. 甲題"] == 26 and sizes["一、 點值達 1.0[1]"] == 26
    assert sizes["一、背景說明："] == 16 and sizes["(一) 辦理一"] == 16
    heading = next(p for p in paragraphs if p.text == "一、背景說明：")
    assert heading.runs[0].bold
    fonts = heading.runs[0]._element.rPr.rFonts
    assert fonts.get(qn("w:eastAsia")) == "標楷體" and fonts.get(qn("w:ascii")) == "Times New Roman"
    assert all(p.style.name == "Normal" for p in paragraphs) and not docx.tables
    assert "待補" not in "".join(texts)


def test_renderer_without_sources_has_no_reference_list():
    workspace = _workspace()
    workspace.outline["1"]["short"] = ["只有文字"]
    workspace.outline["1"]["detail"] = [{"title": "背景說明", "points": ["x"]}]
    texts = [p.text for p in _open(build_document(workspace)).paragraphs]
    assert "參考資料" not in texts
