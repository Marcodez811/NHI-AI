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
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4
from xml.etree import ElementTree as ET

from .contracts import JobError
from .source_manifest import SlideSource, SourceManifestError, load_source_manifest

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from app.models.slides import SlideOutline

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
        title_element = item.find(f"{_C}tx")
        series_title = _text(title_element if title_element is not None else ET.Element("empty"))
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
            # Kept empty for readers of the v1 snapshot contract. Final
            # renders are now generated by the backend, so there is no
            # second, compared render set.
            "trusted_render_sha256": [],
        },
        "slide_count": len(slides),
        "slides": slides,
        "renders": [dict(item) for item in render_records],
        "trusted_renders": [],
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


def _default_trusted_renderer(
    deck: Path,
    output_dir: Path,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
    slide_count: int,
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
    pdf_finding = _check_converted_pdf(pdfs, slide_count)
    if pdf_finding:
        raise _RenderCandidateError(pdf_finding)
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


class _RenderCandidateError(RuntimeError):
    """A renderer reached a candidate-specific deck failure."""

    def __init__(self, finding: ValidationFinding) -> None:
        super().__init__(finding.message)
        self.finding = finding


def _render_backend_final(
    deck: Path,
    slide_count: int,
    *,
    output_dir: Path,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
    renderer: Callable[[Path, Path], Any] | None,
) -> tuple[list[ValidationFinding], list[_RenderRecord], Path | None]:
    """Generate the only render set used by validation and semantic review."""

    try:
        if renderer is None:
            rendered_dir = _default_trusted_renderer(deck, output_dir, command_runner, slide_count)
        else:
            returned = renderer(deck, output_dir)
            if isinstance(returned, (str, Path)):
                rendered_dir = Path(returned)
            else:
                rendered_dir = output_dir
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise RuntimeError("backend renderer temporary directory is invalid")
        if rendered_dir.is_symlink() or not rendered_dir.is_dir():
            raise RuntimeError("backend renderer did not return an existing directory")
        resolved_output = output_dir.resolve(strict=True)
        resolved_rendered = rendered_dir.resolve(strict=True)
        try:
            resolved_rendered.relative_to(resolved_output)
        except ValueError as exc:
            raise RuntimeError("backend renderer returned output outside its temporary directory") from exc
        render_findings, records = _validate_renders(rendered_dir, slide_count)
        if render_findings:
            candidate_codes = {"render_blank", "render_all_identical"}
            return [
                finding
                if finding.code in candidate_codes
                else ValidationFinding(
                    f"backend_{finding.code}",
                    finding.message,
                    origin="infrastructure",
                    severity=finding.severity,
                    slide_number=finding.slide_number,
                    details=finding.details,
                )
                for finding in render_findings
            ], records, None
        return [], records, rendered_dir
    except _RenderCandidateError as exc:
        return [exc.finding], [], None
    except Exception as exc:
        return [ValidationFinding("renderer_error", "backend renderer could not render the deck", details={"error": str(exc)}, origin="infrastructure")], [], None


def _replace_final_renders(source_dir: Path, final_dir: Path) -> None:
    """Atomically make a verified backend render set visible to the reviewer."""

    if source_dir.is_symlink() or not source_dir.is_dir():
        raise OSError(f"backend render directory is invalid: {source_dir}")
    replacement = final_dir.with_name(f".{final_dir.name}.{uuid4().hex}.tmp")
    if replacement.exists() or replacement.is_symlink():
        raise OSError(f"backend render replacement is not fresh: {replacement}")
    previous = final_dir.with_name(f".{final_dir.name}.{uuid4().hex}.previous")
    try:
        shutil.copytree(source_dir, replacement, symlinks=False)
        if final_dir.exists() or final_dir.is_symlink():
            os.replace(final_dir, previous)
        os.replace(replacement, final_dir)
    except Exception:
        _clear_final_renders(final_dir)
        raise
    finally:
        _clear_final_renders(replacement)
        _clear_final_renders(previous)


def _clear_final_renders(final_dir: Path) -> None:
    """Ensure a failed candidate cannot expose an earlier deck's review images."""

    if final_dir.is_symlink() or final_dir.is_file():
        final_dir.unlink(missing_ok=True)
    elif final_dir.is_dir():
        shutil.rmtree(final_dir)


def _clear_validator_artifacts(*paths: Path) -> None:
    """Remove prior validator outputs before evaluating a new candidate."""

    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)


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


