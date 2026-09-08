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
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("deck", type=Path)
    parser.add_argument("--pdf-dir", type=Path, default=Path("work/rendered/pdf"))
    parser.add_argument("--output-dir", type=Path, default=Path("work/rendered/final"))
    parser.add_argument("--metadata", type=Path, default=Path("work/rendered/render_metadata.json"))
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    deck = args.deck.resolve(strict=True)
    args.pdf_dir.mkdir(parents=True, exist_ok=True)
    helper = Path(__file__).resolve().parent / "office" / "soffice.py"
    office_args = [sys.executable, str(helper), "--headless", "--convert-to", "pdf", "--outdir", str(args.pdf_dir), str(deck)]
    subprocess.run(office_args, check=True)
    pdf = args.pdf_dir / f"{deck.stem}.pdf"
    if not pdf.is_file():
        raise RuntimeError(f"LibreOffice did not produce {pdf}")

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="author_render_", dir=args.output_dir.parent) as temporary:
        staging = Path(temporary)
        ppm_args = ["pdftoppm", "-png", "-r", str(args.dpi), str(pdf), str(staging / "slide")]
        subprocess.run(ppm_args, check=True)
        by_number: dict[int, Path] = {}
        for path in sorted(staging.iterdir()):
            match = SLIDE_RE.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_file():
                raise RuntimeError(f"unexpected pdftoppm output: {path.name}")
            number = int(match.group(1))
            if number in by_number:
                raise RuntimeError(f"duplicate pdftoppm slide number: {number}")
            by_number[number] = path
        if not by_number:
            raise RuntimeError("pdftoppm produced no PNG slides")
        if args.output_dir.is_symlink() or args.output_dir.is_file():
            args.output_dir.unlink()
        elif args.output_dir.is_dir():
            shutil.rmtree(args.output_dir)
        args.output_dir.mkdir(parents=True)
        for number, source in sorted(by_number.items()):
            shutil.copyfile(source, args.output_dir / f"slide-{number}.png")

    _write_json(
        args.metadata,
        {
            "format_version": "AuthorRenderMetadata v1",
            "deck": str(args.deck),
            "dpi": args.dpi,
            "slide_count": len(by_number),
            "commands": {"libreoffice": office_args[2:-1] + ["<deck>"], "pdftoppm": ppm_args[1:-2] + ["<pdf>", "<output-prefix>"]},
            "tools": {"python": sys.version.split()[0], "soffice": _version("soffice", "--version"), "pdftoppm": _version("pdftoppm", "-v")},
            "environment": {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ},
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
