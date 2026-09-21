from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import app.services.slides.validation as validation_module
from app.services.slides.validation import (
    ValidationStatus,
    _canonicalize_pdftoppm_output,
    _chart_title_findings,
    validate_candidate_deck,
)
from app.services.slides.source_manifest import write_source_manifest


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (32, 18), color)
    image.putpixel((0, 0), (255, 255, 255))
    image.save(output, format="PNG")
    return output.getvalue()


def _write_deck(
    root: Path,
    slide_count: int = 2,
    *,
    paragraphs_by_slide: dict[int, list[str]] | None = None,
    include_notes: bool = False,
    title_placeholder_by_slide: set[int] | None = None,
) -> Path:
    deck = root / "output" / "presentation.pptx"
    deck.parent.mkdir(parents=True)
    parts = {
        "[Content_Types].xml": f'''<?xml version="1.0"?><Types xmlns="{CT_NS}">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
          {''.join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1, slide_count + 1))}
          {''.join(f'<Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>' for i in range(1, slide_count + 1)) if include_notes else ''}
        </Types>''',
        "ppt/presentation.xml": f'''<p:presentation xmlns:p="{P_NS}" xmlns:r="{R_NS}">
          <p:sldIdLst>{''.join(f'<p:sldId id="{i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))}</p:sldIdLst>
        </p:presentation>''',
        "ppt/_rels/presentation.xml.rels": f'''<Relationships xmlns="{REL_NS}">
          {''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>' for i in range(1, slide_count + 1))}
        </Relationships>''',
    }
    for index in range(1, slide_count + 1):
        paragraphs = (paragraphs_by_slide or {}).get(
            index,
            [
                "Cover title"
                if index == 1
                else "參考資料"
                if index == slide_count
                else f"Slide {index}"
            ],
        )
        text_xml = "".join(
            f"<a:p><a:r><a:rPr/><a:t>{text}</a:t></a:r></a:p>"
            for text in paragraphs
        )
        title_shape_xml = ""
        if index in (title_placeholder_by_slide or set()):
            title_shape_xml = (
                f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/>'
                f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{paragraphs[0]}</a:t></a:r></a:p></p:txBody></p:sp>'
            )
            text_xml = "".join(
                f"<a:p><a:r><a:rPr/><a:t>{text}</a:t></a:r></a:p>"
                for text in paragraphs[1:]
            )
        parts[f"ppt/slides/slide{index}.xml"] = f'''<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">
          <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/>{title_shape_xml}<p:sp><p:nvSpPr/><p:spPr/>
            <p:txBody><a:bodyPr/><a:lstStyle/>{text_xml}</p:txBody>
          </p:sp></p:spTree></p:cSld>
        </p:sld>'''
        if include_notes:
            parts[f"ppt/slides/_rels/slide{index}.xml.rels"] = f'''<Relationships xmlns="{REL_NS}">
              <Relationship Id="rIdNotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{index}.xml"/>
            </Relationships>'''
            parts[f"ppt/notesSlides/notesSlide{index}.xml"] = f'''<p:notes xmlns:p="{P_NS}" xmlns:a="{A_NS}">
              <p:notesText><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{"cover" if index == 1 else "body"}</a:t></a:r></a:p></p:txBody></p:notesText>
            </p:notes>'''
    with zipfile.ZipFile(deck, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return deck


def _renderer(colors: tuple[tuple[int, int, int], ...] = ((10, 20, 30), (20, 30, 40))):
    def render(deck: Path, output_dir: Path) -> None:
        del deck
        output_dir.mkdir(parents=True, exist_ok=True)
        for number, color in enumerate(colors, start=1):
            (output_dir / f"slide-{number}.png").write_bytes(_png_bytes(color))

    return render


def _pass_checker(path: Path) -> dict[str, object]:
    return {
        "format_version": "trusted-test-checker",
        "presentation": {"path": str(path), "sha256": "test", "slide_count": 2},
        "status": "pass",
        "findings": [],
    }


def _write_outline(root: Path, *, nodes: list[dict[str, object]], total_slides: int) -> Path:
    outline_path = root / "work" / "outline.json"
    outline_path.parent.mkdir(parents=True, exist_ok=True)
    outline_path.write_text(
        json.dumps(
            {
                "title": "Cover title",
                "narrative": "A through-line covering the approved sections.",
                "nodes": nodes,
                "total_slides": total_slides,
            }
        ),
        encoding="utf-8",
    )
    return outline_path


def _outline_node(node_id: str, heading: str) -> dict[str, object]:
    return {
        "id": node_id,
        "heading": heading,
        "intent": f"Cover {heading} with grounded evidence.",
        "key_points": ["first point", "second point"],
        "evidence_refs": [],
        "emphasis": "normal",
        "approx_slides": 1,
    }


def _write_outline_mapping(root: Path, nodes: list[tuple[str, int, int]]) -> Path:
    mapping_path = root / "work" / "outline_mapping.json"
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {"node_id": node_id, "slide_start": slide_start, "slide_end": slide_end}
                    for node_id, slide_start, slide_end in nodes
                ]
            }
        ),
        encoding="utf-8",
    )
    return mapping_path