def _normalize_visible_text(value: str) -> str:
    """Collapse whitespace so OOXML soft-wraps never defeat exact matching."""

    return " ".join(value.split())


_CITATION_FOOTER_RE = re.compile(
    r"^(?:\[\s*(?P<number>\d+)\s*\]\s*)?資料來源\s*[：:]\s*(?P<source>.+)$"
)
_REFERENCE_ENTRY_RE = re.compile(
    r"^\[\s*(?P<number>\d+)\s*\]\s*(?P<source>.+)$"
)
_REFERENCES_SLIDE_TITLE = "參考資料"
_SHA256_TOKEN_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")


def _catalog_source_name(value: str, sources: Sequence[SlideSource]) -> str | None:
    """Return the exact catalog name at a citation-detail boundary."""

    normalized = _normalize_visible_text(value)
    names = sorted(
        (_normalize_visible_text(source.display_name) for source in sources),
        key=len,
        reverse=True,
    )
    for name in names:
        if (
            normalized == name
            or normalized.startswith(name + "，")
            or normalized.startswith(name + ",")
        ):
            return name
    return None


def _citation_source_name(value: str, sources: Sequence[SlideSource]) -> str | None:
    """Resolve one footer/reference payload to its allowlisted display name."""

    source_text = value.strip()
    nested_footer = _CITATION_FOOTER_RE.fullmatch(source_text)
    if nested_footer is not None:
        source_text = nested_footer.group("source").strip()
    return _catalog_source_name(source_text, sources)


def _source_name_allowed(value: str, sources: Sequence[SlideSource]) -> bool:
    """Match one manifest name with an exact citation-detail boundary."""

    return _citation_source_name(value, sources) is not None


def _citation_abstraction_leaks(
    value: str,
    sources: Sequence[SlideSource],
) -> list[str]:
    """Name internal artifacts that appear in an explicit visible citation."""

    normalized = _normalize_visible_text(value)
    folded = normalized.casefold()
    leaks: set[str] = set()
    for token in ("EvidenceStore", "work/evidence.json"):
        if token.casefold() in folded:
            leaks.add(token)
    if _SHA256_TOKEN_RE.search(normalized):
        leaks.add("sha256")
    for source in sources:
        staged_filename = _normalize_visible_text(source.staged_filename)
        display_name = _normalize_visible_text(source.display_name)
        if (
            staged_filename != display_name
            and normalized.count(staged_filename) > display_name.count(staged_filename)
        ):
            leaks.add(staged_filename)
    return sorted(leaks)


def _citation_source_name_findings(
    package: _PptxPackage,
    sources: Sequence[SlideSource],
) -> list[ValidationFinding]:
    """Reject non-catalog names only in explicit citation-shaped paragraphs."""

    findings: list[ValidationFinding] = []
    last_slide_index = len(package.slide_roots) - 1
    for slide_index, root in enumerate(package.slide_roots):
        paragraphs = [_normalize_visible_text(value) for value in _paragraph_texts(root)]
        references_slide = (
            slide_index == last_slide_index
            and _REFERENCES_SLIDE_TITLE in paragraphs
        )
        for paragraph in paragraphs:
            footer_match = _CITATION_FOOTER_RE.fullmatch(paragraph)
            reference_match = (
                _REFERENCE_ENTRY_RE.fullmatch(paragraph) if references_slide else None
            )
            matched = footer_match or reference_match
            if matched is None:
                continue
            leaks = _citation_abstraction_leaks(paragraph, sources)
            if leaks:
                findings.append(
                    ValidationFinding(
                        "citation_abstraction_leak",
                        "citation exposes an internal workflow identifier or artifact",
                        slide_number=slide_index + 1,
                        details={"citation": paragraph, "leaks": leaks},
                        origin="candidate",
                    )
                )
                continue
            source_text = matched.group("source").strip()
            if _source_name_allowed(source_text, sources):
                continue
            findings.append(
                ValidationFinding(
                    "citation_source_name_invalid",
                    "citation source name is not a knowledge-base display name",
                    slide_number=slide_index + 1,
                    details={"citation": paragraph},
                    origin="candidate",
                )
            )
    return findings


