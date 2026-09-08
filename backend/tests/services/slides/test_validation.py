from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

from app.services.slides.validation import ValidationStatus, validate_candidate_deck


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
    with zipfile.ZipFile(deck, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return deck


def _write_renders(root: Path, slide_count: int = 2, *, identical: bool = False) -> None:
    render_dir = root / "work" / "rendered" / "final"
    render_dir.mkdir(parents=True)
    for index in range(1, slide_count + 1):
        color = (50, 100, 150) if identical else (index * 40, 100, 150)
        (render_dir / f"slide-{index}.png").write_bytes(_png_bytes(color))


def _pass_checker(path: Path) -> dict[str, object]:
    return {
        "format_version": "trusted-test-checker",
        "presentation": {"path": str(path), "sha256": "test", "slide_count": 2},
        "status": "pass",
        "findings": [],
    }


def test_valid_candidate_writes_hash_bound_snapshot_and_content_check(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    _write_renders(tmp_path)
    result = validate_candidate_deck(
        tmp_path,
        expected_slide_count=2,
        requested_title="Cover title",
        content_checker=_pass_checker,
        require_logo=False,
        check_libreoffice=False,
        check_trusted_renders=False,
    )

    assert result.status is ValidationStatus.PASS
    assert result.deck_snapshot["format_version"] == "DeckSnapshot v1"
    assert result.deck_snapshot["artifact_binding"]["pptx_sha256"] == result.pptx_sha256
    assert [slide["slide_number"] for slide in result.deck_snapshot["slides"]] == [1, 2]
    assert result.content_check_path.is_file()
    assert result.deck_snapshot_path.is_file()


def test_candidate_fails_for_identical_decoded_renders(tmp_path: Path) -> None:
    _write_deck(tmp_path)
    _write_renders(tmp_path, identical=True)
    result = validate_candidate_deck(
        tmp_path,
        expected_slide_count=2,
        content_checker=_pass_checker,
        require_logo=False,
        check_libreoffice=False,
    )

    assert result.status is ValidationStatus.FAIL
    assert any(finding.code == "render_all_identical" for finding in result.findings)


def test_candidate_fails_for_missing_render_number_and_wrong_count(tmp_path: Path) -> None:
    _write_deck(tmp_path, slide_count=2)
    render_dir = tmp_path / "work" / "rendered" / "final"
    render_dir.mkdir(parents=True)
    (render_dir / "slide-2.png").write_bytes(_png_bytes((50, 100, 150)))
    result = validate_candidate_deck(
        tmp_path,
        expected_slide_count=3,
        content_checker=_pass_checker,
        require_logo=False,
        check_libreoffice=False,
    )

    codes = {finding.code for finding in result.findings}
    assert result.status is ValidationStatus.FAIL
    assert "slide_count" in codes
    assert "render_missing" in codes


def test_candidate_fails_when_submitted_renders_differ_from_trusted_render(
    tmp_path: Path,
) -> None:
    _write_deck(tmp_path)
    _write_renders(tmp_path)

    def trusted_renderer(deck: Path, output_dir: Path) -> None:
        del deck
        output_dir.mkdir(parents=True, exist_ok=True)
        # These files are valid, nonblank, and distinct.  They deliberately
        # represent a different render than the author-submitted PNGs.
        (output_dir / "slide-1.png").write_bytes(_png_bytes((10, 20, 30)))
        (output_dir / "slide-2.png").write_bytes(_png_bytes((20, 30, 40)))

    result = validate_candidate_deck(
        tmp_path,
        expected_slide_count=2,
        content_checker=_pass_checker,
        require_logo=False,
        check_libreoffice=False,
        trusted_render=trusted_renderer,
    )

    assert result.status is ValidationStatus.FAIL
    mismatches = [
        finding for finding in result.findings if finding.code == "render_stale_or_mismatch"
    ]
    assert [finding.slide_number for finding in mismatches] == [1, 2]