def _validate(root: Path, **kwargs: object):
    sources_path = root / "work" / "sources.json"
    if "sources_path" not in kwargs and not sources_path.exists():
        write_source_manifest(sources_path, ["source.pdf"], ["年度報告.pdf"])
    options: dict[str, object] = {
        "expected_slide_count": 1,
        "requested_title": "Cover title",
        "content_checker": _pass_checker,
        "require_logo": False,
        "renderer": _renderer(),
    }
    options.update(kwargs)
    return validate_candidate_deck(root, **options)


def test_chart_title_findings_rejects_an_untitled_native_chart() -> None:
    findings = _chart_title_findings(
        {
            "slides": [
                {
                    "slide_number": 3,
                    "charts": [
                        {"part": "ppt/charts/chart1.xml", "title": None},
                        {"part": "ppt/charts/chart2.xml", "title": "給付件數年度趨勢"},
                    ],
                }
            ]
        }
    )

    assert [finding.code for finding in findings] == ["chart_title_missing"]
    assert findings[0].slide_number == 3
    assert findings[0].details == {
        "chart_index": 1,
        "part": "ppt/charts/chart1.xml",
    }


def test_chart_title_findings_accepts_non_empty_native_chart_titles() -> None:
    assert not _chart_title_findings(
        {
            "slides": [
                {
                    "slide_number": 2,
                    "charts": [
                        {
                            "part": "ppt/charts/chart1.xml",
                            "title": "各年度門診申報件數",
                        }
                    ],
                }
            ]
        }
    )