def _references_slide_findings(
    package: _PptxPackage,
    sources: Sequence[SlideSource],
) -> list[ValidationFinding]:
    """Validate the last-slide reference index against visible content citations."""

    last_slide_number = len(package.slide_roots)
    last_paragraphs = [
        _normalize_visible_text(value)
        for value in _paragraph_texts(package.slide_roots[-1])
    ]
    title_count = last_paragraphs.count(_REFERENCES_SLIDE_TITLE)
    if title_count != 1:
        return [
            ValidationFinding(
                "references_slide_missing",
                "the last slide must be titled exactly `參考資料`",
                slide_number=last_slide_number,
                details={"title": _REFERENCES_SLIDE_TITLE, "matches": title_count},
                origin="candidate",
            )
        ]

    findings: list[ValidationFinding] = []
    content_sources: set[str] = set()
    content_citations: list[tuple[int, int | None, str]] = []
    for slide_number, root in enumerate(package.slide_roots[:-1], start=1):
        for paragraph in (
            _normalize_visible_text(value) for value in _paragraph_texts(root)
        ):
            footer_match = _CITATION_FOOTER_RE.fullmatch(paragraph)
            if footer_match is None:
                continue
            marker_text = footer_match.group("number")
            marker = int(marker_text) if marker_text is not None else None
            if marker is None:
                findings.append(
                    ValidationFinding(
                        "citation_footer_number_missing",
                        "content citation footer must start with a numbered source marker",
                        slide_number=slide_number,
                        details={"citation": paragraph},
                        origin="candidate",
                    )
                )
            source_name = _citation_source_name(
                footer_match.group("source"),
                sources,
            )
            if source_name is not None:
                content_sources.add(source_name)
                content_citations.append((slide_number, marker, source_name))

    numbers: list[int] = []
    referenced_sources: list[str] = []
    reference_citations: list[tuple[int, str]] = []
    for paragraph in last_paragraphs:
        entry_match = _REFERENCE_ENTRY_RE.fullmatch(paragraph)
        if entry_match is None:
            continue
        number = int(entry_match.group("number"))
        numbers.append(number)
        source_name = _citation_source_name(entry_match.group("source"), sources)
        if source_name is not None:
            referenced_sources.append(source_name)
            reference_citations.append((number, source_name))

    duplicate_numbers = sorted(
        number for number in set(numbers) if numbers.count(number) > 1
    )
    if duplicate_numbers:
        findings.append(
            ValidationFinding(
                "references_number_duplicate",
                "reference numbers must be unique",
                slide_number=last_slide_number,
                details={"numbers": duplicate_numbers},
                origin="candidate",
            )
        )
    elif sorted(numbers) != list(range(1, len(numbers) + 1)):
        findings.append(
            ValidationFinding(
                "references_number_sequence",
                "reference numbers must form a contiguous sequence starting at 1",
                slide_number=last_slide_number,
                details={"numbers": sorted(numbers)},
                origin="candidate",
            )
        )

    duplicate_sources = sorted(
        source_name
        for source_name in set(referenced_sources)
        if referenced_sources.count(source_name) > 1
    )
    if duplicate_sources:
        findings.append(
            ValidationFinding(
                "references_source_duplicate",
                "each cited source must appear exactly once on the references slide",
                slide_number=last_slide_number,
                details={"sources": duplicate_sources},
                origin="candidate",
            )
        )

    reference_source_set = set(referenced_sources)
    missing_sources = sorted(content_sources - reference_source_set)
    unused_sources = sorted(reference_source_set - content_sources)
    if missing_sources or unused_sources:
        findings.append(
            ValidationFinding(
                "references_source_mismatch",
                "references must list exactly the sources cited by content slides",
                slide_number=last_slide_number,
                details={
                    "missing_sources": missing_sources,
                    "unused_sources": unused_sources,
                },
                origin="candidate",
            )
        )

    marker_sources: dict[int, set[str]] = {}
    for _, marker, source_name in content_citations:
        if marker is not None:
            marker_sources.setdefault(marker, set()).add(source_name)
    for marker, source_name in reference_citations:
        marker_sources.setdefault(marker, set()).add(source_name)
    for marker, mapped_sources in sorted(marker_sources.items()):
        if len(mapped_sources) > 1:
            findings.append(
                ValidationFinding(
                    "citation_marker_source_conflict",
                    "one citation marker maps to multiple sources",
                    details={"marker": marker, "sources": sorted(mapped_sources)},
                    origin="candidate",
                )
            )

    content_markers_by_source: dict[str, set[int]] = {}
    for _, marker, source_name in content_citations:
        if marker is not None:
            content_markers_by_source.setdefault(source_name, set()).add(marker)
    for source_name, source_markers in sorted(content_markers_by_source.items()):
        if len(source_markers) > 1:
            findings.append(
                ValidationFinding(
                    "citation_source_marker_inconsistent",
                    "a source must reuse one citation marker across content slides",
                    details={"source": source_name, "markers": sorted(source_markers)},
                    origin="candidate",
                )
            )

    reference_sources_by_marker: dict[int, set[str]] = {}
    for marker, source_name in reference_citations:
        reference_sources_by_marker.setdefault(marker, set()).add(source_name)
    for slide_number, marker, source_name in content_citations:
        if marker is None:
            continue
        reference_sources = reference_sources_by_marker.get(marker, set())
        if reference_sources != {source_name}:
            findings.append(
                ValidationFinding(
                    "citation_footer_reference_mismatch",
                    "content citation marker does not resolve to the same source in references",
                    slide_number=slide_number,
                    details={
                        "marker": marker,
                        "footer_source": source_name,
                        "reference_sources": sorted(reference_sources),
                    },
                    origin="candidate",
                )
            )
    return findings


