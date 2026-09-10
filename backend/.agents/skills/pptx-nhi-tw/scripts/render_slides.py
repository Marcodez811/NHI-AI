#!/usr/bin/env python3
"""Render a PPTX with the workflow toolchain and record safe diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader


SLIDE_RE = re.compile(r"slide-([0-9]+)\.png")
ENV_ALLOWLIST = ("FONTCONFIG_FILE", "LANG", "LC_ALL", "LC_CTYPE", "PPTX_CJK_FONT", "SAL_USE_VCLPLUGIN", "TZ")


def _version(command: str, *args: str) -> str | None:
    try:
        result = subprocess.run([command, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0][:200] if lines else None


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clear_preview(preview_dir: Path, metadata_path: Path) -> None:
    """Remove generated preview artifacts without deleting a sandbox mount."""

    if preview_dir.is_symlink() or not preview_dir.is_dir():
        raise RuntimeError(f"preview directory is missing or invalid: {preview_dir}")
    for path in preview_dir.iterdir():
        if (path.is_symlink() or path.is_file()) and (SLIDE_RE.fullmatch(path.name) or path == metadata_path):
            path.unlink()


def _decode_png(path: Path) -> None:
    """Reject truncated or non-PNG converter output before publication."""

    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"expected PNG, got {image.format}")
            image.verify()
        with Image.open(path) as image:
            image.load()
            if image.width < 2 or image.height < 2:
                raise ValueError("image is too small")
    except Exception as exc:
        raise RuntimeError(f"invalid PNG output {path.name}: {exc}") from exc


def _collect_staged_renders(staged_dir: Path, page_count: int) -> dict[int, Path]:
    """Return one decoded render for every PDF page, indexed by page number."""

    if page_count <= 0:
        raise RuntimeError("LibreOffice PDF has no pages")
    by_number: dict[int, Path] = {}
    for path in sorted(staged_dir.iterdir()):
        match = SLIDE_RE.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            raise RuntimeError(f"unexpected pdftoppm output: {path.name}")
        number = int(match.group(1))
        if number in by_number:
            raise RuntimeError(f"duplicate pdftoppm slide number: {number}")
        if number not in range(1, page_count + 1):
            raise RuntimeError(f"pdftoppm produced out-of-range slide number: {number}")
        _decode_png(path)
        by_number[number] = path
    missing = [number for number in range(1, page_count + 1) if number not in by_number]
    if missing:
        raise RuntimeError(f"pdftoppm omitted slide number(s): {missing}")
    return by_number


def _publish_preview(staged: dict[int, Path], preview_dir: Path, metadata_path: Path) -> None:
    """Replace generated PNGs while preserving the mounted preview directory."""

    _clear_preview(preview_dir, metadata_path)
    for number, source in sorted(staged.items()):
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"invalid staged PNG output: {source.name}")
        shutil.copyfile(source, preview_dir / f"slide-{number}.png")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("deck", type=Path)
    parser.add_argument("--pdf-dir", type=Path, default=Path("work/rendered/preview-pdf"))
    parser.add_argument("--output-dir", type=Path, default=Path("work/rendered/preview"))
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.output_dir.is_symlink() or not args.output_dir.is_dir():
        raise RuntimeError(f"preview directory is missing or invalid: {args.output_dir}")
    preview_dir = args.output_dir.resolve(strict=True)
    metadata_path = (args.metadata or args.output_dir / "render_metadata.json").resolve(strict=False)
    if metadata_path.parent != preview_dir:
        raise RuntimeError("preview metadata must be a direct child of the preview directory")
    args.pdf_dir.mkdir(parents=True, exist_ok=True)
    if args.pdf_dir.is_symlink() or not args.pdf_dir.is_dir():
        raise RuntimeError(f"preview PDF directory is missing or invalid: {args.pdf_dir}")
    helper = Path(__file__).resolve().parent / "office" / "soffice.py"
    try:
        deck = args.deck.resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="author_render_", dir=args.pdf_dir) as temporary:
            staging_root = Path(temporary)
            pdf_dir = staging_root / "pdf"
            png_dir = staging_root / "png"
            pdf_dir.mkdir()
            png_dir.mkdir()
            office_args = [sys.executable, str(helper), "--headless", "--convert-to", "pdf", "--outdir", str(pdf_dir), str(deck)]
            subprocess.run(office_args, check=True)
            pdf = pdf_dir / f"{deck.stem}.pdf"
            if not pdf.is_file() or pdf.stat().st_size == 0:
                raise RuntimeError(f"LibreOffice did not produce {pdf}")
            try:
                page_count = len(PdfReader(str(pdf)).pages)
            except Exception as exc:
                raise RuntimeError(f"LibreOffice produced an unreadable PDF: {exc}") from exc
            if page_count <= 0:
                raise RuntimeError("LibreOffice PDF has no pages")
            ppm_args = ["pdftoppm", "-png", "-r", str(args.dpi), str(pdf), str(png_dir / "slide")]
            subprocess.run(ppm_args, check=True)
            by_number = _collect_staged_renders(png_dir, page_count)
            _publish_preview(by_number, preview_dir, metadata_path)
    except Exception:
        _clear_preview(preview_dir, metadata_path)
        raise

    try:
        _write_json(
            metadata_path,
            {
                "format_version": "AuthorPreviewMetadata v1",
                "deck": str(deck),
                "dpi": args.dpi,
                "slide_count": page_count,
                "commands": {"libreoffice": office_args[2:-1] + ["<deck>"], "pdftoppm": ppm_args[1:-2] + ["<pdf>", "<output-prefix>"]},
                "tools": {"python": sys.version.split()[0], "soffice": _version("soffice", "--version"), "pdftoppm": _version("pdftoppm", "-v")},
                "environment": {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ},
            },
        )
    except Exception:
        _clear_preview(preview_dir, metadata_path)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