def test_backend_renders_replace_stale_author_previews_and_bind_snapshot(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    preview = tmp_path / "work" / "rendered" / "preview"
    preview.mkdir(parents=True)
    (preview / "slide-1.png").write_bytes(_png_bytes((100, 100, 100)))
    (preview / "slide-2.png").write_bytes(_png_bytes((100, 100, 100)))

    result = _validate(tmp_path)

    final = tmp_path / "work" / "rendered" / "final"
    assert result.status is ValidationStatus.PASS
    assert [path.name for path in sorted(final.iterdir())] == ["slide-1.png", "slide-2.png"]
    assert (final / "slide-1.png").read_bytes() != (preview / "slide-1.png").read_bytes()
    assert result.deck_snapshot["artifact_binding"]["pptx_sha256"] == result.pptx_sha256
    assert result.deck_snapshot["trusted_renders"] == []


def test_requested_count_excludes_the_final_references_slide(tmp_path: Path) -> None:
    _write_deck(tmp_path)

    result = _validate(tmp_path, expected_slide_count=2)

    finding = next(finding for finding in result.findings if finding.code == "slide_count")
    assert finding.details == {
        "expected_content_slides": 2,
        "expected_total_slides": 3,
        "actual_total_slides": 2,
    }


def test_speaker_notes_parts_are_a_candidate_finding(tmp_path: Path) -> None:
    deck = _write_deck(tmp_path, include_notes=True)

    result = _validate(tmp_path)

    finding = next(
        finding for finding in result.findings if finding.code == "speaker_notes_present"
    )
    assert finding.origin == "candidate"
    assert finding.details["parts"] == [
        "ppt/notesSlides/notesSlide1.xml",
        "ppt/notesSlides/notesSlide2.xml",
    ]
    snapshot = validation_module.build_deck_snapshot(deck)
    assert snapshot["slides"][0]["notes"] == ["cover"]


def test_failed_validation_clears_old_final_renders(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    final = tmp_path / "work" / "rendered" / "final"
    final.mkdir(parents=True)
    (final / "slide-1.png").write_bytes(_png_bytes((1, 2, 3)))

    def failing_checker(path: Path) -> dict[str, object]:
        report = _pass_checker(path)
        report["status"] = "fail"
        report["findings"] = [{"type": "placeholder_todo"}]
        return report

    result = _validate(tmp_path, content_checker=failing_checker)

    assert result.status is ValidationStatus.FAIL
    assert not final.exists()
    assert any(finding.code == "content_check_failed" for finding in result.findings)


def test_renderer_failure_is_infrastructure_failure(tmp_path: Path) -> None:
    _write_deck(tmp_path)

    def broken_renderer(deck: Path, output_dir: Path) -> None:
        del deck, output_dir
        raise RuntimeError("LibreOffice unavailable")

    result = _validate(tmp_path, renderer=broken_renderer)

    finding = next(finding for finding in result.findings if finding.code == "renderer_error")
    assert result.status is ValidationStatus.FAIL
    assert finding.origin == "infrastructure"


def test_renderer_output_outside_temporary_root_is_rejected(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    outside = tmp_path / "outside"

    def escaping_renderer(deck: Path, output_dir: Path) -> Path:
        del deck, output_dir
        outside.mkdir()
        (outside / "slide-1.png").write_bytes(_png_bytes((10, 20, 30)))
        (outside / "slide-2.png").write_bytes(_png_bytes((20, 30, 40)))
        return outside

    result = _validate(tmp_path, renderer=escaping_renderer)

    assert result.status is ValidationStatus.FAIL
    assert any(finding.code == "renderer_error" for finding in result.findings)
    assert not (tmp_path / "work" / "rendered" / "final").exists()
    assert not list((tmp_path / "work" / "rendered").glob(".backend_render_*"))


def test_renderer_returning_a_file_is_rejected(tmp_path: Path) -> None:
    _write_deck(tmp_path)

    def file_renderer(deck: Path, output_dir: Path) -> Path:
        del deck
        marker = output_dir / "not-a-render-directory"
        marker.write_text("wrong return type", encoding="utf-8")
        return marker

    result = _validate(tmp_path, renderer=file_renderer)

    assert result.status is ValidationStatus.FAIL
    assert any(finding.code == "renderer_error" for finding in result.findings)
    assert not (tmp_path / "work" / "rendered" / "final").exists()


def test_artifact_write_failure_clears_final_renders_and_temporary_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_deck(tmp_path)
    original_write = validation_module._write_json_atomic

    def fail_content_write(path: Path, payload: object) -> None:
        if path.name == "content_check.json":
            raise OSError("disk full")
        original_write(path, payload)

    monkeypatch.setattr(validation_module, "_write_json_atomic", fail_content_write)

    with pytest.raises(OSError, match="disk full"):
        _validate(tmp_path)

    rendered = tmp_path / "work" / "rendered"
    assert not (rendered / "final").exists()
    assert not list(rendered.glob(".backend_render_*"))
    assert not (tmp_path / "work" / "intermediate" / "content_check.json").exists()
    assert not (tmp_path / "work" / "intermediate" / "deck_snapshot.json").exists()


def test_unexpected_validation_error_removes_the_entire_temporary_render_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_deck(tmp_path)
    final = tmp_path / "work" / "rendered" / "final"
    final.mkdir(parents=True)
    (final / "slide-1.png").write_bytes(_png_bytes((1, 2, 3)))

    def fail_logo_check(*_: object) -> tuple[list[object], list[str]]:
        raise RuntimeError("logo inspection crashed")

    monkeypatch.setattr(validation_module, "_logo_image_parts", fail_logo_check)

    with pytest.raises(RuntimeError, match="logo inspection crashed"):
        _validate(tmp_path, require_logo=True)

    rendered = tmp_path / "work" / "rendered"
    assert not final.exists()
    assert not list(rendered.glob(".backend_render_*"))


def test_partial_final_render_staging_is_removed_when_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_deck(tmp_path)

    def fail_copy(source: Path, destination: Path, **_: object) -> None:
        destination.mkdir()
        raise OSError("disk full")

    monkeypatch.setattr(validation_module.shutil, "copytree", fail_copy)

    result = _validate(tmp_path)

    rendered = tmp_path / "work" / "rendered"
    assert result.status is ValidationStatus.FAIL
    assert any(finding.code == "final_render_publish_error" for finding in result.findings)
    assert not (rendered / "final").exists()
    assert not list(rendered.glob(".final.*"))
    assert not list(rendered.glob(".backend_render_*"))


def test_malformed_backend_render_output_is_an_infrastructure_failure(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    result = _validate(tmp_path, renderer=_renderer(((10, 20, 30),)))

    finding = next(finding for finding in result.findings if finding.code == "backend_render_missing")
    assert result.status is ValidationStatus.FAIL
    assert finding.origin == "infrastructure"


def test_generated_identical_slides_remain_candidate_failures(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    result = _validate(tmp_path, renderer=_renderer(((10, 20, 30), (10, 20, 30))))

    assert result.status is ValidationStatus.FAIL
    assert any(finding.code == "render_all_identical" for finding in result.findings)
    assert not (tmp_path / "work" / "rendered" / "final").exists()


def test_content_finding_is_forwarded_with_candidate_origin(tmp_path: Path) -> None:
    _write_deck(tmp_path)

    def checker(path: Path) -> dict[str, object]:
        return {
            "format_version": "trusted-test-checker",
            "presentation": {"path": str(path), "sha256": "test", "slide_count": 2},
            "status": "fail",
            "findings": [{"type": "placeholder_todo", "slide_number": 2, "match": "TODO"}],
        }

    result = _validate(tmp_path, content_checker=checker)

    finding = next(finding for finding in result.findings if finding.code == "content_check_failed")
    assert finding.origin == "candidate"
    assert finding.details["content_findings"] == [{"type": "placeholder_todo", "slide_number": 2, "match": "TODO"}]


def test_pdftoppm_canonicalization_rejects_padded_name_collisions(tmp_path: Path) -> None:
    source = tmp_path / "pdftoppm"
    destination = tmp_path / "canonical"
    source.mkdir()
    (source / "slide-1.png").write_bytes(_png_bytes((40, 100, 150)))
    (source / "slide-01.png").write_bytes(_png_bytes((80, 100, 150)))

    try:
        _canonicalize_pdftoppm_output(source, destination)
    except RuntimeError as exc:
        assert "colliding numeric slide IDs" in str(exc)
    else:
        raise AssertionError("padded names must collide")
    assert not destination.exists()


def test_no_outline_file_leaves_validation_unaffected(tmp_path: Path) -> None:
    """A non-planning job never writes ``work/outline.json``; behavior must be unchanged."""

    _write_deck(tmp_path)
    result = _validate(tmp_path)

    assert result.status is ValidationStatus.PASS
    assert not any(finding.code.startswith("outline_") for finding in result.findings)


def test_citation_source_names_accept_only_catalog_display_names(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: [
                "Cover title",
                "本文提到 report__2.pdf，但不是引用。",
                "[2] 普通編號內容，不是來源清單。",
            ],
            2: [
                "參考資料",
                "[1] 2025，年度報告.pdf，〈財務〉，PDF 第 12 頁",
                "[1] 資料來源：2025，年度報告.pdf，PDF 第 12 頁",
            ],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report__2.pdf"],
        ["2025，年度報告.pdf"],
    )

    result = _validate(tmp_path)

    source_findings = [
        finding for finding in result.findings if finding.code == "citation_source_name_invalid"
    ]
    assert source_findings == []


def test_citation_source_name_rejects_unknown_and_prefix_names(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: [
                "Slide 2",
                "[1] 資料來源：年度報告.pdf.bak，PDF 第 3 頁",
                "[2] 資料來源：內部摘要.pdf，PDF 第 8 頁",
            ],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report.pdf"],
        ["年度報告.pdf"],
    )

    result = _validate(tmp_path)

    findings = [
        finding for finding in result.findings if finding.code == "citation_source_name_invalid"
    ]
    assert [finding.slide_number for finding in findings] == [2, 2]
    assert all(finding.origin == "candidate" for finding in findings)


def test_citation_rejects_internal_tokens_after_an_allowed_source_name(tmp_path: Path) -> None:
    digest = "a" * 64
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: [
                "參考資料",
                "[1] 年度報告.pdf，report__2.pdf",
                "[2] 年度報告.pdf，EvidenceStore，work/evidence.json",
                f"[3] 年度報告.pdf，{digest}",
            ],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report__2.pdf"],
        ["年度報告.pdf"],
    )

    result = _validate(tmp_path)

    findings = [
        finding for finding in result.findings if finding.code == "citation_abstraction_leak"
    ]
    assert [finding.slide_number for finding in findings] == [2, 2, 2]
    assert findings[0].details["leaks"] == ["report__2.pdf"]
    assert findings[1].details["leaks"] == ["EvidenceStore", "work/evidence.json"]
    assert findings[2].details["leaks"] == ["sha256"]


def test_citation_does_not_treat_a_filename_inside_its_display_name_as_leakage(
    tmp_path: Path,
) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title", "[1] 資料來源：Annual report.pdf"],
            2: ["參考資料", "[1] Annual report.pdf"],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report.pdf"],
        ["Annual report.pdf"],
    )

    result = _validate(tmp_path, requested_title=None)

    assert result.status is ValidationStatus.PASS


def test_references_slide_matches_unique_numbered_content_sources(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        slide_count=3,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: [
                "Policy result",
                "[1] 資料來源：年度報告.pdf，PDF 第 12 頁",
                "[2] 資料來源：政策說明.docx，〈給付範圍〉",
            ],
            3: [
                "參考資料",
                "[1] 年度報告.pdf，〈財務〉，PDF 第 12 頁",
                "[2] 政策說明.docx，〈給付範圍〉",
            ],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report.pdf", "policy.docx"],
        ["年度報告.pdf", "政策說明.docx"],
    )

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    assert result.status is ValidationStatus.PASS


def test_references_slide_requires_exact_last_title(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title", "[1] 資料來源：年度報告.pdf"],
            2: ["參考文獻", "[1] 年度報告.pdf"],
        },
    )

    result = _validate(tmp_path)

    finding = next(
        finding for finding in result.findings if finding.code == "references_slide_missing"
    )
    assert finding.slide_number == 2
    assert finding.details["title"] == "參考資料"


def test_references_slide_rejects_body_title_spoofing_a_title_placeholder(
    tmp_path: Path,
) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: ["附錄", "參考資料", "[1] 年度報告.pdf"],
        },
        title_placeholder_by_slide={2},
    )

    result = _validate(tmp_path)

    finding = next(
        finding for finding in result.findings if finding.code == "references_slide_missing"
    )
    assert finding.details["actual"] == "附錄"