@dataclass(frozen=True)
class _OutlineMappingEntry:
    """One author-declared, inclusive content-slide range."""

    node_id: str
    slide_start: int
    slide_end: int


def _read_outline_mapping(path: Path) -> tuple[list[_OutlineMappingEntry] | None, ValidationFinding | None]:
    """Load the narrow author sidecar without accepting ambiguous shapes."""

    if not path.is_file() or path.is_symlink():
        return None, ValidationFinding(
            "outline_mapping_missing",
            "work/outline_mapping.json is required for an approved outline",
            details={"path": "work/outline_mapping.json"},
            origin="candidate",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, ValidationFinding(
            "outline_mapping_invalid",
            "work/outline_mapping.json is not valid JSON",
            details={"error": str(exc)},
            origin="candidate",
        )
    if not isinstance(payload, Mapping) or set(payload) != {"nodes"}:
        return None, ValidationFinding(
            "outline_mapping_invalid",
            "outline mapping must contain only a nodes array",
            origin="candidate",
        )
    raw_entries = payload["nodes"]
    if not isinstance(raw_entries, list):
        return None, ValidationFinding(
            "outline_mapping_invalid",
            "outline mapping nodes must be an array",
            origin="candidate",
        )

    entries: list[_OutlineMappingEntry] = []
    expected_keys = {"node_id", "slide_start", "slide_end"}
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping) or set(raw_entry) != expected_keys:
            return None, ValidationFinding(
                "outline_mapping_invalid",
                "each outline mapping entry must contain only node_id, slide_start, and slide_end",
                details={"entry_index": index},
                origin="candidate",
            )
        node_id = raw_entry.get("node_id")
        slide_start = raw_entry.get("slide_start")
        slide_end = raw_entry.get("slide_end")
        if (
            not isinstance(node_id, str)
            or not node_id.strip()
            or type(slide_start) is not int
            or type(slide_end) is not int
        ):
            return None, ValidationFinding(
                "outline_mapping_invalid",
                "outline mapping IDs must be non-empty strings and slide bounds must be integers",
                details={"entry_index": index},
                origin="candidate",
            )
        entries.append(
            _OutlineMappingEntry(
                node_id=node_id,
                slide_start=slide_start,
                slide_end=slide_end,
            )
        )
    return entries, None


