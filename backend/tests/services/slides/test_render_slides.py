from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from PIL import Image
from pypdf import PdfWriter


SCRIPT_PATH = Path(__file__).resolve().parents[3] / ".agents" / "skills" / "pptx-nhi-tw" / "scripts" / "render_slides.py"
SPEC = importlib.util.spec_from_file_location("render_slides_test_module", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
render_slides = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render_slides)


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (32, 18), color).save(path, format="PNG")


def test_publish_preview_preserves_mount_directory_and_canonicalizes_names(tmp_path: Path) -> None:
    preview = tmp_path / "preview"
    staged = tmp_path / "staged"
    metadata = preview / "render_metadata.json"
    preview.mkdir()
    staged.mkdir()
    _write_png(preview / "slide-1.png", (1, 2, 3))
    _write_png(preview / "slide-2.png", (4, 5, 6))
    metadata.write_text("old", encoding="utf-8")
    (preview / "notes.txt").write_text("keep", encoding="utf-8")
    source = staged / "slide-01.png"
    _write_png(source, (7, 8, 9))

    render_slides._publish_preview({1: source}, preview, metadata)

    assert preview.is_dir()
    assert (preview / "slide-1.png").read_bytes() == source.read_bytes()
    assert not (preview / "slide-2.png").exists()
    assert not metadata.exists()
    assert (preview / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_collect_staged_renders_rejects_duplicate_numeric_names_and_corrupt_png(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()
    _write_png(staged / "slide-1.png", (1, 2, 3))
    _write_png(staged / "slide-01.png", (4, 5, 6))

    with pytest.raises(RuntimeError, match="duplicate"):
        render_slides._collect_staged_renders(staged, 1)

    (staged / "slide-01.png").unlink()
    (staged / "slide-2.png").write_bytes(b"not a PNG")
    with pytest.raises(RuntimeError, match="invalid PNG"):
        render_slides._collect_staged_renders(staged, 2)


def test_main_replaces_previews_only_after_a_complete_pdf_render_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = tmp_path / "presentation.pptx"
    deck.write_bytes(b"placeholder")
    preview = tmp_path / "preview"
    pdf_dir = tmp_path / "preview-pdf"
    preview.mkdir()
    (preview / "notes.txt").write_text("keep", encoding="utf-8")
    page_counts = iter((2, 1))
    active_page_count = 0

    def fake_run(args: list[str], **_: object) -> CompletedProcess[str]:
        nonlocal active_page_count
        if args[0] == sys.executable:
            active_page_count = next(page_counts)
            output = Path(args[args.index("--outdir") + 1]) / f"{deck.stem}.pdf"
            writer = PdfWriter()
            for _ in range(active_page_count):
                writer.add_blank_page(width=100, height=100)
            with output.open("wb") as handle:
                writer.write(handle)
        elif args[0] == "pdftoppm":
            prefix = Path(args[-1])
            for number in range(1, active_page_count + 1):
                suffix = f"{number:02}" if active_page_count == 1 else str(number)
                _write_png(prefix.with_name(f"{prefix.name}-{suffix}.png"), (number, 2, 3))
        else:
            raise AssertionError(args)
        return CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(render_slides.subprocess, "run", fake_run)
    monkeypatch.setattr(render_slides, "_version", lambda *_: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT_PATH),
            str(deck),
            "--pdf-dir",
            str(pdf_dir),
            "--output-dir",
            str(preview),
        ],
    )

    assert render_slides.main() == 0
    assert sorted(path.name for path in preview.glob("slide-*.png")) == ["slide-1.png", "slide-2.png"]
    assert render_slides.main() == 0
    assert sorted(path.name for path in preview.glob("slide-*.png")) == ["slide-1.png"]
    assert (preview / "render_metadata.json").is_file()
    assert (preview / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert not list(pdf_dir.glob("author_render_*"))


def test_main_clears_old_generated_previews_when_conversion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = tmp_path / "presentation.pptx"
    deck.write_bytes(b"placeholder")
    preview = tmp_path / "preview"
    preview.mkdir()
    _write_png(preview / "slide-1.png", (1, 2, 3))
    (preview / "render_metadata.json").write_text("old", encoding="utf-8")
    (preview / "notes.txt").write_text("keep", encoding="utf-8")

    def fake_run(args: list[str], **_: object) -> CompletedProcess[str]:
        # Simulate a successful converter process that fails to produce a PDF.
        return CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(render_slides.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), str(deck), "--output-dir", str(preview)])

    with pytest.raises(RuntimeError, match="did not produce"):
        render_slides.main()

    assert not list(preview.glob("slide-*.png"))
    assert not (preview / "render_metadata.json").exists()
    assert (preview / "notes.txt").read_text(encoding="utf-8") == "keep"