def test_references_slide_rejects_each_unnumbered_or_malformed_paragraph(
    tmp_path: Path,
) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title", "[1] 資料來源：年度報告.pdf"],
            2: [
                "參考資料",
                "[1] 年度報告.pdf",
                "年度報告.pdf，PDF 第 12 頁",
                "[2]資料來源：年度報告.pdf",
                "[3] 年度報告.pdf，[4] 政策說明.docx",
            ],
        },
    )

    result = _validate(tmp_path)

    invalid = [
        finding.details["paragraph"]
        for finding in result.findings
        if finding.code == "references_entry_invalid"
    ]
    assert invalid == [
        "年度報告.pdf，PDF 第 12 頁",
        "[2]資料來源：年度報告.pdf",
        "[3] 年度報告.pdf，[4] 政策說明.docx",
    ]


@pytest.mark.parametrize(
    "artifact",
    [
        "work/sources.json",
        "work/outline_mapping.json",
        "trace.json",
        "ppt/charts/chart1.xml",
        "ppt/slides/slide2.xml",
        "word/document.xml",
    ],
)
def test_citation_rejects_additional_internal_artifact_names(
    tmp_path: Path,
    artifact: str,
) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title", "[1] 資料來源：年度報告.pdf"],
            2: ["參考資料", f"[1] 年度報告.pdf，{artifact}"],
        },
    )

    result = _validate(tmp_path)

    leak = next(
        finding for finding in result.findings if finding.code == "citation_abstraction_leak"
    )
    assert artifact in leak.details["leaks"]