def _outline_cross_check(
    outline: "SlideOutline",
    mapping_path: Path,
    content_slide_count: int | None,
) -> list[ValidationFinding]:
    """Catch a candidate that silently drifted from the human-approved outline.

    This is additive to, not a replacement for, the plain ``expected_slide_count``
    check above: that one enforces the brief's requested count, this one enforces
    the planner's approved structure. Both findings are ``origin="candidate"`` so
    they flow through the existing validate-then-revise loop like any other
    deterministic finding -- there is no separate failure path for outline drift.
    """

    findings: list[ValidationFinding] = []
    entries, mapping_finding = _read_outline_mapping(mapping_path)
    if mapping_finding is not None:
        findings.append(mapping_finding)
    if entries is None:
        if content_slide_count is not None and content_slide_count != outline.total_slides:
            findings.append(
                ValidationFinding(
                    "outline_slide_count_mismatch",
                    "candidate content-slide count does not match the approved outline's total_slides",
                    details={"expected": outline.total_slides, "actual": content_slide_count},
                    origin="candidate",
                )
            )
        return findings

    approved_nodes = {node.id: node for node in outline.nodes}
    approved_positions = {node.id: index for index, node in enumerate(outline.nodes)}
    occurrences: dict[str, list[_OutlineMappingEntry]] = {node.id: [] for node in outline.nodes}
    for entry in entries:
        if entry.node_id not in approved_nodes:
            findings.append(
                ValidationFinding(
                    "outline_node_unknown",
                    f"outline mapping contains unapproved node ID `{entry.node_id}`",
                    details={"node_id": entry.node_id},
                    origin="candidate",
                )
            )
            continue
        occurrences[entry.node_id].append(entry)

    for node in outline.nodes:
        count = len(occurrences[node.id])
        if count == 0:
            findings.append(
                ValidationFinding(
                    "outline_node_missing",
                    f"approved outline section `{node.id}` ({node.heading!r}) is absent from the mapping",
                    details={"node_id": node.id, "heading": node.heading},
                    origin="candidate",
                )
            )
        elif count > 1:
            findings.append(
                ValidationFinding(
                    "outline_node_duplicate",
                    f"approved outline section `{node.id}` appears more than once in the mapping",
                    details={"node_id": node.id, "occurrences": count},
                    origin="candidate",
                )
            )

    mapped_approved_ids = [entry.node_id for entry in entries if entry.node_id in approved_positions]
    if mapped_approved_ids != sorted(mapped_approved_ids, key=approved_positions.__getitem__):
        findings.append(
            ValidationFinding(
                "outline_node_order",
                "outline mapping nodes are not in the approved order",
                details={
                    "expected": [node.id for node in outline.nodes],
                    "actual": mapped_approved_ids,
                },
                origin="candidate",
            )
        )

    valid_ranges: list[_OutlineMappingEntry] = []
    for entry in entries:
        if entry.slide_start > entry.slide_end:
            findings.append(
                ValidationFinding(
                    "outline_range_invalid",
                    f"outline mapping range for `{entry.node_id}` starts after it ends",
                    details={
                        "node_id": entry.node_id,
                        "slide_start": entry.slide_start,
                        "slide_end": entry.slide_end,
                    },
                    origin="candidate",
                )
            )
            continue
        if content_slide_count is not None and (
            entry.slide_start < 1 or entry.slide_end > content_slide_count
        ):
            findings.append(
                ValidationFinding(
                    "outline_range_out_of_bounds",
                    f"outline mapping range for `{entry.node_id}` falls outside the content slides",
                    details={
                        "node_id": entry.node_id,
                        "slide_start": entry.slide_start,
                        "slide_end": entry.slide_end,
                        "content_slide_count": content_slide_count,
                    },
                    origin="candidate",
                )
            )
            continue
        valid_ranges.append(entry)

    for previous, current in zip(valid_ranges, valid_ranges[1:]):
        if current.slide_start < previous.slide_start:
            findings.append(
                ValidationFinding(
                    "outline_range_order",
                    f"outline mapping range for `{current.node_id}` precedes `{previous.node_id}`",
                    details={"previous_node_id": previous.node_id, "node_id": current.node_id},
                    origin="candidate",
                )
            )
        elif current.slide_start <= previous.slide_end:
            findings.append(
                ValidationFinding(
                    "outline_range_overlap",
                    f"outline mapping ranges for `{previous.node_id}` and `{current.node_id}` overlap",
                    details={"previous_node_id": previous.node_id, "node_id": current.node_id},
                    origin="candidate",
                )
            )
        elif current.slide_start != previous.slide_end + 1:
            findings.append(
                ValidationFinding(
                    "outline_range_gap",
                    f"outline mapping leaves a gap between `{previous.node_id}` and `{current.node_id}`",
                    details={"previous_node_id": previous.node_id, "node_id": current.node_id},
                    origin="candidate",
                )
            )

    if content_slide_count is not None:
        coverage = [0] * content_slide_count
        for entry in valid_ranges:
            for slide_number in range(entry.slide_start, entry.slide_end + 1):
                coverage[slide_number - 1] += 1
        missing_slides = [index for index, count in enumerate(coverage, start=1) if count == 0]
        repeated_slides = [index for index, count in enumerate(coverage, start=1) if count > 1]
        if missing_slides or repeated_slides:
            findings.append(
                ValidationFinding(
                    "outline_slide_coverage",
                    "outline mapping must cover each content slide exactly once",
                    details={"missing_slides": missing_slides, "repeated_slides": repeated_slides},
                    origin="candidate",
                )
            )

    if content_slide_count is not None and content_slide_count != outline.total_slides:
        findings.append(
            ValidationFinding(
                "outline_slide_count_mismatch",
                "candidate content-slide count does not match the approved outline's total_slides",
                details={"expected": outline.total_slides, "actual": content_slide_count},
                origin="candidate",
            )
        )
    return findings


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


