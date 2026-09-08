"""Deterministic validation and snapshotting for generated presentations.

The author is allowed to write the candidate deck and its PNG renders.  This
module is deliberately backend-owned: it reads those files, resolves slide
order from the OOXML relationship graph, and writes the two validator
artifacts atomically.  It never trusts a report written by the author as a
release gate.

The public entry point is :func:`validate_candidate_deck`.  It returns a
typed ``PASS``/``FAIL`` result rather than asking callers to infer success
from a subprocess exit code.  ``validate_deck`` is kept as a small alias for
integrators that prefer a shorter name.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from xml.etree import ElementTree as ET
import platform

from .contracts import JobError

try:  # Pillow is a production dependency, but keep module import lightweight.
    from PIL import Image, ImageChops
except ImportError:  # pragma: no cover - preflight reports the missing package.
    Image = None  # type: ignore[assignment]
    ImageChops = None  # type: ignore[assignment]


VALIDATION_VERSION = "DeckValidation v1"
SNAPSHOT_VERSION = "DeckSnapshot v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

_A = f"{{{DRAWING_NS}}}"
_P = f"{{{PRESENTATION_NS}}}"
_R = f"{{{RELATIONSHIP_NS}}}"
_PR = f"{{{PACKAGE_RELATIONSHIP_NS}}}"
_C = f"{{{CHART_NS}}}"

DEFAULT_LOGO_ASSET_DIR = (
    Path(__file__).resolve().parents[3]
    / ".agents"
    / "skills"
    / "pptx-nhi-tw"
    / "assets"
)
TRUSTED_CONTENT_CHECKER = (
    Path(__file__).resolve().parents[3]
    / ".agents"
    / "skills"
    / "pptx-nhi-tw"
    / "scripts"
    / "check_pptx_content.py"
)
TRUSTED_RENDERER = (
    Path(__file__).resolve().parents[3]
    / ".agents"
    / "skills"
    / "pptx-nhi-tw"
    / "scripts"
    / "office"
    / "soffice.py"
)
TRUSTED_RENDER_DPI = 150
RENDER_FILENAME_RE = re.compile(r"slide-([0-9]+)\.png")
_RENDER_ENV_ALLOWLIST = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SAL_USE_VCLPLUGIN",
    "TZ",
)


class ValidationStatus(str, Enum):
    """The only release-control states produced by the validator."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class ValidationFinding:
    """A deterministic, machine-readable reason a candidate cannot ship."""

    code: str
    message: str
    origin: Literal["candidate", "infrastructure"]
    severity: Literal["blocking", "error"] = "blocking"
    slide_number: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "origin": self.origin,
            "severity": self.severity,
            "message": self.message,
        }
        if self.slide_number is not None:
            result["slide_number"] = self.slide_number
        if self.details:
            result["details"] = dict(self.details)
        return result


@dataclass(frozen=True)
class ValidationResult:
    """Result and backend-owned artifacts for one candidate deck."""

    status: ValidationStatus
    findings: tuple[ValidationFinding, ...]
    pptx_sha256: str | None
    slide_count: int | None
    content_check_path: Path
    deck_snapshot_path: Path
    content_check: Mapping[str, Any]
    deck_snapshot: Mapping[str, Any]

    @property
    def passed(self) -> bool:
        return self.status is ValidationStatus.PASS

    @property
    def failed(self) -> bool:
        return not self.passed

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe coordinator-facing result."""

        return {
            "status": self.status.value,
            "pptx_sha256": self.pptx_sha256,
            "slide_count": self.slide_count,
            "findings": [finding.as_dict() for finding in self.findings],
            "content_check": str(self.content_check_path),
            "deck_snapshot": str(self.deck_snapshot_path),
        }


@dataclass(frozen=True)
class _RenderRecord:
    slide_number: int
    filename: str
    sha256: str
    pixel_sha256: str | None
    width: int | None
    height: int | None
    byte_size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "slide_number": self.slide_number,
            "filename": self.filename,
            "sha256": self.sha256,
            "pixel_sha256": self.pixel_sha256,
            "width": self.width,
            "height": self.height,
            "byte_size": self.byte_size,
        }


@dataclass(frozen=True)
class _PptxPackage:
    """Parsed package data needed by validation and snapshotting."""

    members: tuple[str, ...]
    ordered_slides: tuple[str, ...]
    slide_roots: tuple[ET.Element, ...]
    slide_relationships: tuple[dict[str, tuple[str, str]], ...]


class PptxPackageError(ValueError):
    """A malformed or internally inconsistent OOXML package."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash a file in bounded chunks so large decks do not fill memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(data: bytes, member: str) -> ET.Element:
    try:
        # The standard library parser does not resolve external entities.  A
        # PPTX relationship part is package data, so no external entities are
        # useful here; rejecting DTDs also keeps snapshots deterministic.
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise PptxPackageError(f"external entity declaration in {member}")
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise PptxPackageError(f"invalid XML in {member}: {exc}") from exc


def _resolve_member(source_member: str, target: str) -> str:
    """Resolve an OOXML relationship target and reject path escapes."""

    if not isinstance(target, str) or not target.strip():
        raise PptxPackageError(f"empty relationship target from {source_member}")
    if target.startswith("/"):
        member = posixpath.normpath(target.lstrip("/"))
    else:
        member = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_member), target)
        )
    if member in {"", ".", ".."} or member.startswith("../"):
        raise PptxPackageError(
            f"relationship target escapes package from {source_member}: {target}"
        )
    return member


def _relationships(
    archive: zipfile.ZipFile, source_member: str
) -> dict[str, tuple[str, str]]:
    rels_member = posixpath.join(
        posixpath.dirname(source_member),
        "_rels",
        posixpath.basename(source_member) + ".rels",
    )
    if rels_member not in archive.namelist():
        return {}
    root = _parse_xml(archive.read(rels_member), rels_member)
    result: dict[str, tuple[str, str]] = {}
    for relationship in root.findall(f"{_PR}Relationship"):
        relation_id = relationship.get("Id")
        target = relationship.get("Target")
        relation_type = relationship.get("Type") or ""
        if not relation_id or not target:
            raise PptxPackageError(f"relationship in {rels_member} has no Id/Target")
        if relation_id in result:
            raise PptxPackageError(f"duplicate relationship Id in {rels_member}: {relation_id}")
        # External hyperlinks/media are not package members.  Retain them in
        # the relationship map, but only resolve package targets later.
        if relationship.get("TargetMode") == "External":
            result[relation_id] = (f"__external__:{target}", relation_type)
        else:
            result[relation_id] = (_resolve_member(source_member, target), relation_type)
    return result


