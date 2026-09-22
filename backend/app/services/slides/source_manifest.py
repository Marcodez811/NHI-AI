"""Authoritative knowledge-base names for staged slide sources.

The extractor identifies documents by their staged filenames, while delivered
slides must use the names people see in the knowledge base.  This sidecar is
the narrow bridge between those two namespaces; it deliberately carries no
database IDs, storage paths, or hashes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


SOURCE_MANIFEST_VERSION = "SlideSources v1"


class SourceManifestError(ValueError):
    """The backend-owned source-name sidecar is missing or malformed."""


@dataclass(frozen=True)
class SlideSource:
    """One staged extraction filename and its user-facing catalog name."""

    staged_filename: str
    display_name: str


def _source_entry(value: Any, index: int) -> SlideSource:
    if not isinstance(value, dict) or set(value) != {"staged_filename", "display_name"}:
        raise SourceManifestError(
            f"sources[{index}] must contain only staged_filename and display_name"
        )
    staged_filename = value.get("staged_filename")
    display_name = value.get("display_name")
    if (
        not isinstance(staged_filename, str)
        or not staged_filename
        or staged_filename in {".", ".."}
        or Path(staged_filename).name != staged_filename
        or "/" in staged_filename
        or "\\" in staged_filename
    ):
        raise SourceManifestError(f"sources[{index}].staged_filename is invalid")
    if not isinstance(display_name, str) or not display_name.strip():
        raise SourceManifestError(f"sources[{index}].display_name is invalid")
    return SlideSource(staged_filename=staged_filename, display_name=display_name)


def load_source_manifest(path: Path) -> tuple[SlideSource, ...]:
    """Read a strict source manifest without accepting alternate namespaces."""

    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SourceManifestError("work/sources.json is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceManifestError("work/sources.json is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"format_version", "sources"}:
        raise SourceManifestError(
            "work/sources.json must contain only format_version and sources"
        )
    if payload.get("format_version") != SOURCE_MANIFEST_VERSION:
        raise SourceManifestError("work/sources.json has an unsupported format_version")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise SourceManifestError("work/sources.json must contain a non-empty sources array")
    sources = tuple(_source_entry(value, index) for index, value in enumerate(raw_sources))
    staged_filenames = [source.staged_filename for source in sources]
    if len(staged_filenames) != len(set(staged_filenames)):
        raise SourceManifestError("work/sources.json contains duplicate staged filenames")
    return sources


def write_source_manifest(
    path: Path,
    staged_filenames: Sequence[str],
    display_names: Sequence[str],
) -> tuple[SlideSource, ...]:
    """Atomically write and re-read the backend-owned source-name mapping."""

    if len(staged_filenames) != len(display_names) or not staged_filenames:
        raise SourceManifestError("staged filenames and display names must align")
    sources = tuple(
        _source_entry(
            {"staged_filename": staged_filename, "display_name": display_name},
            index,
        )
        for index, (staged_filename, display_name) in enumerate(
            zip(staged_filenames, display_names, strict=True)
        )
    )
    if len({source.staged_filename for source in sources}) != len(sources):
        raise SourceManifestError("staged filenames must be unique")
    payload = {
        "format_version": SOURCE_MANIFEST_VERSION,
        "sources": [
            {
                "staged_filename": source.staged_filename,
                "display_name": source.display_name,
            }
            for source in sources
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return load_source_manifest(path)


__all__ = [
    "SOURCE_MANIFEST_VERSION",
    "SlideSource",
    "SourceManifestError",
    "load_source_manifest",
    "write_source_manifest",
]
