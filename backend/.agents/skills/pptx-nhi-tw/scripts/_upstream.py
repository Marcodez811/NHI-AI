"""Run the authorized project PPTX tooling without duplicating its large XSD bundle."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def run(relative_path: str) -> None:
    project_root = Path(__file__).resolve().parents[4]
    target = project_root / "skills" / "pptx-nhi-tw" / "scripts" / relative_path
    if not target.is_file():
        raise SystemExit(f"Authorized upstream PPTX tool is unavailable: {target}")
    sys.path.insert(0, str(target.parent))
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")
