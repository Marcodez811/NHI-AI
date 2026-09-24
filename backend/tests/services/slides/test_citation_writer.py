"""Backend citation insertion must preserve author layout and make mistakes retryable."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from lxml import etree
from pypdf import PdfReader

from app.services.slides.adapter import SlidesWorkflowAdapter
from app.services.slides.citation_writer import write_citations
from app.services.slides.source_manifest import write_source_manifest
from app.services.slides.validation import _load_pptx_package, _paragraph_texts, validate_candidate_deck
from tests.services.slides.test_validation import _pass_checker, _renderer, _write_deck

def _workspace(root: Path, *, same_names: bool = False) -> Path:
    _write_deck(root, slide_count=4, paragraphs_by_slide={1: ["Cover title"], 2: ["Facts"], 3: ["More facts"], 4: ["參考資料"]}, extra_shapes_by_slide={2: ["{{CITATION}}"], 3: ["{{CITATION}}"], 4: ["{{REFERENCES}}"]})
    work = root / "work"
    work.mkdir(exist_ok=True)
    write_source_manifest(work / "sources.json", ["one.pdf", "two.txt"], ["年度報告.pdf", "年度報告.pdf" if same_names else "政策摘要.txt"])
    (work / "evidence.json").write_text(json.dumps({"blocks": [
        {"id": "a", "document_id": "d1", "citation": {"section_path": ["概況"], "locator_label": "PDF 第 2 頁"}},
        {"id": "b", "document_id": "d1", "citation": {"section_path": ["概況"], "locator_label": "PDF 第 3 頁"}},
        {"id": "c", "document_id": "d2", "citation": {"section_path": [], "locator_label": ""}},
    ], "documents": [{"document_id": "d1", "source": "input/one.pdf"}, {"document_id": "d2", "source": "input/two.txt"}]}, ensure_ascii=False), encoding="utf-8")
    (work / "slide_citations.json").write_text(json.dumps({"slides": {"2": ["a", "b", "c"], "3": ["b"]}}), encoding="utf-8")
    return root / "output" / "presentation.pptx"


def _texts(deck: Path) -> list[list[str]]:
    package = _load_pptx_package(deck)
    return [_paragraph_texts(root) for root in package.slide_roots]


def test_writer_numbers_by_source_and_aggregates_only_cited_locators(tmp_path: Path) -> None:
    deck = _workspace(tmp_path)
    assert write_citations(tmp_path) == []
    texts = _texts(deck)
    assert texts[1][-2:] == ["[1] 資料來源：年度報告.pdf，〈概況〉，PDF 第 2 頁、〈概況〉，PDF 第 3 頁", "[2] 資料來源：政策摘要.txt"]
    assert texts[2][-1] == "[1] 資料來源：年度報告.pdf，〈概況〉，PDF 第 3 頁"
    assert texts[3][-2:] == ["[1] 年度報告.pdf，〈概況〉，PDF 第 2 頁、〈概況〉，PDF 第 3 頁", "[2] 政策摘要.txt"]
    assert not any("{{" in text for slide in texts for text in slide)
    assert not any(name.startswith("ppt/notesSlides/") for name in zipfile.ZipFile(deck).namelist())
    result = validate_candidate_deck(tmp_path, expected_slide_count=3, requested_title="Cover title", require_logo=False, content_checker=_pass_checker, renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50), (40, 50, 60))))
    assert not [finding for finding in result.findings if finding.code.startswith(("citation_", "references_"))]


def test_same_display_name_keeps_distinct_source_numbers(tmp_path: Path) -> None:
    deck = _workspace(tmp_path, same_names=True)
    assert write_citations(tmp_path) == []
    assert "[1] 資料來源：年度報告.pdf" in _texts(deck)[1][-2]
    assert _texts(deck)[1][-1] == "[2] 資料來源：年度報告.pdf"


@pytest.mark.parametrize("mistake", ["missing_json", "invalid_json", "invalid_shape", "unknown_id", "slide_out_of_range", "missing_footer", "missing_references"])
def test_author_mistake_is_retryable_and_does_not_modify_deck(tmp_path: Path, mistake: str) -> None:
    deck = _workspace(tmp_path)
    declaration = tmp_path / "work" / "slide_citations.json"
    if mistake == "missing_json":
        declaration.unlink()
    elif mistake == "invalid_json":
        declaration.write_text("{", encoding="utf-8")
    elif mistake == "invalid_shape":
        declaration.write_text('{"slides":{"2":"a"}}', encoding="utf-8")
    elif mistake == "unknown_id":
        declaration.write_text('{"slides":{"2":["unknown"]}}', encoding="utf-8")
    elif mistake == "slide_out_of_range":
        declaration.write_text('{"slides":{"4":["a"]}}', encoding="utf-8")
    elif mistake in {"missing_footer", "missing_references"}:
        member = "ppt/slides/slide2.xml" if mistake == "missing_footer" else "ppt/slides/slide4.xml"
        with zipfile.ZipFile(deck) as archive:
            parts = {item.filename: (item, archive.read(item.filename)) for item in archive.infolist()}
        info, xml = parts[member]
        parts[member] = (info, xml.replace(b"{{CITATION}}" if mistake == "missing_footer" else b"{{REFERENCES}}", b"not a placeholder"))
        with zipfile.ZipFile(deck, "w") as archive:
            for info, content in parts.values():
                archive.writestr(info, content)
    before = deck.read_bytes()
    adapter = SlidesWorkflowAdapter()
    with patch("app.services.slides.adapter.clean_candidate_deck", return_value=[]):
        adapter.post_author_completion_check(None, None, tmp_path)
    assert deck.read_bytes() == before
    result = validate_candidate_deck(tmp_path, require_logo=False, content_checker=_pass_checker, renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50), (40, 50, 60))))
    assert any(finding.origin == "candidate" and finding.code.startswith("citation_") for finding in result.findings)


def test_stale_placeholder_alone_is_candidate_finding(tmp_path: Path) -> None:
    _workspace(tmp_path)
    result = validate_candidate_deck(tmp_path, require_logo=False, content_checker=_pass_checker, renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50), (40, 50, 60))))
    assert [finding.slide_number for finding in result.findings if finding.code == "citation_placeholder_remaining"] == [2, 3, 4]


def test_unreadable_author_package_does_not_escape_completion_check(tmp_path: Path) -> None:
    _workspace(tmp_path)
    deck = tmp_path / "output" / "presentation.pptx"
    deck.write_bytes(b"not a PPTX")
    SlidesWorkflowAdapter().post_author_completion_check(None, None, tmp_path)
    report = json.loads((tmp_path / "work" / "intermediate" / "citation_writer.json").read_text(encoding="utf-8"))
    assert report["problems"][0]["code"] == "citation_candidate_package_invalid"
    result = validate_candidate_deck(tmp_path, require_logo=False, content_checker=_pass_checker)
    assert any(finding.code == "citation_candidate_package_invalid" and finding.origin == "candidate" for finding in result.findings)


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice required")
def test_writer_output_loads_in_libreoffice(tmp_path: Path) -> None:
    script = """const pptxgen=require('pptxgenjs'); const p=new pptxgen();
    for(const [title, token] of [['Cover title',null],['Facts','{{CITATION}}'],['More facts','{{CITATION}}'],['參考資料','{{REFERENCES}}']]) {const s=p.addSlide();s.addText(title,{x:1,y:1,w:8,h:1,fontSize:28}); if(token)s.addText(token,{x:1,y:5,w:8,h:1,fontSize:14,color:'666666'});}p.writeFile({fileName:process.argv[1]});"""
    deck = _workspace(tmp_path)
    subprocess.run(["node", "-e", script, str(deck)], cwd=Path(__file__).resolve().parents[3], check=True, capture_output=True, text=True)
    drawing = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    with zipfile.ZipFile(deck) as archive:
        original = etree.fromstring(archive.read("ppt/slides/slide2.xml"))
    placeholder = next(paragraph for paragraph in original.iter(f"{drawing}p") if "{{CITATION}}" in "".join(paragraph.itertext()))
    original_run_style = etree.tostring(placeholder.find(f"{drawing}r/{drawing}rPr"), method="c14n")
    original_paragraph_style = placeholder.find(f"{drawing}pPr")
    original_paragraph_style_bytes = etree.tostring(original_paragraph_style, method="c14n") if original_paragraph_style is not None else None
    assert write_citations(tmp_path) == []
    with zipfile.ZipFile(deck) as archive:
        written = etree.fromstring(archive.read("ppt/slides/slide2.xml"))
    paragraphs = [paragraph for paragraph in written.iter(f"{drawing}p") if "資料來源：" in "".join(paragraph.itertext())]
    assert len(paragraphs) == 2
    for paragraph in paragraphs:
        assert etree.tostring(paragraph.find(f"{drawing}r/{drawing}rPr"), method="c14n") == original_run_style
        properties = paragraph.find(f"{drawing}pPr")
        assert (etree.tostring(properties, method="c14n") if properties is not None else None) == original_paragraph_style_bytes
    converted = tmp_path / "converted"
    converted.mkdir()
    completed = subprocess.run(["soffice", f"-env:UserInstallation={ (tmp_path / 'office-profile').as_uri() }", "--headless", "--convert-to", "pdf", "--outdir", str(converted), str(deck)], capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    pdf = converted / "presentation.pdf"
    assert pdf.is_file(), completed.stdout + completed.stderr
    assert len(PdfReader(pdf).pages) == 4