def _ordered_slide_names(
    archive: zipfile.ZipFile,
) -> tuple[list[str], dict[str, tuple[str, str]]]:
    names = set(archive.namelist())
    required = {"[Content_Types].xml", "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"}
    missing = sorted(required - names)
    if missing:
        raise PptxPackageError("missing required OOXML part(s): " + ", ".join(missing))
    presentation = _parse_xml(archive.read("ppt/presentation.xml"), "ppt/presentation.xml")
    presentation_relationships = _relationships(archive, "ppt/presentation.xml")
    slide_names: list[str] = []
    slide_ids = presentation.findall(f".//{_P}sldId")
    if not slide_ids:
        raise PptxPackageError("presentation contains no ordered slides")
    for slide_id in slide_ids:
        relation_id = slide_id.get(f"{_R}id")
        if not relation_id or relation_id not in presentation_relationships:
            raise PptxPackageError(
                f"presentation slide relationship is missing: {relation_id or '(empty)'}"
            )
        target, relation_type = presentation_relationships[relation_id]
        if not relation_type.endswith("/slide"):
            raise PptxPackageError(
                f"presentation relationship {relation_id} is not a slide relationship"
            )
        if target not in names:
            raise PptxPackageError(f"presentation references missing slide part: {target}")
        if target in slide_names:
            raise PptxPackageError(f"presentation references slide more than once: {target}")
        slide_names.append(target)
    package_slides = {
        member
        for member in names
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", member)
    }
    unreferenced = sorted(package_slides - set(slide_names))
    if unreferenced:
        raise PptxPackageError(
            "unreferenced slide part(s): " + ", ".join(unreferenced)
        )
    return slide_names, presentation_relationships


def _load_pptx_package(path: Path) -> _PptxPackage:
    if path.is_symlink() or not path.is_file():
        raise PptxPackageError("presentation.pptx is missing or is not a regular file")
    if path.stat().st_size == 0:
        raise PptxPackageError("presentation.pptx is empty")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise PptxPackageError("PPTX contains duplicate ZIP member names")
            if any(name.startswith("/") or "\\" in name for name in names):
                raise PptxPackageError("PPTX contains an invalid ZIP member path")
            content_types = _parse_xml(archive.read("[Content_Types].xml"), "[Content_Types].xml")
            defaults = {
                node.get("Extension"): node.get("ContentType")
                for node in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Default")
            }
            overrides = {
                node.get("PartName"): node.get("ContentType")
                for node in content_types.findall(f"{{{CONTENT_TYPES_NS}}}Override")
            }
            if not any(
                value and "presentationml.presentation" in value
                for value in (*defaults.values(), *overrides.values())
            ):
                raise PptxPackageError("[Content_Types].xml has no presentation content type")
            slide_names, _ = _ordered_slide_names(archive)
            slide_roots: list[ET.Element] = []
            slide_relationships: list[dict[str, tuple[str, str]]] = []
            for member in slide_names:
                root = _parse_xml(archive.read(member), member)
                slide_roots.append(root)
                relationships = _relationships(archive, member)
                for target, relation_type in relationships.values():
                    if relation_type.endswith("/slide"):
                        raise PptxPackageError(f"slide relationship points to another slide: {member}")
                    if (
                        relation_type
                        and not relation_type.endswith("/hyperlink")
                        and not target.startswith("__external__:")
                        and target not in names
                    ):
                        raise PptxPackageError(
                            f"slide relationship points to missing part: {member} -> {target}"
                        )
                slide_relationships.append(relationships)
            # Referenced layout/master/theme parts are not required for the
            # snapshot, but the package must at least contain them when the
            # slide relationship says they exist (checked above).
            return _PptxPackage(
                members=tuple(names),
                ordered_slides=tuple(slide_names),
                slide_roots=tuple(slide_roots),
                slide_relationships=tuple(slide_relationships),
            )
    except FileNotFoundError as exc:
        raise PptxPackageError(f"presentation not found: {path}") from exc
    except zipfile.BadZipFile as exc:
        raise PptxPackageError("presentation is not a valid OOXML ZIP archive") from exc
    except KeyError as exc:
        raise PptxPackageError(f"PPTX is missing required member: {exc.args[0]}") from exc


def _text(root: ET.Element) -> str:
    """Join DrawingML runs while preserving meaningful line breaks."""

    parts: list[str] = []
    for node in root.iter(f"{_A}t"):
        if node.text:
            parts.append(node.text)
    return "".join(parts).strip()


def _paragraph_texts(root: ET.Element) -> list[str]:
    return [value for value in (_text(paragraph) for paragraph in root.findall(f".//{_A}p")) if value]


def _chart_cache_points(node: ET.Element) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for cache in node.findall(f".//{_C}strCache") + node.findall(f".//{_C}numCache"):
        values: list[dict[str, Any]] = []
        for point in cache.findall(f"{_C}pt"):
            value = point.find(f"{_C}v")
            values.append({"index": point.get("idx"), "value": value.text if value is not None else None})
        points.append({"kind": _local_name(cache.tag), "values": values})
    return points


def _chart_snapshot(root: ET.Element, member: str) -> dict[str, Any]:
    series: list[dict[str, Any]] = []
    for index, item in enumerate(root.findall(f".//{_C}ser"), start=1):
        series_title = _text(item.find(f"{_C}tx") or ET.Element("empty"))
        series.append({
            "index": index,
            "title": series_title or None,
            "caches": _chart_cache_points(item),
        })
    chart_title = _text(root.find(f".//{_C}title") or ET.Element("empty"))
    return {
        "part": member,
        "title": chart_title or None,
        "series": series,
        "chart_type_elements": sorted({_local_name(node.tag) for node in root.iter() if node.tag.startswith(_C)}),
    }


def _relationship_part(slide_member: str, relation_target: str) -> str:
    # Relationship targets are already resolved by _relationships; this
    # helper only exists to make image/chart code self-documenting.
    del slide_member
    return relation_target


