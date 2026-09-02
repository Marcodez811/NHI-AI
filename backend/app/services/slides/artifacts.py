"""Filesystem, preflight, skill-staging, and artifact validation helpers."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

from app.models.slides import DEFAULT_MAX_DOCUMENTS, SUPPORTED_SLIDE_SOURCE_EXTENSIONS

from .contracts import JobError

ASSETS_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = ASSETS_ROOT.parents[2]
# Skills are a backend-global catalog.  Adapters declare the subset they need
# and the generic orchestrator stages only those names into each job.
REPOSITORY_SKILLS_DIR = ASSETS_ROOT.parents[2] / ".agents" / "skills"
WINDOWS_FONTS_DIR = Path("/mnt/c/Windows/Fonts")
SOURCE_SKILL = "source-document-extraction"
PPTX_SKILL = "pptx-nhi-tw"
REQUIRED_SKILLS = (SOURCE_SKILL, PPTX_SKILL)
# Backwards-compatible import surface for callers that used this module's old
# constant. The canonical set is shared with the API and worker resolver.
SUPPORTED_SOURCE_EXTENSIONS = SUPPORTED_SLIDE_SOURCE_EXTENSIONS
REQUIRED_PYTHON_AGENT_PACKAGES = {"PIL": "Pillow", "lxml": "lxml", "defusedxml": "defusedxml"}
REQUIRED_PDF_AGENT_PACKAGES = {
    "pdfplumber": "pdfplumber",
    "pypdf": "pypdf",
    "pypdfium2": "pypdfium2",
    "pytesseract": "pytesseract",
}
REQUIRED_NODE_MODULES = ("pptxgenjs", "sharp", "react", "react-dom", "react-icons")
REQUIRED_TESSERACT_LANGUAGES = {"chi_tra", "eng"}
CJK_FONT_PREFERENCES = (
    "Microsoft JhengHei", "Noto Sans CJK", "Noto Sans TC", "Noto Serif CJK", "Source Han", "PingFang TC",
)
FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}
WINDOWS_JHENGHEI_FILES = ("msjh.ttc", "msjhbd.ttc", "msjhl.ttc")


def _run_checked_tool(command: Sequence[str], label: str, runner: Callable[..., subprocess.CompletedProcess[str]], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(list(command), cwd=cwd, capture_output=True, text=True, timeout=120, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise JobError("preflight", f"could not run {label}: {exc}") from exc
    if result.returncode:
        details = (result.stderr or result.stdout or "").strip()
        raise JobError("preflight", f"{label} failed: {details or 'no details'}")
    return result


def _has_font_files(directory: Path) -> bool:
    return directory.is_dir() and any(path.is_file() and path.suffix.lower() in FONT_SUFFIXES for path in directory.iterdir())


def discover_cjk_font_dirs(assets_root: Path = ASSETS_ROOT, windows_fonts_dir: Path = WINDOWS_FONTS_DIR) -> list[Path]:
    directories: list[Path] = []
    workspace_fonts = assets_root / "fonts"
    if _has_font_files(workspace_fonts):
        directories.append(workspace_fonts.resolve())
    if windows_fonts_dir.is_dir() and any((windows_fonts_dir / name).is_file() for name in WINDOWS_JHENGHEI_FILES):
        directories.append(windows_fonts_dir.resolve())
    return directories


def create_job_fontconfig(job_dir: Path, *, assets_root: Path = ASSETS_ROOT, windows_fonts_dir: Path = WINDOWS_FONTS_DIR) -> Path | None:
    font_dirs = discover_cjk_font_dirs(assets_root, windows_fonts_dir)
    if not font_dirs:
        return None
    config_dir = job_dir / "work" / "fontconfig"
    cache_dir = config_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    directories = "\n".join(f"  <dir>{escape(str(path))}</dir>" for path in font_dirs)
    config_path = config_dir / "fonts.conf"
    config_path.write_text(
        "<?xml version=\"1.0\"?>\n<!DOCTYPE fontconfig SYSTEM \"urn:fontconfig:fonts.dtd\">\n<fontconfig>\n"
        "  <include ignore_missing=\"yes\">/etc/fonts/fonts.conf</include>\n"
        f"{directories}\n  <cachedir>{escape(str(cache_dir))}</cachedir>\n</fontconfig>\n",
        encoding="utf-8",
    )
    return config_path


def preflight(input_paths: Sequence[Path], *, tool_lookup: Callable[[str], str | None] = shutil.which, module_lookup: Callable[[str], Any] = importlib.util.find_spec, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run, backend_root: Path = BACKEND_ROOT, environment: dict[str, str] | None = None) -> str:
    has_pdf = any(path.suffix.lower() == ".pdf" for path in input_paths)
    required_modules = dict(REQUIRED_PYTHON_AGENT_PACKAGES)
    if has_pdf:
        required_modules.update(REQUIRED_PDF_AGENT_PACKAGES)
    missing_modules = [package for import_name, package in required_modules.items() if module_lookup(import_name) is None]
    if missing_modules:
        raise JobError("preflight", "missing Python packages: " + ", ".join(missing_modules))
    required_tools = {"node": "Node.js", "fc-list": "fontconfig"}
    if has_pdf:
        required_tools["tesseract"] = "Tesseract"
    missing_tools = [label for name, label in required_tools.items() if not tool_lookup(name)]
    if missing_tools:
        raise JobError("preflight", "missing system tools: " + ", ".join(missing_tools))
    if not tool_lookup("soffice"):
        raise JobError("preflight", "LibreOffice is required to render and visually validate the slides, but 'soffice' was not found")
    module_checks = "; ".join(f"require.resolve({name!r})" for name in REQUIRED_NODE_MODULES)
    _run_checked_tool(["node", "-e", module_checks], "required Node modules", runner, cwd=backend_root, env=environment)
    fonts = _run_checked_tool(["fc-list", "--format=%{family}\\n"], "CJK font discovery", runner, env=environment).stdout
    selected_font = next((family for family in CJK_FONT_PREFERENCES if family.casefold() in fonts.casefold()), None)
    if not selected_font:
        raise JobError("preflight", "no Traditional-Chinese/CJK font was found")
    if has_pdf:
        languages = set(_run_checked_tool(["tesseract", "--list-langs"], "Tesseract language discovery", runner, env=environment).stdout.split())
        missing_languages = sorted(REQUIRED_TESSERACT_LANGUAGES - languages)
        if missing_languages:
            raise JobError("preflight", "missing Tesseract language data: " + ", ".join(missing_languages))
    return selected_font


def validate_source_paths(source_paths: Sequence[Path]) -> list[Path]:
    if not 1 <= len(source_paths) <= DEFAULT_MAX_DOCUMENTS:
        raise JobError("inputs", f"expected between 1 and {DEFAULT_MAX_DOCUMENTS} source files")
    seen: set[Path] = set()
    validated: list[Path] = []
    for path in source_paths:
        if not path.is_absolute() or path != path.resolve():
            raise JobError("inputs", "source paths must be resolved absolute file paths")
        if path in seen:
            raise JobError("inputs", "source paths must be unique")
        if not path.is_file():
            raise JobError("inputs", "a source is not a regular file")
        if path.suffix.lower() not in SUPPORTED_SOURCE_EXTENSIONS:
            raise JobError("inputs", "a source has an unsupported file type")
        seen.add(path)
        validated.append(path)
    return validated


def create_job_workspace(job_id: str, jobs_root: Path) -> Path:
    if not isinstance(job_id, str) or not job_id or job_id in {".", ".."} or Path(job_id).name != job_id:
        raise JobError("workspace", "job identifier is invalid")
    jobs_root = Path(jobs_root).expanduser().resolve()
    try:
        jobs_root.mkdir(parents=True, exist_ok=True)
        job_dir = jobs_root / job_id
        if job_dir.is_symlink():
            raise JobError("workspace", "job workspace is invalid")
        job_dir.mkdir(parents=True, exist_ok=True)
        resolved = job_dir.resolve(strict=True)
    except JobError:
        raise
    except OSError as exc:
        raise JobError("workspace", "job workspace is unavailable") from exc
    try:
        resolved.relative_to(jobs_root)
    except ValueError as exc:
        raise JobError("workspace", "job workspace is invalid") from exc
    for relative_path in ("input", "template", "work/extracted", "work/images", "work/intermediate", "output"):
        (job_dir / relative_path).mkdir(parents=True, exist_ok=True)
    return job_dir


def discover_repo_skills(skills_dir: Path = REPOSITORY_SKILLS_DIR) -> dict[str, Path]:
    """List job skills from the service-local asset tree."""

    if not skills_dir.is_dir():
        return {}
    return {path.name: path for path in skills_dir.iterdir() if path.is_dir()}


def stage_required_skills(job_dir: Path, skills_dir: Path = REPOSITORY_SKILLS_DIR) -> list[str]:
    available = discover_repo_skills(skills_dir)
    missing = [name for name in REQUIRED_SKILLS if name not in available]
    if missing:
        raise JobError("stage_skills", "missing repository skills: " + ", ".join(missing))
    destination_root = job_dir / ".agents" / "skills"
    destination_root.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_SKILLS:
        source = available[name]
        if not (source / "SKILL.md").is_file():
            raise JobError("stage_skills", f"skill has no SKILL.md: {name}")
        shutil.copytree(source, destination_root / name, ignore=shutil.ignore_patterns("*:Zone.Identifier", "__pycache__", "*.pyc"))
    return list(REQUIRED_SKILLS)


def stage_uploads(job_dir: Path, uploaded_paths: Sequence[Path]) -> list[str]:
    staged_names: list[str] = []
    for source in uploaded_paths:
        if not source.is_file():
            raise JobError("stage_uploads", "a source file disappeared before staging")
        destination = job_dir / "input" / source.name
        if destination.exists():
            destination = destination.with_stem(f"{destination.stem}__{len(staged_names) + 1}")
        shutil.copy2(source, destination)
        staged_names.append(destination.name)
    return staged_names


def _pptx_slide_count(path: Path) -> int:
    if not path.is_file():
        raise JobError("verify_output", "presentation not found")
    if path.stat().st_size < 10_000:
        raise JobError("verify_output", f"presentation is suspiciously small: {path.stat().st_size} bytes")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise JobError("verify_output", "presentation is not a valid PPTX/ZIP") from exc
    if "[Content_Types].xml" not in names:
        raise JobError("verify_output", "presentation has no OOXML content types")
    slides = [name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
    if not slides:
        raise JobError("verify_output", "presentation contains no slides")
    return len(slides)


def _run_validator(command: Sequence[str], label: str, job_dir: Path, runner: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    try:
        result = runner(list(command), cwd=job_dir, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise JobError("verify_output", f"could not run {label}: {exc}") from exc
    if result.returncode:
        details = (result.stderr or result.stdout or "").strip()
        raise JobError("verify_output", f"{label} failed: {details or 'no details'}")


def verify_output(
    job_dir: Path,
    *,
    expected_slide_count: int | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    deck = job_dir / "output" / "presentation.pptx"
    slide_count = _pptx_slide_count(deck)

    if (
        expected_slide_count is not None
        and slide_count != expected_slide_count
    ):
        raise JobError(
            "verify_output",
            (
                f"expected {expected_slide_count} slides, "
                f"found {slide_count}"
            ),
        )

    skills = job_dir / ".agents" / "skills"
    evidence = job_dir / "work" / "intermediate" / "evidence_map.json"
    content_check = job_dir / "work" / "intermediate" / "content_check.json"
    qa_report = job_dir / "work" / "intermediate" / "qa_report.json"
    review_report = job_dir / "work" / "intermediate" / "review_report.json"
    manifest = job_dir / "work" / "extracted" / "manifest.json"
    pptx_scripts = skills / PPTX_SKILL / "scripts"

    required_artifacts = (
        evidence,
        content_check,
        review_report,
        qa_report,
    )

    if not all(path.is_file() for path in required_artifacts):
        raise JobError(
            "verify_output",
            "EvidenceMap, content check, SlideReview, or QAReport is missing",
        )

    input_dir = job_dir / "input"
    has_extractable_source = any(
        path.suffix.lower() in {".docx", ".pdf"}
        for path in input_dir.iterdir()
    )

    if has_extractable_source:
        if not manifest.is_file():
            raise JobError(
                "verify_output",
                "source extraction manifest is missing",
            )

        _run_validator(
            [
                sys.executable,
                str(
                    skills
                    / SOURCE_SKILL
                    / "scripts"
                    / "validate_sources.py"
                ),
                str(manifest.parent),
            ],
            "source extraction validation",
            job_dir,
            runner,
        )

        _run_validator(
            [
                sys.executable,
                str(
                    pptx_scripts
                    / "validate_evidence_map.py"
                ),
                str(manifest),
                str(evidence),
            ],
            "EvidenceMap validation",
            job_dir,
            runner,
        )

    _run_validator(
        [
            sys.executable,
            str(pptx_scripts / "review_report.py"),
            "validate",
            "--pptx",
            str(deck),
            "--renders",
            str(job_dir / "work" / "rendered" / "final"),
            "--report",
            str(review_report),
        ],
        "SlideReview validation",
        job_dir,
        runner,
    )

    _run_validator(
        [
            sys.executable,
            str(pptx_scripts / "check_pptx_content.py"),
            str(deck),
            "--output",
            str(content_check),
            "--fail-on-findings",
        ],
        "PPTX content validation",
        job_dir,
        runner,
    )

    _run_validator(
        [
            sys.executable,
            str(pptx_scripts / "qa_report.py"),
            "validate",
            "--pptx",
            str(deck),
            "--evidence-map",
            str(evidence),
            "--report",
            str(qa_report),
        ],
        "QAReport validation",
        job_dir,
        runner,
    )

    renders = sorted(
        (job_dir / "work" / "rendered" / "final").glob("*.png")
    )

    if len(renders) != slide_count:
        raise JobError(
            "verify_output",
            (
                f"expected {slide_count} final slide renders, "
                f"found {len(renders)}"
            ),
        )

    invalid = [
        path.name
        for path in renders
        if path.stat().st_size <= 8
        or path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"
    ]

    if invalid:
        raise JobError(
            "verify_output",
            "invalid PNG renders: " + ", ".join(invalid),
        )

    return deck


def publish_output(output_path: Path, job_id: str, destination_dir: Path) -> Path:
    if not isinstance(job_id, str) or not job_id or job_id in {".", ".."} or Path(job_id).name != job_id:
        raise JobError("publish", "job identifier is invalid")
    output_path = Path(output_path)
    if output_path.is_symlink() or not output_path.is_file():
        raise JobError("publish", "presentation artifact is unavailable")
    destination = destination_dir.expanduser().resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise JobError("publish", "presentation artifact could not be published") from exc
    published = destination / f"{job_id}.pptx"
    # Copy to a destination-local temporary file, then create a hard link with
    # an exclusive no-overwrite operation.  This makes publication atomic and
    # protects an already-published artifact from replayed job IDs.
    temporary = destination / f".{published.name}.{uuid4().hex}.tmp"
    try:
        shutil.copy2(output_path, temporary, follow_symlinks=False)
        os.link(temporary, published)
        temporary.unlink(missing_ok=True)
        output_path.unlink()
    except FileExistsError as exc:
        temporary.unlink(missing_ok=True)
        raise JobError("publish", "presentation artifact already exists") from exc
    except (OSError, shutil.Error) as exc:
        temporary.unlink(missing_ok=True)
        raise JobError("publish", "presentation artifact could not be published") from exc
    return published


def cleanup_job(job_dir: Path) -> None:
    shutil.rmtree(job_dir, ignore_errors=True)
