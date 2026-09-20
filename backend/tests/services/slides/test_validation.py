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
    validate_candidate_deck,
)


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


def _write_deck(root: Path, slide_count: int = 2) -> Path:
    deck = root / "output" / "presentation.pptx"
    deck.parent.mkdir(parents=True)
    parts = {
        "[Content_Types].xml": f'''<?xml version="1.0"?><Types xmlns="{CT_NS}">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
          {''.join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1, slide_count + 1))}
          {''.join(f'<Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>' for i in range(1, slide_count + 1))}
        </Types>''',
        "ppt/presentation.xml": f'''<p:presentation xmlns:p="{P_NS}" xmlns:r="{R_NS}">
          <p:sldIdLst>{''.join(f'<p:sldId id="{i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))}</p:sldIdLst>
        </p:presentation>''',
        "ppt/_rels/presentation.xml.rels": f'''<Relationships xmlns="{REL_NS}">
          {''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>' for i in range(1, slide_count + 1))}
        </Relationships>''',
    }
    for index in range(1, slide_count + 1):
        text = "Cover title" if index == 1 else f"Slide {index}"
        parts[f"ppt/slides/slide{index}.xml"] = f'''<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">
          <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr/><p:sp><p:nvSpPr/><p:spPr/>
            <p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr/><a:t>{text}</a:t></a:r></a:p></p:txBody>
          </p:sp></p:spTree></p:cSld>
        </p:sld>'''
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


def _validate(root: Path, **kwargs: object):
    options: dict[str, object] = {
        "expected_slide_count": 2,
        "requested_title": "Cover title",
        "content_checker": _pass_checker,
        "require_logo": False,
        "renderer": _renderer(),
    }
    options.update(kwargs)
    return validate_candidate_deck(root, **options)


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


def test_approved_outline_matching_the_deck_passes(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=2,
    )

    result = _validate(tmp_path)

    assert result.status is ValidationStatus.PASS
    assert not any(finding.code.startswith("outline_") for finding in result.findings)


def test_outline_node_missing_from_deck_is_a_candidate_finding(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("missing", "Regional Risk Outlook")],
        total_slides=2,
    )

    result = _validate(tmp_path)

    assert result.status is ValidationStatus.FAIL
    finding = next(finding for finding in result.findings if finding.code == "outline_node_missing")
    assert finding.origin == "candidate"
    assert finding.details["node_id"] == "missing"
    assert finding.details["heading"] == "Regional Risk Outlook"


def test_outline_total_slides_mismatch_is_a_candidate_finding(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    _write_outline(
        tmp_path,
        nodes=[_outline_node("cover", "Cover title"), _outline_node("body", "Slide 2")],
        total_slides=5,
    )

    result = _validate(tmp_path)

    assert result.status is ValidationStatus.FAIL
    finding = next(finding for finding in result.findings if finding.code == "outline_slide_count_mismatch")
    assert finding.origin == "candidate"
    assert finding.details == {"expected": 5, "actual": 2}