def _slide_snapshot(
    archive: zipfile.ZipFile,
    member: str,
    root: ET.Element,
    relationships: Mapping[str, tuple[str, str]],
    slide_number: int,
) -> dict[str, Any]:
    text_elements: list[dict[str, Any]] = []
    for shape_index, shape in enumerate(root.findall(f".//{_P}sp"), start=1):
        value = _text(shape)
        if value:
            text_elements.append({"shape_index": shape_index, "text": value})

    tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(root.findall(f".//{_A}graphicData/{_A}tbl") + root.findall(f".//{_A}graphicData/{_P}tbl"), start=1):
        rows: list[list[str]] = []
        for row in table.findall(f"{_A}tr"):
            rows.append([_text(cell) for cell in row.findall(f"{_A}tc")])
        tables.append({"index": table_index, "rows": rows})

    images: list[dict[str, Any]] = []
    for image_index, blip in enumerate(root.findall(f".//{_A}blip"), start=1):
        relation_id = blip.get(f"{_R}embed") or blip.get(f"{_R}link")
        relation = relationships.get(relation_id or "")
        target = relation[0] if relation and relation[1] and relation[1].endswith("/image") else None
        image_bytes = archive.read(target) if target and target in archive.namelist() else None
        images.append({
            "index": image_index,
            "relationship_id": relation_id,
            "target": _relationship_part(member, target) if target else None,
            "sha256": _sha256_bytes(image_bytes) if image_bytes is not None else None,
            "external": bool(relation and relation[1].endswith("/hyperlink")),
        })

    charts: list[dict[str, Any]] = []
    for relation_id, (target, relation_type) in relationships.items():
        if not relation_type.endswith("/chart") or target not in archive.namelist():
            continue
        charts.append(_chart_snapshot(_parse_xml(archive.read(target), target), target) | {"relationship_id": relation_id})

    notes: list[str] = []
    note_parts: list[str] = []
    for target, relation_type in relationships.values():
        if relation_type.endswith("/notesSlide") and target in archive.namelist():
            note_parts.extend(_paragraph_texts(_parse_xml(archive.read(target), target)))
    notes.extend(note_parts)
    return {
        "slide_number": slide_number,
        "part": member,
        "text": text_elements,
        "tables": tables,
        "charts": charts,
        "notes": notes,
        "images": images,
    }


def build_deck_snapshot(
    pptx_path: Path,
    *,
    render_records: Sequence[Mapping[str, Any]] = (),
    trusted_render_records: Sequence[Mapping[str, Any]] = (),
    evidence_sha256: str | None = None,
) -> dict[str, Any]:
    """Extract the visible OOXML content in presentation order.

    Snapshotting charts records cached values exactly as stored in the OOXML
    package.  It intentionally does not calculate formulas or perform OCR;
    those limitations are made explicit for the semantic reviewer.
    """

    package = _load_pptx_package(pptx_path)
    with zipfile.ZipFile(pptx_path) as archive:
        slides = [
            _slide_snapshot(archive, member, root, relationships, number)
            for number, (member, root, relationships) in enumerate(
                zip(package.ordered_slides, package.slide_roots, package.slide_relationships),
                start=1,
            )
        ]
    return {
        "format_version": SNAPSHOT_VERSION,
        "status": "available",
        "artifact_binding": {
            "pptx_sha256": sha256_file(pptx_path),
            "evidence_sha256": evidence_sha256,
            "render_sha256": [str(item.get("sha256")) for item in render_records],
            "trusted_render_sha256": [
                str(item.get("sha256")) for item in trusted_render_records
            ],
        },
        "slide_count": len(slides),
        "slides": slides,
        "renders": [dict(item) for item in render_records],
        "trusted_renders": [dict(item) for item in trusted_render_records],
        "limitations": [
            "Raster images are recorded by package path and hash; image-only claims are not OCRed.",
            "Chart values are the stored OOXML caches; formulas are not recalculated.",
            "SmartArt, OLE objects, media, animations, and transitions are not semantically interpreted.",
            "The semantic reviewer must use the renders for content that is not represented as visible OOXML text.",
        ],
    }


def _unavailable_snapshot(
    pptx_sha256: str | None,
    evidence_sha256: str | None,
    error: str,
) -> dict[str, Any]:
    return {
        "format_version": SNAPSHOT_VERSION,
        "status": "unavailable",
        "artifact_binding": {
            "pptx_sha256": pptx_sha256,
            "evidence_sha256": evidence_sha256,
            "render_sha256": [],
            "trusted_render_sha256": [],
        },
        "slide_count": None,
        "slides": [],
        "renders": [],
        "limitations": ["Snapshot extraction did not complete."],
        "errors": [error],
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write an artifact through a sibling temporary file and atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _render_sort_key(path: Path) -> tuple[int, str]:
    match = RENDER_FILENAME_RE.fullmatch(path.name)
    if not match:
        return (0, path.name)
    try:
        return (int(match.group(1)), path.name)
    except ValueError:
        return (0, path.name)


def _pixel_digest(image: Any) -> str:
    rgba = image.convert("RGBA")
    return _sha256_bytes(rgba.tobytes())


def _is_solid(image: Any) -> bool:
    rgb = image.convert("RGB")
    if rgb.width == 0 or rgb.height == 0:
        return True
    # ImageChops avoids materializing a Python tuple for every pixel.
    difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, rgb.getpixel((0, 0))))
    # ``getbbox`` on RGBA only considered alpha on older Pillow versions;
    # checking the RGB channels keeps the blank-render tripwire reliable.
    return all(channel.getbbox() is None for channel in difference.split())


