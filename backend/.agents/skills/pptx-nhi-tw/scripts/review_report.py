"""Validate SlideReview v1 against the final deck, renders, and NHI logo assets."""

from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from check_pptx_content import (
    DRAWING_NS,
    PACKAGE_RELATIONSHIP_NS,
    RELATIONSHIP_NS,
    _ordered_slide_names,
    check_pptx_content,
)
from contracts import (
    REVIEW_REPORT_VERSION,
    ContractError,
    load_json,
    require_list,
    require_object,
    require_string,
    sha256_file,
)


REQUIRED_CHECKS = {
    "narrative_coherence",
    "factual_coherence",
    "design_coherence",
    "title_branding",
    "visual_quality",
}
VALID_CATEGORIES = {"narrative", "factual", "design", "branding", "visual"}
VALID_SEVERITIES = {"blocking", "advisory"}
VALID_FINDING_STATUSES = {"open", "resolved"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _render_sort_key(path: Path) -> tuple[int, str]:
    matches = re.findall(r"\d+", path.stem)
    return (int(matches[-1]) if matches else 0, path.name)


def _official_logo_hashes(asset_dir: Path) -> set[str]:
    logo_paths = [asset_dir / "nhi_logo_large.png", asset_dir / "nhi_logo_small.png"]
    missing = [str(path) for path in logo_paths if not path.is_file()]
    if missing:
        raise ContractError("Missing bundled NHI logo asset(s): " + ", ".join(missing))
    return {sha256_file(path) for path in logo_paths}


def _first_slide_image_hashes(pptx_path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(pptx_path) as archive:
            slide_names = _ordered_slide_names(archive)
            if not slide_names:
                raise ContractError("PPTX contains no slides")
            slide_name = slide_names[0]
            relationships_name = posixpath.join(
                posixpath.dirname(slide_name),
                "_rels",
                posixpath.basename(slide_name) + ".rels",
            )
            if relationships_name not in archive.namelist():
                return []
            try:
                slide = ET.fromstring(archive.read(slide_name))
                relationships = ET.fromstring(archive.read(relationships_name))
            except ET.ParseError as exc:
                raise ContractError(f"Invalid first-slide relationship XML: {exc}") from exc
            embedded_ids = {
                node.get(f"{{{RELATIONSHIP_NS}}}embed")
                for node in slide.findall(f".//{{{DRAWING_NS}}}blip")
                if node.get(f"{{{RELATIONSHIP_NS}}}embed")
            }
            targets = {
                relation.get("Id"): relation.get("Target")
                for relation in relationships.findall(
                    f"{{{PACKAGE_RELATIONSHIP_NS}}}Relationship"
                )
            }
            hashes: list[str] = []
            for relation_id in embedded_ids:
                target = targets.get(relation_id)
                if not target:
                    continue
                member = (
                    posixpath.normpath(target.lstrip("/"))
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join(posixpath.dirname(slide_name), target))
                )
                if member in archive.namelist():
                    hashes.append(hashlib.sha256(archive.read(member)).hexdigest())
            return hashes
    except FileNotFoundError as exc:
        raise ContractError(f"PPTX file not found: {pptx_path}") from exc
    except zipfile.BadZipFile as exc:
        raise ContractError(f"Invalid PPTX ZIP archive: {pptx_path}") from exc


def _validate_findings(findings: list[Any], slide_count: int) -> None:
    for position, value in enumerate(findings, start=1):
        finding = require_object(value, f"review.findings[{position}]")
        slides = require_list(
            finding.get("slide_numbers"), f"review.findings[{position}].slide_numbers"
        )
        if not slides or any(
            not isinstance(slide, int)
            or isinstance(slide, bool)
            or slide < 1
            or slide > slide_count
            for slide in slides
        ):
            raise ContractError(
                f"review.findings[{position}].slide_numbers must reference final slides"
            )
        category = finding.get("category")
        severity = finding.get("severity")
        status = finding.get("status")
        if category not in VALID_CATEGORIES:
            raise ContractError(f"review.findings[{position}].category is invalid")
        if severity not in VALID_SEVERITIES:
            raise ContractError(f"review.findings[{position}].severity is invalid")
        if status not in VALID_FINDING_STATUSES:
            raise ContractError(f"review.findings[{position}].status is invalid")
        require_string(finding.get("description"), f"review.findings[{position}].description")
        resolution = finding.get("resolution")
        if not isinstance(resolution, str):
            raise ContractError(f"review.findings[{position}].resolution must be a string")
        if status == "resolved" and not resolution.strip():
            raise ContractError(f"review.findings[{position}] is resolved but has no resolution")
        if severity == "blocking" and status != "resolved":
            raise ContractError(f"review.findings[{position}] is an open blocking finding")


def _validate_renders(report: dict[str, Any], render_dir: Path, slide_count: int) -> None:
    render_paths = sorted(render_dir.glob("*.png"), key=_render_sort_key)
    if len(render_paths) != slide_count:
        raise ContractError(
            f"Expected {slide_count} final PNG renders, found {len(render_paths)}"
        )
    invalid = [
        path.name
        for path in render_paths
        if path.stat().st_size <= len(PNG_SIGNATURE)
        or path.read_bytes()[: len(PNG_SIGNATURE)] != PNG_SIGNATURE
    ]
    if invalid:
        raise ContractError("Invalid final PNG render(s): " + ", ".join(invalid))
    entries = require_list(report.get("reviewed_renders"), "review.reviewed_renders")
    if len(entries) != slide_count:
        raise ContractError("review.reviewed_renders must contain exactly one entry per slide")
    by_slide: dict[int, dict[str, Any]] = {}
    for position, value in enumerate(entries, start=1):
        entry = require_object(value, f"review.reviewed_renders[{position}]")
        slide_number = entry.get("slide_number")
        if (
            not isinstance(slide_number, int)
            or isinstance(slide_number, bool)
            or slide_number < 1
            or slide_number > slide_count
            or slide_number in by_slide
        ):
            raise ContractError("review.reviewed_renders has invalid or duplicate slide_number")
        by_slide[slide_number] = entry
    for slide_number, path in enumerate(render_paths, start=1):
        if by_slide[slide_number].get("sha256") != sha256_file(path):
            raise ContractError(
                f"reviewed render SHA-256 does not match final slide {slide_number} PNG"
            )
    if slide_count > 1 and len({sha256_file(path) for path in render_paths}) == 1:
        raise ContractError("All final PNG renders are identical")


def validate_review_report(
    pptx_path: Path,
    render_dir: Path,
    report_path: Path,
    asset_dir: Path | None = None,
) -> list[str]:
    """Return deterministic validation errors for a release-ready SlideReview v1."""

    errors: list[str] = []
    try:
        report = require_object(load_json(report_path), "review report")
        if report.get("format_version") != REVIEW_REPORT_VERSION:
            raise ContractError(
                f"review report.format_version must be {REVIEW_REPORT_VERSION!r}"
            )
        current = check_pptx_content(pptx_path)["presentation"]
        presentation = require_object(report.get("presentation"), "review.presentation")
        for key in ("sha256", "slide_count"):
            if presentation.get(key) != current[key]:
                raise ContractError(
                    f"review presentation.{key} does not match the final PPTX"
                )
        review_round = report.get("review_round")
        if (
            not isinstance(review_round, int)
            or isinstance(review_round, bool)
            or review_round < 1
        ):
            raise ContractError("review.review_round must be an integer >= 1")
        checks = require_object(report.get("checks"), "review.checks")
        if set(checks) != REQUIRED_CHECKS:
            raise ContractError(
                "review.checks must contain exactly: " + ", ".join(sorted(REQUIRED_CHECKS))
            )
        for name in sorted(REQUIRED_CHECKS):
            check = require_object(checks[name], f"review.checks.{name}")
            if check.get("status") != "pass":
                raise ContractError(f"review.checks.{name}.status must be pass")
            notes = require_string(check.get("notes"), f"review.checks.{name}.notes")
            if len(notes.strip()) < 20:
                raise ContractError(
                    f"review.checks.{name}.notes must record a substantive review"
                )
        _validate_findings(
            require_list(report.get("findings"), "review.findings"),
            current["slide_count"],
        )
        _validate_renders(report, render_dir, current["slide_count"])
        if report.get("overall_status") != "pass":
            raise ContractError("review.overall_status must be pass")
        resolved_asset_dir = asset_dir or Path(__file__).resolve().parents[1] / "assets"
        logo_hashes = _official_logo_hashes(resolved_asset_dir)
        embedded_logo_count = sum(
            image_hash in logo_hashes
            for image_hash in _first_slide_image_hashes(pptx_path)
        )
        if embedded_logo_count != 1:
            raise ContractError(
                "first slide must embed exactly one unmodified bundled NHI logo asset"
            )
    except (ContractError, OSError) as exc:
        errors.append(str(exc))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--pptx", type=Path, required=True)
    validate.add_argument("--renders", type=Path, required=True)
    validate.add_argument("--report", type=Path, required=True)
    validate.add_argument("--assets", type=Path)
    args = parser.parse_args()
    errors = validate_review_report(args.pptx, args.renders, args.report, args.assets)
    if errors:
        print("SlideReview validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("SlideReview validation PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