def test_references_slide_rejects_duplicate_numbers_sources_and_mismatch(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        slide_count=3,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: [
                "Policy result",
                "[1] 資料來源：年度報告.pdf，PDF 第 12 頁",
                "[2] 資料來源：政策說明.docx，〈給付範圍〉",
            ],
            3: [
                "參考資料",
                "[1] 年度報告.pdf，PDF 第 12 頁",
                "[1] 年度報告.pdf，〈財務〉",
            ],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report.pdf", "policy.docx"],
        ["年度報告.pdf", "政策說明.docx"],
    )

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    finding_codes = {finding.code for finding in result.findings}
    assert "references_number_duplicate" in finding_codes
    assert "references_source_duplicate" in finding_codes
    mismatch = next(
        finding for finding in result.findings if finding.code == "references_source_mismatch"
    )
    assert mismatch.details == {
        "missing_sources": ["政策說明.docx"],
        "unused_sources": [],
    }


def test_content_footers_reuse_reference_number_for_a_repeated_source(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        slide_count=4,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: ["Finding A", "[1] 資料來源：年度報告.pdf，PDF 第 12 頁"],
            3: ["Finding B", "[1] 資料來源：年度報告.pdf，〈財務〉"],
            4: ["參考資料", "[1] 年度報告.pdf，〈財務〉，PDF 第 12 頁"],
        },
    )

    result = _validate(
        tmp_path,
        expected_slide_count=3,
        renderer=_renderer(
            ((10, 20, 30), (20, 30, 40), (30, 40, 50), (40, 50, 60))
        ),
    )

    assert result.status is ValidationStatus.PASS


