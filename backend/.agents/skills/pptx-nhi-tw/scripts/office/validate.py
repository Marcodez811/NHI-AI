from pathlib import Path
import runpy
import sys

target = Path(__file__).resolve().parents[5] / "skills" / "pptx-nhi-tw" / "scripts" / "office" / "validate.py"
if not target.is_file():
    raise SystemExit(f"Authorized upstream Office validator is unavailable: {target}")
sys.path.insert(0, str(target.parent))
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