def _validate_renders(
    render_dir: Path,
    slide_count: int,
) -> tuple[list[ValidationFinding], list[_RenderRecord]]:
    findings: list[ValidationFinding] = []
    if render_dir.is_symlink() or not render_dir.is_dir():
        return [
            ValidationFinding(
                "renders_missing",
                f"render directory is missing or is not a regular directory: {render_dir}",
                details={"path": str(render_dir)},
                origin="candidate",
            )
        ], []

    # Iterate direct children rather than globbing.  A final-render stage is a
    # strict boundary: malformed names, nested directories, and symlinks must
    # not be silently ignored or followed into another stage.
    entries = sorted(render_dir.iterdir(), key=lambda path: path.name)
    parsed: dict[int, list[Path]] = {}
    for path in entries:
        match = RENDER_FILENAME_RE.fullmatch(path.name)
        if match is None:
            findings.append(
                ValidationFinding(
                    "render_malformed_name",
                    "render entry does not use the slide-<number>.png naming scheme",
                    details={"file": path.name},
                    origin="candidate",
                )
            )
            continue
        try:
            number = int(match.group(1))
        except ValueError:
            findings.append(
                ValidationFinding(
                    "render_out_of_range",
                    "render filename contains an unrepresentable numeric slide ID",
                    details={"file": path.name, "digits": len(match.group(1))},
                    origin="candidate",
                )
            )
            continue
        parsed.setdefault(number, []).append(path)

    expected_ids = set(range(1, slide_count + 1))
    actual_ids = set(parsed)
    out_of_range = sorted(actual_ids - expected_ids)
    if out_of_range:
        findings.append(
            ValidationFinding(
                "render_out_of_range",
                "render filename contains a slide number outside the PPTX slide range",
                details={
                    "slide_numbers": out_of_range,
                    "files": [path.name for number in out_of_range for path in parsed[number]],
                },
                origin="candidate",
            )
        )
    duplicate_ids = sorted(number for number, paths in parsed.items() if len(paths) > 1)
    for number in duplicate_ids:
        findings.append(
            ValidationFinding(
                "render_duplicate_id",
                "multiple PNG filenames resolve to the same numeric slide ID",
                slide_number=number if number in expected_ids else None,
                details={"slide_number": number, "files": [path.name for path in parsed[number]]},
                origin="candidate",
            )
        )
    missing_ids = sorted(expected_ids - actual_ids)
    if missing_ids:
        findings.append(
            ValidationFinding(
                "render_missing",
                "one or more slide PNG renders are missing",
                details={"slide_numbers": missing_ids, "files": [f"slide-{number}.png" for number in missing_ids]},
                origin="candidate",
            )
        )
    records: list[_RenderRecord] = []
    pixel_hashes: list[str] = []
    dimensions: set[tuple[int, int]] = set()
    successful_by_slide: dict[int, list[str]] = {}
    for number, paths in sorted(parsed.items()):
        for path in paths:
            if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                findings.append(
                    ValidationFinding(
                        "render_non_regular",
                        "render must be a regular, non-symlink file",
                        slide_number=number if number in expected_ids else None,
                        details={"file": path.name, "kind": "symlink" if path.is_symlink() else "directory_or_non_regular"},
                        origin="candidate",
                    )
                )
                continue
            if number < 1 or number > slide_count:
                continue
            try:
                raw = path.read_bytes()
            except Exception as exc:
                findings.append(
                    ValidationFinding(
                        "render_read_error",
                        f"PNG render could not be read: {path.name}",
                        slide_number=number,
                        details={"error": str(exc)},
                        origin="candidate",
                    )
                )
                continue
            if len(raw) <= len(PNG_SIGNATURE) or raw[: len(PNG_SIGNATURE)] != PNG_SIGNATURE:
                findings.append(
                    ValidationFinding(
                        "render_invalid_png",
                        f"invalid PNG render: {path.name}",
                        slide_number=number,
                        origin="candidate",
                    )
                )
                continue
            if Image is None:
                findings.append(
                    ValidationFinding(
                        "renderer_dependency_missing",
                        "Pillow is required to decode PNG renders",
                        origin="infrastructure",
                    )
                )
                continue
            try:
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    image.load()
                    if image.format != "PNG":
                        raise ValueError(f"decoded format is {image.format or 'unknown'}, not PNG")
                    width, height = image.size
                    if width < 2 or height < 2:
                        findings.append(
                            ValidationFinding(
                                "render_invalid_dimensions",
                                f"render has invalid dimensions: {path.name}",
                                slide_number=number,
                                details={"width": width, "height": height},
                                origin="candidate",
                            )
                        )
                    if _is_solid(image):
                        findings.append(
                            ValidationFinding(
                                "render_blank",
                                f"render is a solid blank image: {path.name}",
                                slide_number=number,
                                origin="candidate",
                            )
                        )
                    pixel_hash = _pixel_digest(image)
                    pixel_hashes.append(pixel_hash)
                    dimensions.add((width, height))
                    successful_by_slide.setdefault(number, []).append(path.name)
                    records.append(_RenderRecord(number, path.name, _sha256_bytes(raw), pixel_hash, width, height, len(raw)))
            except Exception as exc:
                findings.append(
                    ValidationFinding(
                        "render_decode_error",
                        f"PNG render could not be decoded: {path.name}",
                        slide_number=number,
                        details={"error": str(exc)},
                        origin="candidate",
                    )
                )
    incomplete_ids = sorted(expected_ids - set(successful_by_slide))
    if incomplete_ids:
        findings.append(
            ValidationFinding(
                "render_incomplete",
                "each expected slide must have exactly one successfully decoded regular PNG",
                details={
                    "expected_slide_numbers": sorted(expected_ids),
                    "decoded_slide_numbers": sorted(successful_by_slide),
                    "missing_or_undecoded": incomplete_ids,
                },
                origin="candidate",
            )
        )
    if any(len(names) != 1 for names in successful_by_slide.values()):
        findings.append(
            ValidationFinding(
                "render_decoded_duplicate",
                "each expected slide must have exactly one successfully decoded PNG",
                details={"decoded_files": successful_by_slide},
                origin="candidate",
            )
        )
    if len(dimensions) > 1:
        findings.append(ValidationFinding("render_dimensions_mismatch", "slide renders do not share dimensions", details={"dimensions": sorted(dimensions)}, origin="candidate"))
    if slide_count > 1 and len(pixel_hashes) == slide_count and len(set(pixel_hashes)) == 1:
        findings.append(ValidationFinding("render_all_identical", "all slide renders contain identical decoded pixels", origin="candidate"))
    records.sort(key=lambda item: item.slide_number)
    return findings, records


def _is_regular_file(path: Path) -> bool:
    """Return true only for an ordinary non-symlink filesystem file."""

    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
        )
    except OSError:
        return False


def _canonicalize_pdftoppm_output(source_dir: Path, destination_dir: Path) -> Path:
    """Copy pdftoppm output into a strict, unpadded slide-name directory.

    ``pdftoppm`` normally emits ``slide-1.png`` but its output naming can be
    influenced by the input/prefix.  Numeric IDs are intentionally parsed
    rather than string-compared, then copied to a fresh directory using one
    canonical name per page.  Padded/unpadded collisions are rejected before
    any output is accepted by the validator.
    """

    if source_dir.is_symlink() or not source_dir.is_dir():
        raise RuntimeError(f"pdftoppm output directory is missing: {source_dir}")
    if destination_dir.exists() or destination_dir.is_symlink():
        raise RuntimeError(f"canonical render directory is not fresh: {destination_dir}")
    destination_dir.mkdir(parents=True, exist_ok=False)
    by_number: dict[int, Path] = {}
    try:
        for path in sorted(source_dir.iterdir(), key=lambda item: item.name):
            match = RENDER_FILENAME_RE.fullmatch(path.name)
            if match is None:
                raise RuntimeError(f"pdftoppm emitted a malformed PNG name: {path.name}")
            if not _is_regular_file(path):
                raise RuntimeError(f"pdftoppm emitted a non-regular PNG: {path.name}")
            number = int(match.group(1))
            previous = by_number.get(number)
            if previous is not None:
                raise RuntimeError(
                    "pdftoppm emitted colliding numeric slide IDs: "
                    f"{previous.name}, {path.name}"
                )
            by_number[number] = path
        for number, source in sorted(by_number.items()):
            target = destination_dir / f"slide-{number}.png"
            if target.exists() or target.is_symlink():
                raise RuntimeError(f"canonical render path collision: {target.name}")
            shutil.copyfile(source, target, follow_symlinks=False)
        return destination_dir
    except Exception:
        shutil.rmtree(destination_dir, ignore_errors=True)
        raise


