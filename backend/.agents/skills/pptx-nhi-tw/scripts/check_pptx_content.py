"""Inspect PPTX slide text directly from OOXML for release-blocking tripwires."""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from contracts import CONTENT_CHECK_VERSION, ContractError, sha256_file, write_json


DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
TEXT_TAG = f"{{{DRAWING_NS}}}t"
PLACEHOLDER_PATTERNS = (
    ("placeholder_lorem", re.compile(r"\blorem\s+ipsum\b", re.IGNORECASE)),
    ("placeholder_todo", re.compile(r"\b(?:todo|tbd)\b", re.IGNORECASE)),
    ("placeholder_insert", re.compile(r"\[\s*(?:insert|replace|add)\b", re.IGNORECASE)),
    ("placeholder_x", re.compile(r"\bX{3,}\b", re.IGNORECASE)),
    ("placeholder_zh", re.compile(r"(?:示例|範例文字|請輸入|點選以)")),
)
# Deliberately conservative: a tripwire prompts human review; it is not a language detector.
SIMPLIFIED_TRIPWIRE = re.compile(r"[医疗药诊险费对国会现时问题经学个门车东华]")


def _slide_sort_key(name: str) -> tuple[int, str]:
    match = re.search(r"slide(\d+)\.xml$", name)
    return (int(match.group(1)) if match else 0, name)


def _ordered_slide_names(archive: zipfile.ZipFile) -> list[str]:
    all_slide_names = sorted(
        (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=_slide_sort_key,
    )
    if "ppt/presentation.xml" not in archive.namelist() or "ppt/_rels/presentation.xml.rels" not in archive.namelist():
        return all_slide_names
    try:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    except ET.ParseError as exc:
        raise ContractError(f"Invalid presentation XML: {exc}") from exc
    relationship_targets = {
        rel.get("Id"): rel.get("Target")
        for rel in relationships.findall(f"{{{PACKAGE_RELATIONSHIP_NS}}}Relationship")
    }
    slide_ids = presentation.findall(f".//{{{PRESENTATION_NS}}}sldId")
    ordered: list[str] = []
    for slide_id in slide_ids:
        relation_id = slide_id.get(f"{{{RELATIONSHIP_NS}}}id")
        target = relationship_targets.get(relation_id)
        if not target:
            raise ContractError(f"presentation slide relationship is missing: {relation_id}")
        part_name = posixpath.normpath(posixpath.join("ppt", target))
        if part_name not in all_slide_names:
            raise ContractError(f"presentation references a missing slide part: {part_name}")
        ordered.append(part_name)
    return ordered


def extract_slide_text(pptx_path: Path) -> list[tuple[int, str]]:
    if pptx_path.suffix.lower() not in {".pptx", ".potx"}:
        raise ContractError(f"Expected a .pptx or .potx file, got: {pptx_path}")
    try:
        with zipfile.ZipFile(pptx_path) as archive:
            slide_names = _ordered_slide_names(archive)
            if not slide_names:
                raise ContractError("PPTX contains no ppt/slides/slideN.xml parts")
            slides: list[tuple[int, str]] = []
            for position, name in enumerate(slide_names, start=1):
                try:
                    root = ET.fromstring(archive.read(name))
                except ET.ParseError as exc:
                    raise ContractError(f"Invalid XML in {name}: {exc}") from exc
                text = "".join(node.text or "" for node in root.iter(TEXT_TAG))
                slides.append((position, text))
            return slides
    except FileNotFoundError as exc:
        raise ContractError(f"PPTX file not found: {pptx_path}") from exc
    except zipfile.BadZipFile as exc:
        raise ContractError(f"Invalid PPTX ZIP archive: {pptx_path}") from exc


def check_pptx_content(pptx_path: Path) -> dict:
    findings: list[dict[str, object]] = []
    slides = extract_slide_text(pptx_path)
    for slide_number, text in slides:
        for finding_type, pattern in PLACEHOLDER_PATTERNS:
            for match in pattern.finditer(text):
                findings.append({"type": finding_type, "slide_number": slide_number, "match": match.group(0)})
        for match in SIMPLIFIED_TRIPWIRE.finditer(text):
            findings.append({"type": "simplified_chinese_tripwire", "slide_number": slide_number, "match": match.group(0)})
    findings.sort(key=lambda item: (int(item["slide_number"]), str(item["type"]), str(item["match"])))
    return {
        "format_version": CONTENT_CHECK_VERSION,
        "presentation": {
            "path": str(pptx_path),
            "sha256": sha256_file(pptx_path),
            "slide_count": len(slides),
        },
        "status": "pass" if not findings else "fail",
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--output", type=Path, help="Write deterministic JSON to this path")
    parser.add_argument("--fail-on-findings", action="store_true", help="Exit non-zero when a tripwire is found")
    args = parser.parse_args()
    try:
        report = check_pptx_content(args.pptx)
    except ContractError as exc:
        print(f"PPTX content check FAILED: {exc}", file=sys.stderr)
        return 1
    if args.output:
        write_json(args.output, report)
    print(f"PPTX content check {report['status'].upper()}: {report['presentation']['slide_count']} slide(s), {len(report['findings'])} finding(s)")
    return 1 if args.fail_on_findings and report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