def _validate_candidate_deck_in_render_root(
    job_dir: Path,
    *,
    render_root: Path,
    expected_slide_count: int | None = None,
    requested_title: str | None = None,
    evidence_path: Path | None = None,
    outline_path: Path | None = None,
    sources_path: Path | None = None,
    logo_dir: Path | None = DEFAULT_LOGO_ASSET_DIR,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    content_checker: Callable[[Path], Mapping[str, Any]] | None = None,
    renderer: Callable[[Path, Path], Any] | None = None,
    require_logo: bool = True,
) -> ValidationResult:
    """Perform validation while the caller owns the isolated render directory."""

    deck = job_dir / "output" / "presentation.pptx"
    evidence = Path(evidence_path) if evidence_path is not None else job_dir / "work" / "evidence.json"
    outline_file = Path(outline_path) if outline_path is not None else job_dir / "work" / "outline.json"
    sources_file = Path(sources_path) if sources_path is not None else job_dir / "work" / "sources.json"
    artifact_dir = job_dir / "work" / "intermediate"
    content_path = artifact_dir / "content_check.json"
    snapshot_path = artifact_dir / "deck_snapshot.json"
    _clear_final_renders(job_dir / "work" / "rendered" / "final")
    _clear_validator_artifacts(content_path, snapshot_path)
    findings: list[ValidationFinding] = []
    sources: tuple[SlideSource, ...] | None = None
    try:
        sources = load_source_manifest(sources_file)
    except SourceManifestError as exc:
        findings.append(
            ValidationFinding(
                "source_manifest_unavailable",
                "backend source-name manifest is missing or malformed",
                details={"error": str(exc)},
                origin="infrastructure",
            )
        )
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

    # Outline-driven jobs write ``work/outline.json`` once a human approves the
    # planner's proposal (Stage 5b); every other job never creates that file, so
    # this stays absent and the cross-check below never runs -- behavior for a
    # non-planning job is unchanged. Imported locally, matching evidence.py's
    # own avoidance of a module-level ``app.models`` dependency.
    outline: "SlideOutline | None" = None
    if outline_file.is_file() and not outline_file.is_symlink():
        from app.models.slides import SlideOutline

        outline = SlideOutline.model_validate(json.loads(outline_file.read_text(encoding="utf-8")))

    package: _PptxPackage | None = None
    try:
        package = _load_pptx_package(deck)
        slide_count = len(package.ordered_slides)
        if sources is not None:
            findings.extend(_citation_source_name_findings(package, sources))
            findings.extend(_references_slide_findings(package, sources))
        if expected_slide_count is not None and slide_count != expected_slide_count:
            findings.append(ValidationFinding("slide_count", "PPTX slide count does not match the requested count", details={"expected": expected_slide_count, "actual": slide_count}, origin="candidate"))
    except PptxPackageError as exc:
        findings.append(ValidationFinding("pptx_invalid", str(exc), origin="candidate"))

    render_records: list[_RenderRecord] = []
    rendered_dir: Path | None = None
    if slide_count is not None:
        render_findings, render_records, rendered_dir = _render_backend_final(
            deck,
            slide_count,
            output_dir=render_root,
            command_runner=command_runner,
            renderer=renderer,
        )
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

    if package is not None:
        try:
            snapshot = build_deck_snapshot(
                deck,
                render_records=[record.as_dict() for record in render_records],
                evidence_sha256=evidence_sha256,
            )
        except Exception as exc:
            snapshot = _unavailable_snapshot(pptx_sha256, evidence_sha256, str(exc))
            findings.append(ValidationFinding("snapshot_error", "deck snapshot could not be generated", details={"error": str(exc)}, origin="infrastructure"))
    else:
        snapshot = _unavailable_snapshot(pptx_sha256, evidence_sha256, "PPTX package validation failed")

    if outline is not None and snapshot.get("status") == "available":
        # Only run against a snapshot that actually parsed; a broken package
        # already produced ``pptx_invalid``/``snapshot_error`` above, and an
        # "every node missing" pile-on would just be noise on top of that.
        findings.extend(
            _outline_cross_check(
                outline,
                job_dir / "work" / "outline_mapping.json",
                slide_count,
            )
        )

    content_report["validator_binding"] = {
        **dict(content_report.get("validator_binding", {})),
        "pptx_sha256": pptx_sha256,
        "evidence_sha256": evidence_sha256,
        "render_sha256": [record.sha256 for record in render_records],
        "trusted_render_sha256": [],
    }
    if not findings and rendered_dir is not None:
        try:
            _replace_final_renders(rendered_dir, job_dir / "work" / "rendered" / "final")
        except OSError as exc:
            finding = ValidationFinding(
                "final_render_publish_error",
                "backend render output could not be published for semantic review",
                details={"error": str(exc)},
                origin="infrastructure",
            )
            findings.append(finding)
    content_report["validator_status"] = ValidationStatus.PASS.value if not findings else ValidationStatus.FAIL.value
    content_report["validator_findings"] = [finding.as_dict() for finding in findings]
    try:
        _write_json_atomic(snapshot_path, snapshot)
        _write_json_atomic(content_path, content_report)
    except Exception:
        _clear_final_renders(job_dir / "work" / "rendered" / "final")
        _clear_validator_artifacts(content_path, snapshot_path)
        raise
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