def test_content_footer_requires_a_numbered_marker(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={
            1: ["Cover title", "資料來源：年度報告.pdf，PDF 第 12 頁"],
            2: ["參考資料", "[1] 年度報告.pdf，PDF 第 12 頁"],
        },
    )

    result = _validate(tmp_path)

    finding = next(
        finding
        for finding in result.findings
        if finding.code == "citation_footer_number_missing"
    )
    assert finding.slide_number == 1
    assert finding.origin == "candidate"


def test_content_footer_markers_must_map_one_to_one_with_references(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        slide_count=4,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: [
                "Finding A",
                "[1] 資料來源：年度報告.pdf，PDF 第 12 頁",
                "[1] 資料來源：政策說明.docx，〈給付範圍〉",
            ],
            3: ["Finding B", "[2] 資料來源：年度報告.pdf，〈財務〉"],
            4: [
                "參考資料",
                "[1] 年度報告.pdf，PDF 第 12 頁",
                "[2] 政策說明.docx，〈給付範圍〉",
            ],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report.pdf", "policy.docx"],
        ["年度報告.pdf", "政策說明.docx"],
    )

    result = _validate(
        tmp_path,
        expected_slide_count=3,
        renderer=_renderer(
            ((10, 20, 30), (20, 30, 40), (30, 40, 50), (40, 50, 60))
        ),
    )

    finding_codes = {finding.code for finding in result.findings}
    assert "citation_marker_source_conflict" in finding_codes
    assert "citation_source_marker_inconsistent" in finding_codes
    mismatch_findings = [
        finding
        for finding in result.findings
        if finding.code == "citation_footer_reference_mismatch"
    ]
    assert [(finding.slide_number, finding.details["marker"]) for finding in mismatch_findings] == [
        (2, 1),
        (3, 2),
    ]