def _tool_version(executable: str, *, version_args: Sequence[str]) -> str | None:
    """Read a short tool version without capturing arbitrary environment data."""

    resolved = shutil.which(executable)
    if not resolved:
        return None
    try:
        result = subprocess.run(
            [resolved, *version_args],
            capture_output=True,
            text=True,
            timeout=10,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return output[0][:200] if output else None


def _allowlisted_render_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in _RENDER_ENV_ALLOWLIST
        if key in os.environ
    }


def _reset_render_diagnostics(path: Path) -> None:
    """Remove only the job-local, latest-only diagnostics directory."""

    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_regular_file(source: Path, target: Path) -> None:
    if not _is_regular_file(source):
        raise OSError(f"refusing to copy non-regular render artifact: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target, follow_symlinks=False)


def _write_render_diagnostics(
    diagnostics_dir: Path,
    deck: Path,
    submitted_dir: Path,
    trusted_dir: Path,
    submitted_records: Sequence[_RenderRecord],
    trusted_records: Sequence[_RenderRecord],
    findings: Sequence[ValidationFinding],
    render_context: Mapping[str, Any],
) -> None:
    """Persist a bounded, secret-free snapshot of a trusted-render mismatch."""

    _reset_render_diagnostics(diagnostics_dir)
    staging = diagnostics_dir.with_name(f".{diagnostics_dir.name}.{uuid4().hex}.tmp")
    staging.mkdir(parents=True, exist_ok=False)
    try:
        if _is_regular_file(deck):
            _copy_regular_file(deck, staging / "presentation.pptx")
        for record in submitted_records:
            _copy_regular_file(submitted_dir / record.filename, staging / "submitted" / record.filename)
        for record in trusted_records:
            _copy_regular_file(trusted_dir / record.filename, staging / "trusted" / record.filename)
        author_metadata_path = submitted_dir.parent / "render_metadata.json"
        author_metadata: Mapping[str, Any] | None = None
        if _is_regular_file(author_metadata_path) and author_metadata_path.stat().st_size <= 64 * 1024:
            try:
                loaded = json.loads(author_metadata_path.read_text(encoding="utf-8"))
                author_metadata = loaded if isinstance(loaded, dict) else None
            except (OSError, UnicodeError, ValueError):
                author_metadata = None
        payload = {
            "format_version": "RenderDiagnostics v1",
            "reason": "submitted render does not match the independent trusted render",
            "presentation": {
                "filename": "presentation.pptx",
                "sha256": sha256_file(deck) if _is_regular_file(deck) else None,
            },
            "submitted_renders": [record.as_dict() for record in submitted_records],
            "trusted_renders": [record.as_dict() for record in trusted_records],
            "findings": [finding.as_dict() for finding in findings],
            "tools": {
                **dict(render_context.get("tools", {})),
                "python": {
                    "executable": Path(sys.executable).name,
                    "version": platform.python_version(),
                },
            },
            "render_env": _allowlisted_render_env(),
            # Author-owned and diagnostic only. It never affects validation.
            "submitted_render_metadata": author_metadata,
        }
        _write_json_atomic(staging / "diagnostics.json", payload)
        os.replace(staging, diagnostics_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _default_trusted_renderer(
    deck: Path,
    output_dir: Path,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> Path:
    """Render a deck using the backend's fixed LibreOffice/PDF toolchain.

    The author render directory is never passed to this function.  The
    temporary output directory is fresh and receives canonical
    ``slide-<number>.png`` files from ``pdftoppm``.  Keeping this command path
    backend-owned prevents a job-local skill from substituting a renderer.
    """

    if not TRUSTED_RENDERER.is_file():
        raise FileNotFoundError(f"trusted renderer is missing: {TRUSTED_RENDERER}")
    if shutil.which("pdftoppm") is None:
        raise FileNotFoundError("pdftoppm is required by the trusted renderer")
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = output_dir / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    ppm_dir = output_dir / "pdftoppm"
    ppm_dir.mkdir(parents=True, exist_ok=False)
    canonical_dir = output_dir / "canonical"
    office_args = [
        sys.executable,
        str(TRUSTED_RENDERER),
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(pdf_dir),
        str(deck),
    ]
    try:
        office_result = command_runner(
            office_args,
            cwd=deck.parent,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"trusted LibreOffice renderer could not run: {exc}") from exc
    if office_result.returncode:
        details = (getattr(office_result, "stderr", "") or getattr(office_result, "stdout", "") or "").strip()
        raise RuntimeError(f"trusted LibreOffice renderer failed: {details or 'no details'}")
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if len(pdfs) != 1:
        raise RuntimeError(f"trusted LibreOffice renderer produced {len(pdfs)} PDF files")
    image_args = [
        "pdftoppm",
        "-png",
        "-r",
        str(TRUSTED_RENDER_DPI),
        str(pdfs[0]),
        str(ppm_dir / "slide"),
    ]
    try:
        image_result = command_runner(
            image_args,
            cwd=deck.parent,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"trusted PNG renderer could not run: {exc}") from exc
    if image_result.returncode:
        details = (getattr(image_result, "stderr", "") or getattr(image_result, "stdout", "") or "").strip()
        raise RuntimeError(f"trusted PNG renderer failed: {details or 'no details'}")
    return _canonicalize_pdftoppm_output(ppm_dir, canonical_dir)


def _validate_against_trusted_renders(
    deck: Path,
    submitted_records: Sequence[_RenderRecord],
    slide_count: int,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
    trusted_render: Callable[[Path, Path], Any] | None,
    submitted_dir: Path | None = None,
    diagnostics_dir: Path | None = None,
) -> tuple[list[ValidationFinding], list[_RenderRecord]]:
    """Independently render and compare decoded pixels slide by slide."""

    findings: list[ValidationFinding] = []
    submitted_by_slide = {record.slide_number: record for record in submitted_records}
    render_context: dict[str, Any] = {
        "tools": {
            "trusted_renderer": {
                "path": TRUSTED_RENDERER.name,
                "args": ["--headless", "--convert-to", "pdf", "--outdir", "<temporary>", "<deck>"],
            },
            "pdftoppm": {
                "args": ["-png", "-r", str(TRUSTED_RENDER_DPI), "<pdf>", "<temporary>/slide"],
            },
        }
    }
    with tempfile.TemporaryDirectory(prefix="slides_validator_trusted_render_") as temporary:
        output_dir = Path(temporary)
        try:
            if trusted_render is None:
                trusted_dir = _default_trusted_renderer(deck, output_dir, command_runner)
            else:
                returned = trusted_render(deck, output_dir)
                if isinstance(returned, (str, Path)):
                    returned_path = Path(returned)
                    trusted_dir = returned_path if returned_path.is_dir() else returned_path.parent
                else:
                    trusted_dir = output_dir
                render_context["tools"]["trusted_renderer"] = {
                    "kind": "injected",
                    "callable": getattr(trusted_render, "__qualname__", repr(trusted_render))[:200],
                }
        except Exception as exc:
            findings.append(
                ValidationFinding(
                    "trusted_renderer_error",
                    "backend trusted renderer could not independently render the deck",
                    details={"error": str(exc)},
                    origin="infrastructure",
                )
            )
            return findings, []
        trusted_findings, trusted_records = _validate_renders(trusted_dir, slide_count)
        # Retain renderer validity findings, but prefix their codes so the
        # coordinator can distinguish submitted-artifact defects from a
        # trusted-renderer/toolchain defect.
        findings.extend(
            ValidationFinding(
                f"trusted_{finding.code}",
                finding.message,
                "infrastructure",
                finding.severity,
                finding.slide_number,
                finding.details,
            )
            for finding in trusted_findings
        )
        trusted_by_slide = {record.slide_number: record for record in trusted_records}
        for slide_number in range(1, slide_count + 1):
            submitted = submitted_by_slide.get(slide_number)
            trusted = trusted_by_slide.get(slide_number)
            if submitted is None or trusted is None:
                continue
            if (
                submitted.pixel_sha256 != trusted.pixel_sha256
                or (submitted.width, submitted.height)
                != (trusted.width, trusted.height)
            ):
                findings.append(
                    ValidationFinding(
                        "render_stale_or_mismatch",
                        "submitted render does not match the independent trusted render",
                        slide_number=slide_number,
                        details={
                            "submitted_pixel_sha256": submitted.pixel_sha256,
                            "trusted_pixel_sha256": trusted.pixel_sha256,
                            "submitted_dimensions": [submitted.width, submitted.height],
                            "trusted_dimensions": [trusted.width, trusted.height],
                        },
                        origin="candidate",
                    )
                )
        if diagnostics_dir is not None and submitted_dir is not None and any(
            finding.code == "render_stale_or_mismatch" for finding in findings
        ):
            try:
                if trusted_render is None:
                    render_context["tools"]["trusted_renderer"]["version"] = _tool_version("soffice", version_args=("--version",))
                    render_context["tools"]["pdftoppm"]["version"] = _tool_version("pdftoppm", version_args=("-v",))
                _write_render_diagnostics(
                    diagnostics_dir,
                    deck,
                    submitted_dir,
                    trusted_dir,
                    submitted_records,
                    trusted_records,
                    findings,
                    render_context,
                )
            except Exception as exc:
                findings.append(
                    ValidationFinding(
                        "render_diagnostics_error",
                        "trusted-render mismatch diagnostics could not be written",
                        details={"error": str(exc)},
                        origin="infrastructure",
                    )
                )
        return findings, trusted_records


def _logo_image_parts(
    pptx_path: Path,
    package: _PptxPackage,
    logo_dir: Path,
) -> tuple[list[ValidationFinding], list[str]]:
    findings: list[ValidationFinding] = []
    expected_paths = [logo_dir / "nhi_logo_large.png", logo_dir / "nhi_logo_small.png"]
    missing = [str(path) for path in expected_paths if not path.is_file()]
    if missing:
        return [ValidationFinding("logo_assets_missing", "official logo asset(s) are unavailable", details={"files": missing}, origin="infrastructure")], []
    logo_hashes = [_sha256_bytes(path.read_bytes()) for path in expected_paths]
    with zipfile.ZipFile(pptx_path) as archive:
        root = package.slide_roots[0]
        relationships = package.slide_relationships[0]
        matches: list[tuple[str, str, ET.Element]] = []
        for blip in root.findall(f".//{_A}blip"):
            relation_id = blip.get(f"{_R}embed")
            relation = relationships.get(relation_id or "")
            if not relation or not relation[1].endswith("/image") or relation[0] not in archive.namelist():
                continue
            target = relation[0]
            image_hash = _sha256_bytes(archive.read(target))
            if image_hash in logo_hashes:
                matches.append((target, image_hash, blip))
        if len(matches) != 1:
            findings.append(ValidationFinding("logo_count", "first slide must embed exactly one unmodified official NHI logo", slide_number=1, details={"count": len(matches)}, origin="candidate"))
            return findings, logo_hashes
        target, _, blip = matches[0]
        # A srcRect means the source image is cropped.  The hash check above
        # catches pixel edits; this catches a visually altered crop.
        picture = next(
            (
                ancestor
                for ancestor in root.findall(f".//{_P}pic")
                if any(node is blip for node in ancestor.iter())
            ),
            None,
        )
        crop = picture.find(f".//{_A}srcRect") if picture is not None else None
        if crop is not None and any(int(crop.get(edge, "0")) != 0 for edge in ("l", "t", "r", "b")):
            findings.append(ValidationFinding("logo_cropped", "official logo is cropped on the cover", slide_number=1, details={"target": target}, origin="candidate"))
        if Image is not None:
            asset_path = expected_paths[logo_hashes.index(_sha256_bytes(archive.read(target)))]
            try:
                with Image.open(asset_path) as image:
                    native_ratio = image.width / image.height
                # p:pic/p:spPr/a:xfrm/a:ext describes the displayed bounds.
                for pic in root.findall(f".//{_P}pic"):
                    if not any(node is blip for node in pic.iter()):
                        continue
                    ext = pic.find(f"{_P}spPr/{_A}xfrm/{_A}ext")
                    if ext is None:
                        break
                    cx, cy = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
                    if cx > 0 and cy > 0 and abs((cx / cy) - native_ratio) > max(native_ratio * 0.02, 0.01):
                        findings.append(ValidationFinding("logo_aspect_ratio", "official logo aspect ratio was changed", slide_number=1, details={"target": target, "native_ratio": native_ratio, "displayed_ratio": cx / cy}, origin="candidate"))
                    break
            except Exception as exc:
                findings.append(ValidationFinding("logo_asset_unreadable", "official logo asset could not be inspected", slide_number=1, details={"error": str(exc)}, origin="infrastructure"))
    return findings, logo_hashes


def _title_present(snapshot: Mapping[str, Any], requested_title: str) -> bool:
    normalized = " ".join(requested_title.split())
    first = snapshot.get("slides", [{}])[0] if snapshot.get("slides") else {}
    visible_shapes = [
        " ".join(str(item.get("text", "")).split())
        for item in first.get("text", [])
        if isinstance(item, Mapping)
    ]
    return normalized in visible_shapes


def _load_trusted_checker(path: Path = TRUSTED_CONTENT_CHECKER) -> Callable[[Path], dict[str, Any]]:
    """Load the repository-owned content checker, never one from the job.

    The checker is a script because the PPTX skill also exposes it to local
    author tooling.  Importing that exact file keeps the backend gate tied to
    the trusted checkout while still allowing unit tests to inject a checker.
    """

    if not path.is_file():
        raise FileNotFoundError(f"trusted content checker is missing: {path}")
    module_name = "_trusted_slides_content_checker"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load trusted content checker: {path}")
    module = importlib.util.module_from_spec(spec)
    module_dir = str(path.parent)
    old_contracts = sys.modules.get("contracts")
    sys.path.insert(0, module_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(module_dir)
        if old_contracts is None:
            sys.modules.pop("contracts", None)
        else:
            sys.modules["contracts"] = old_contracts
    checker = getattr(module, "check_pptx_content", None)
    if not callable(checker):
        raise AttributeError("trusted content checker has no check_pptx_content function")
    return checker


def _run_content_checker(
    pptx_path: Path,
    checker: Callable[[Path], Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], ValidationFinding | None]:
    try:
        selected = checker or _load_trusted_checker()
        report = dict(selected(pptx_path))
        report.setdefault("format_version", "trusted-content-checker")
        report["validator_binding"] = {"pptx_sha256": sha256_file(pptx_path)}
        if str(report.get("status", "pass")).lower() != "pass" or report.get("findings"):
            return report, ValidationFinding(
                "content_check_failed",
                "trusted deterministic PPTX content checker found problems",
                details={
                    "finding_count": len(report.get("findings", [])) if isinstance(report.get("findings"), list) else None,
                    "content_findings": report.get("findings", []),
                },
                origin="candidate",
            )
        return report, None
    except Exception as exc:
        report = {
            "format_version": "trusted-content-checker",
            "status": "fail",
            "findings": [],
            "validator_binding": {"pptx_sha256": sha256_file(pptx_path)},
            "error": str(exc),
        }
        return report, ValidationFinding("content_checker_error", "trusted deterministic content checker could not run", details={"error": str(exc)}, origin="infrastructure")


def _check_office(
    deck: Path,
    slide_count: int,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
    office_runner: Callable[[Path, Path], Any] | None,
) -> ValidationFinding | None:
    """Open and convert the deck with LibreOffice, using an injectable runner."""

    if office_runner is not None:
        try:
            with tempfile.TemporaryDirectory(prefix="slides_validator_office_") as temporary:
                output_dir = Path(temporary)
                returned = office_runner(deck, output_dir)
                if isinstance(returned, (str, Path)):
                    pdf_candidates = [Path(returned)]
                else:
                    pdf_candidates = sorted(output_dir.glob("*.pdf"))
                return _check_converted_pdf(pdf_candidates, slide_count)
        except Exception as exc:
            return ValidationFinding("libreoffice_error", "LibreOffice could not open or render the deck", details={"error": str(exc)}, origin="infrastructure")
    if shutil.which("soffice") is None:
        return ValidationFinding("libreoffice_unavailable", "LibreOffice is required to validate the candidate deck", origin="infrastructure")
    try:
        with tempfile.TemporaryDirectory(prefix="slides_validator_office_") as temporary:
            output_dir = Path(temporary)
            profile = (output_dir / "profile").resolve().as_uri()
            result = command_runner(
                ["soffice", "--headless", f"-env:UserInstallation={profile}", "--convert-to", "pdf", "--outdir", str(output_dir), str(deck)],
                cwd=deck.parent,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode:
                details = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
                return ValidationFinding("libreoffice_failed", "LibreOffice failed to render the deck", details={"error": details or "no details"}, origin="infrastructure")
            return _check_converted_pdf(sorted(output_dir.glob("*.pdf")), slide_count)
    except (OSError, subprocess.SubprocessError) as exc:
        return ValidationFinding("libreoffice_error", "LibreOffice could not be executed", details={"error": str(exc)}, origin="infrastructure")


def _check_converted_pdf(pdf_candidates: Sequence[Path], slide_count: int) -> ValidationFinding | None:
    if len(pdf_candidates) != 1 or not pdf_candidates[0].is_file() or pdf_candidates[0].stat().st_size == 0:
        return ValidationFinding("libreoffice_no_pdf", "LibreOffice produced no unambiguous PDF render", origin="infrastructure")
    try:
        from pypdf import PdfReader

        page_count = len(PdfReader(str(pdf_candidates[0])).pages)
    except Exception as exc:
        return ValidationFinding("libreoffice_invalid_pdf", "LibreOffice produced an unreadable PDF", details={"error": str(exc)}, origin="infrastructure")
    if page_count != slide_count:
        # LibreOffice successfully rendered the submitted deck, but the
        # resulting page count disagrees with its package structure. The
        # candidate must be corrected; retrying the renderer alone cannot
        # repair that inconsistency.
        return ValidationFinding("libreoffice_slide_count", "LibreOffice render page count does not match PPTX slide count", details={"pages": page_count, "slides": slide_count}, origin="candidate")
    return None


def validate_candidate_deck(
    job_dir: Path,
    *,
    expected_slide_count: int | None = None,
    requested_title: str | None = None,
    evidence_path: Path | None = None,
    logo_dir: Path | None = DEFAULT_LOGO_ASSET_DIR,
    render_dir: Path | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    office_runner: Callable[[Path, Path], Any] | None = None,
    content_checker: Callable[[Path], Mapping[str, Any]] | None = None,
    trusted_render: Callable[[Path, Path], Any] | None = None,
    require_logo: bool = True,
    check_libreoffice: bool = True,
    check_trusted_renders: bool = True,
) -> ValidationResult:
    """Validate one candidate and atomically produce validator artifacts.

    ``office_runner`` receives ``(deck_path, temporary_output_directory)`` and
    may either write exactly one PDF there or return its path.  This seam is
    useful for tests and lets deployments route through the repository's
    hardened LibreOffice helper.  ``content_checker`` is similarly injectable
    but defaults to the trusted backend checkout.

    ``trusted_render`` receives ``(deck_path, temporary_output_directory)``
    and must write canonical ``slide-<number>.png`` files there (or return a
    directory containing them).  When omitted, the backend's bundled
    LibreOffice plus ``pdftoppm`` renderer is used.  Set
    ``check_trusted_renders=False`` only in focused unit tests that cannot
    provide the production rendering toolchain.
    """

    job_dir = Path(job_dir)
    deck = job_dir / "output" / "presentation.pptx"
    renders = Path(render_dir) if render_dir is not None else job_dir / "work" / "rendered" / "final"
    evidence = Path(evidence_path) if evidence_path is not None else job_dir / "work" / "evidence.json"
    artifact_dir = job_dir / "work" / "intermediate"
    content_path = artifact_dir / "content_check.json"
    snapshot_path = artifact_dir / "deck_snapshot.json"
    diagnostics_path = artifact_dir / "render_diagnostics"
    # Diagnostics are a latest-only aid for an independently observed render
    # mismatch.  Remove a prior bundle before this attempt so a successful
    # validation cannot leave stale evidence attached to a new candidate.
    _reset_render_diagnostics(diagnostics_path)
    findings: list[ValidationFinding] = []
    if runner is not None:
        # ``runner`` is the spelling used by the older artifacts helper.  It
        # is accepted here so the coordinator can migrate without an adapter
        # shim while ``command_runner`` remains explicit in new code.
        command_runner = runner
    pptx_sha256: str | None = None
    slide_count: int | None = None
    evidence_sha256: str | None = None
    if evidence.is_file() and not evidence.is_symlink():
        evidence_sha256 = sha256_file(evidence)
    if deck.is_file() and not deck.is_symlink():
        pptx_sha256 = sha256_file(deck)

    package: _PptxPackage | None = None
    try:
        package = _load_pptx_package(deck)
        slide_count = len(package.ordered_slides)
        if expected_slide_count is not None and slide_count != expected_slide_count:
            findings.append(ValidationFinding("slide_count", "PPTX slide count does not match the requested count", details={"expected": expected_slide_count, "actual": slide_count}, origin="candidate"))
    except PptxPackageError as exc:
        findings.append(ValidationFinding("pptx_invalid", str(exc), origin="candidate"))

    render_records: list[_RenderRecord] = []
    trusted_render_records: list[_RenderRecord] = []
    if slide_count is not None:
        render_findings, render_records = _validate_renders(renders, slide_count)
        findings.extend(render_findings)
    else:
        findings.append(ValidationFinding("renders_unchecked", "renders could not be checked because the PPTX is invalid", origin="candidate"))

    content_report: dict[str, Any]
    if deck.is_file() and not deck.is_symlink():
        content_report, content_finding = _run_content_checker(deck, content_checker)
        if content_finding:
            findings.append(content_finding)
    else:
        content_report = {
            "format_version": "trusted-content-checker",
            "status": "fail",
            "findings": [],
            "error": "presentation.pptx is missing",
            "validator_binding": {"pptx_sha256": None},
        }
        findings.append(ValidationFinding("pptx_missing", "presentation.pptx is missing", origin="candidate"))

    if package is not None and require_logo:
        findings.extend(_logo_image_parts(deck, package, Path(logo_dir) if logo_dir else DEFAULT_LOGO_ASSET_DIR)[0])
    if package is not None and requested_title:
        try:
            provisional_snapshot = build_deck_snapshot(
                deck,
                render_records=[record.as_dict() for record in render_records],
                evidence_sha256=evidence_sha256,
            )
            if not _title_present(provisional_snapshot, requested_title):
                findings.append(ValidationFinding("title_missing", "requested title is not visible on the cover slide", slide_number=1, details={"requested_title": requested_title}, origin="candidate"))
        except Exception:
            # The canonical snapshot below records the parse error; avoid a
            # duplicate title failure when the package itself is malformed.
            pass

    if package is not None and check_libreoffice and not any(f.code == "pptx_invalid" for f in findings):
        office_finding = _check_office(deck, slide_count or 0, command_runner=command_runner, office_runner=office_runner)
        if office_finding:
            findings.append(office_finding)

    if package is not None and check_trusted_renders:
        trusted_findings, trusted_render_records = _validate_against_trusted_renders(
            deck,
            render_records,
            slide_count or 0,
            command_runner=command_runner,
            trusted_render=trusted_render,
            submitted_dir=renders,
            diagnostics_dir=diagnostics_path,
        )
        findings.extend(trusted_findings)

    if package is not None:
        try:
            snapshot = build_deck_snapshot(
                deck,
                render_records=[record.as_dict() for record in render_records],
                trusted_render_records=[record.as_dict() for record in trusted_render_records],
                evidence_sha256=evidence_sha256,
            )
        except Exception as exc:
            snapshot = _unavailable_snapshot(pptx_sha256, evidence_sha256, str(exc))
            findings.append(ValidationFinding("snapshot_error", "deck snapshot could not be generated", details={"error": str(exc)}, origin="infrastructure"))
    else:
        snapshot = _unavailable_snapshot(pptx_sha256, evidence_sha256, "PPTX package validation failed")

    content_report["validator_binding"] = {
        **dict(content_report.get("validator_binding", {})),
        "pptx_sha256": pptx_sha256,
        "evidence_sha256": evidence_sha256,
        "render_sha256": [record.sha256 for record in render_records],
        "trusted_render_sha256": [record.sha256 for record in trusted_render_records],
    }
    content_report["validator_status"] = ValidationStatus.PASS.value if not findings else ValidationStatus.FAIL.value
    content_report["validator_findings"] = [finding.as_dict() for finding in findings]
    _write_json_atomic(content_path, content_report)
    _write_json_atomic(snapshot_path, snapshot)
    return ValidationResult(
        status=ValidationStatus.PASS if not findings else ValidationStatus.FAIL,
        findings=tuple(findings),
        pptx_sha256=pptx_sha256,
        slide_count=slide_count,
        content_check_path=content_path,
        deck_snapshot_path=snapshot_path,
        content_check=content_report,
        deck_snapshot=snapshot,
    )


def validate_deck(*args: Any, **kwargs: Any) -> ValidationResult:
    """Compatibility alias for :func:`validate_candidate_deck`."""

    return validate_candidate_deck(*args, **kwargs)


__all__ = [
    "DEFAULT_LOGO_ASSET_DIR",
    "DeckValidationError",
    "PptxPackageError",
    "SNAPSHOT_VERSION",
    "TRUSTED_CONTENT_CHECKER",
    "TRUSTED_RENDERER",
    "TRUSTED_RENDER_DPI",
    "VALIDATION_VERSION",
    "ValidationFinding",
    "ValidationResult",
    "ValidationStatus",
    "build_deck_snapshot",
    "sha256_file",
    "validate_candidate_deck",
    "validate_deck",
]


class DeckValidationError(JobError):
    """Reserved compatibility exception for integrations that need one."""

    def __init__(self, message: str):
        super().__init__("validator", message)