def validate_candidate_deck(
    job_dir: Path,
    *,
    expected_slide_count: int | None = None,
    requested_title: str | None = None,
    evidence_path: Path | None = None,
    outline_path: Path | None = None,
    sources_path: Path | None = None,
    logo_dir: Path | None = DEFAULT_LOGO_ASSET_DIR,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    content_checker: Callable[[Path], Mapping[str, Any]] | None = None,
    renderer: Callable[[Path, Path], Any] | None = None,
    require_logo: bool = True,
) -> ValidationResult:
    """Validate one candidate and atomically produce validator artifacts.

    The backend always produces the final PNGs used by validation and semantic
    review. ``renderer`` is an injectable test/deployment seam; it receives
    ``(deck_path, temporary_output_directory)`` and must write canonical
    ``slide-<number>.png`` files there (or return their directory).  Its
    temporary directory is removed on every exit path, including unexpected
    validation and artifact-publication errors.

    ``outline_path`` defaults to ``job_dir / "work" / "outline.json"``, exactly
    where the approve route (Stage 5b) writes it. When that file is absent --
    every job that never ran a planner -- no outline cross-check runs and
    validation behaves exactly as it did before this check existed.
    """

    job_dir = Path(job_dir)
    render_parent = job_dir / "work" / "rendered"
    render_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".backend_render_", dir=render_parent) as temporary:
        return _validate_candidate_deck_in_render_root(
            job_dir,
            render_root=Path(temporary),
            expected_slide_count=expected_slide_count,
            requested_title=requested_title,
            evidence_path=evidence_path,
            outline_path=outline_path,
            sources_path=sources_path,
            logo_dir=logo_dir,
            command_runner=command_runner,
            runner=runner,
            content_checker=content_checker,
            renderer=renderer,
            require_logo=require_logo,
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