def test_citation_footer_rejects_a_repeated_marker_combining_two_sources(tmp_path: Path) -> None:
    combined = (
        "[1] 資料來源：年度報告.pdf，PDF 第 3 頁；"
        "[2] 資料來源：全民健保檢討報告（草案），PDF 第 7 頁"
    )
    _write_deck(
        tmp_path,
        paragraphs_by_slide={1: ["Cover title", combined]},
    )

    result = _validate(tmp_path)

    findings = [
        finding
        for finding in result.findings
        if finding.code == "citation_footer_combines_sources"
    ]
    assert len(findings) == 1
    assert findings[0].slide_number == 1
    assert findings[0].details == {"citation": combined}
    assert findings[0].origin == "candidate"


def test_citation_footer_rejects_stacked_numbers_before_one_marker(tmp_path: Path) -> None:
    combined = "[1][2] 資料來源：A；B"
    _write_deck(
        tmp_path,
        paragraphs_by_slide={1: ["Cover title", combined]},
    )

    result = _validate(tmp_path)

    findings = [
        finding
        for finding in result.findings
        if finding.code == "citation_footer_combines_sources"
    ]
    assert len(findings) == 1
    assert findings[0].slide_number == 1
    assert findings[0].details == {"citation": combined}


def test_citation_footer_bracket_in_locator_is_not_a_combined_source(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={1: ["Cover title", "[1] 資料來源：年度報告.pdf，第[3]節"]},
    )

    result = _validate(tmp_path)

    assert not any(
        finding.code == "citation_footer_combines_sources" for finding in result.findings
    )


def test_body_text_mentioning_the_citation_marker_once_is_not_combined(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        paragraphs_by_slide={1: ["Cover title", "本報告的資料來源包括多份文件"]},
    )

    result = _validate(tmp_path)

    assert not any(
        finding.code == "citation_footer_combines_sources" for finding in result.findings
    )


def test_two_sources_on_separate_footer_paragraphs_still_passes(tmp_path: Path) -> None:
    _write_deck(
        tmp_path,
        slide_count=3,
        paragraphs_by_slide={
            1: ["Cover title"],
            2: [
                "Policy result",
                "[1] 資料來源：年度報告.pdf，PDF 第 12 頁",
                "[2] 資料來源：政策說明.docx，〈給付範圍〉",
            ],
            3: [
                "參考資料",
                "[1] 年度報告.pdf，〈財務〉，PDF 第 12 頁",
                "[2] 政策說明.docx，〈給付範圍〉",
            ],
        },
    )
    write_source_manifest(
        tmp_path / "work" / "sources.json",
        ["report.pdf", "policy.docx"],
        ["年度報告.pdf", "政策說明.docx"],
    )

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    assert result.status is ValidationStatus.PASS
    assert not any(
        finding.code == "citation_footer_combines_sources" for finding in result.findings
    )


@pytest.mark.parametrize("manifest_contents", [None, "not JSON"])
def test_source_manifest_missing_or_malformed_is_infrastructure_failure(
    tmp_path: Path,
    manifest_contents: str | None,
) -> None:
    _write_deck(tmp_path)
    sources_path = tmp_path / "work" / "sources-invalid.json"
    if manifest_contents is not None:
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text(manifest_contents, encoding="utf-8")

    result = _validate(tmp_path, sources_path=sources_path)

    finding = next(
        finding for finding in result.findings if finding.code == "source_manifest_unavailable"
    )
    assert finding.origin == "infrastructure"


def test_approved_outline_matching_the_deck_passes(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=3)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=2,
    )
    _write_outline_mapping(tmp_path, [("cover", 1, 1), ("body", 2, 2)])

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    assert result.status is ValidationStatus.PASS
    assert not any(finding.code.startswith("outline_") for finding in result.findings)


def test_outline_node_missing_from_deck_is_a_candidate_finding(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=3)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("missing", "Regional Risk Outlook")],
        total_slides=2,
    )
    _write_outline_mapping(tmp_path, [("cover", 1, 2)])

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    assert result.status is ValidationStatus.FAIL
    finding = next(finding for finding in result.findings if finding.code == "outline_node_missing")
    assert finding.origin == "candidate"
    assert finding.details["node_id"] == "missing"
    assert finding.details["heading"] == "Regional Risk Outlook"


def test_outline_total_slides_mismatch_is_a_candidate_finding(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=3)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=5,
    )
    _write_outline_mapping(tmp_path, [("cover", 1, 1), ("body", 2, 2)])

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    assert result.status is ValidationStatus.FAIL
    finding = next(finding for finding in result.findings if finding.code == "outline_slide_count_mismatch")
    assert finding.origin == "candidate"
    assert finding.details == {"expected": 5, "actual": 2}


def test_missing_outline_mapping_is_a_candidate_finding(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=3)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=2,
    )

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    finding = next(finding for finding in result.findings if finding.code == "outline_mapping_missing")
    assert finding.origin == "candidate"


def test_malformed_outline_mapping_is_a_candidate_finding(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=3)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=2,
    )
    mapping_path = tmp_path / "work" / "outline_mapping.json"
    mapping_path.write_text("not JSON", encoding="utf-8")

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    finding = next(finding for finding in result.findings if finding.code == "outline_mapping_invalid")
    assert finding.origin == "candidate"


def test_outline_mapping_rejects_extra_top_level_fields(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=3)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=2,
    )
    mapping_path = _write_outline_mapping(tmp_path, [("cover", 1, 1), ("body", 2, 2)])
    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    mapping_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    assert any(finding.code == "outline_mapping_invalid" for finding in result.findings)


@pytest.mark.parametrize(
    ("mapping", "expected_codes"),
    [
        (
            [("cover", 1, 1), ("cover", 2, 2), ("body", 2, 2)],
            {"outline_node_duplicate", "outline_range_overlap", "outline_slide_coverage"},
        ),
        (
            [("body", 1, 1), ("cover", 2, 2)],
            {"outline_node_order"},
        ),
        (
            [("cover", 2, 2), ("body", 1, 1)],
            {"outline_range_order"},
        ),
        (
            [("cover", 1, 1), ("body", 3, 3)],
            {"outline_range_out_of_bounds", "outline_slide_coverage"},
        ),
    ],
)
def test_outline_mapping_structural_violations_are_candidate_findings(
    tmp_path: Path,
    mapping: list[tuple[str, int, int]],
    expected_codes: set[str],
) -> None:
    _write_deck(tmp_path, slide_count=3)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=2,
    )
    _write_outline_mapping(tmp_path, mapping)

    result = _validate(
        tmp_path,
        expected_slide_count=2,
        renderer=_renderer(((10, 20, 30), (20, 30, 40), (30, 40, 50))),
    )

    finding_codes = {finding.code for finding in result.findings}
    assert expected_codes <= finding_codes
    assert all(
        finding.origin == "candidate"
        for finding in result.findings
        if finding.code in expected_codes
    )


def test_outline_mapping_ranges_must_be_contiguous_and_cover_content_slides(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=4)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 3")],
        total_slides=3,
    )
    _write_outline_mapping(tmp_path, [("cover", 1, 1), ("body", 3, 3)])

    result = _validate(
        tmp_path,
        expected_slide_count=3,
        renderer=_renderer(
            ((10, 20, 30), (20, 30, 40), (30, 40, 50), (40, 50, 60))
        ),
    )

    finding_codes = {finding.code for finding in result.findings}
    assert "outline_range_gap" in finding_codes
    assert "outline_slide_coverage" in finding_codes
