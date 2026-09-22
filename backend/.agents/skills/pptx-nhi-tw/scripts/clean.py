"""Remove unreferenced files and speaker notes from a PPTX package.

Usage: python clean.py <presentation.pptx|unpacked_dir>

Example:
    python clean.py output/presentation.pptx

This script removes:
- Orphaned slides (not in sldIdLst) and their relationships
- [trash] directory (unreferenced files)
- Orphaned .rels files for deleted resources
- Unreferenced media, embeddings, charts, diagrams, drawings, ink files
- Unreferenced theme files
- All speaker-note slides and masters
- Content-Type overrides for deleted files
"""

from __future__ import annotations

import os
import posixpath
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import defusedxml.minidom

from office.helpers import SLIDE_REL_TYPE, opc_target, rels_source_part


NOTES_RELATIONSHIP_SUFFIXES = ("/notesSlide", "/notesMaster")


def _slide_rids(pres_rels_path: Path, unpacked_dir: Path) -> dict[str, str]:
    source_part = rels_source_part(pres_rels_path, unpacked_dir)
    rels_dom = defusedxml.minidom.parse(str(pres_rels_path))

    rids: dict[str, str] = {}
    for rel in rels_dom.getElementsByTagName("Relationship"):
        if rel.getAttribute("Type") != SLIDE_REL_TYPE:
            continue
        part = opc_target(
            rel.getAttribute("Target"), source_part, rel.getAttribute("TargetMode")
        )
        if part is not None:
            rids[rel.getAttribute("Id")] = part
    return rids


def get_slides_in_sldidlst(unpacked_dir: Path) -> set[str]:
    pres_path = unpacked_dir / "ppt" / "presentation.xml"
    pres_rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"

    if not pres_path.exists() or not pres_rels_path.exists():
        return set()

    rid_to_slide = _slide_rids(pres_rels_path, unpacked_dir)

    pres_content = pres_path.read_text(encoding="utf-8")
    referenced_rids = set(re.findall(r'<p:sldId[^>]*r:id="([^"]+)"', pres_content))

    return {
        posixpath.basename(rid_to_slide[rid])
        for rid in referenced_rids
        if rid in rid_to_slide
    }


class RefusedToClean(Exception):
    """The package does not look the way a readable package should."""


def remove_orphaned_slides(unpacked_dir: Path) -> list[str]:
    slides_dir = unpacked_dir / "ppt" / "slides"
    slides_rels_dir = slides_dir / "_rels"
    pres_rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"

    if not slides_dir.exists():
        return []

    referenced_slides = get_slides_in_sldidlst(unpacked_dir)
    on_disk = sorted(slides_dir.glob("slide*.xml"))

    if on_disk and not any(s.name in referenced_slides for s in on_disk):
        listed = re.findall(
            r'<p:sldId[^>]*r:id="([^"]+)"',
            (unpacked_dir / "ppt" / "presentation.xml").read_text(encoding="utf-8")
            if (unpacked_dir / "ppt" / "presentation.xml").exists()
            else "",
        )
        if listed:
            raise RefusedToClean(
                f"<p:sldIdLst> lists {len(listed)} slide(s) and none of the "
                f"{len(on_disk)} slide(s) on disk match any of them. Refusing to "
                f"delete them all — this is a parse failure, not an empty deck."
            )

    removed = []

    for slide_file in on_disk:
        if slide_file.name not in referenced_slides:
            rel_path = slide_file.relative_to(unpacked_dir)
            slide_file.unlink()
            removed.append(str(rel_path))

            rels_file = slides_rels_dir / f"{slide_file.name}.rels"
            if rels_file.exists():
                rels_file.unlink()
                removed.append(str(rels_file.relative_to(unpacked_dir)))

    if removed and pres_rels_path.exists():
        rels_dom = defusedxml.minidom.parse(str(pres_rels_path))
        source_part = rels_source_part(pres_rels_path, unpacked_dir)
        changed = False

        for rel in list(rels_dom.getElementsByTagName("Relationship")):
            if rel.getAttribute("Type") != SLIDE_REL_TYPE:
                continue
            part = opc_target(
                rel.getAttribute("Target"), source_part, rel.getAttribute("TargetMode")
            )
            if part is None:
                continue
            if posixpath.basename(part) not in referenced_slides:
                if rel.parentNode:
                    rel.parentNode.removeChild(rel)
                    changed = True

        if changed:
            with open(pres_rels_path, "wb") as f:
                f.write(rels_dom.toxml(encoding="utf-8"))

    return removed


def remove_trash_directory(unpacked_dir: Path) -> list[str]:
    trash_dir = unpacked_dir / "[trash]"
    removed = []

    if trash_dir.exists() and trash_dir.is_dir():
        for file_path in trash_dir.iterdir():
            if file_path.is_file():
                rel_path = file_path.relative_to(unpacked_dir)
                removed.append(str(rel_path))
                file_path.unlink()
        trash_dir.rmdir()

    return removed


def _referenced_by(rels_files, unpacked_dir: Path) -> set:
    referenced = set()

    for rels_file in rels_files:
        source_part = rels_source_part(rels_file, unpacked_dir)
        dom = defusedxml.minidom.parse(str(rels_file))
        for rel in dom.getElementsByTagName("Relationship"):
            part = opc_target(
                rel.getAttribute("Target"), source_part, rel.getAttribute("TargetMode")
            )
            if part is not None:
                referenced.add(Path(part))

    return referenced


def remove_orphaned_rels_files(unpacked_dir: Path) -> list[str]:
    resource_dirs = ["charts", "diagrams", "drawings"]
    removed = []

    for dir_name in resource_dirs:
        rels_dir = unpacked_dir / "ppt" / dir_name / "_rels"
        if not rels_dir.exists():
            continue

        for rels_file in rels_dir.glob("*.rels"):
            resource_file = rels_dir.parent / rels_file.name.replace(".rels", "")
            if not resource_file.exists():
                rels_file.unlink()
                removed.append(str(rels_file.relative_to(unpacked_dir)))

    return removed


def _remove_notes_relationships(rels_path: Path) -> None:
    """Remove only notes relationships while preserving every other edge."""

    dom = defusedxml.minidom.parse(str(rels_path))
    changed = False
    for relationship in list(dom.getElementsByTagName("Relationship")):
        if relationship.getAttribute("Type").endswith(NOTES_RELATIONSHIP_SUFFIXES):
            if relationship.parentNode:
                relationship.parentNode.removeChild(relationship)
                changed = True
    if changed:
        rels_path.write_bytes(dom.toxml(encoding="utf-8"))


def remove_speaker_notes(unpacked_dir: Path) -> list[str]:
    """Remove the complete notes graph emitted by PowerPoint generators."""

    for rels_path in sorted((unpacked_dir / "ppt").rglob("*.rels")):
        _remove_notes_relationships(rels_path)

    presentation_path = unpacked_dir / "ppt" / "presentation.xml"
    if presentation_path.is_file():
        dom = defusedxml.minidom.parse(str(presentation_path))
        changed = False
        for node in list(dom.getElementsByTagName("p:notesMasterIdLst")):
            if node.parentNode:
                node.parentNode.removeChild(node)
                changed = True
        if changed:
            presentation_path.write_bytes(dom.toxml(encoding="utf-8"))

    removed: list[str] = []
    for directory_name in ("notesSlides", "notesMasters"):
        directory = unpacked_dir / "ppt" / directory_name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*"), reverse=True):
            if path.is_file():
                removed.append(path.relative_to(unpacked_dir).as_posix())
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        directory.rmdir()
    return removed


def get_referenced_files(unpacked_dir: Path) -> set:
    return _referenced_by(sorted(unpacked_dir.rglob("*.rels")), unpacked_dir)


def remove_orphaned_files(unpacked_dir: Path, referenced: set) -> list[str]:
    resource_dirs = ["media", "embeddings", "charts", "diagrams", "tags", "drawings", "ink"]
    removed = []

    for dir_name in resource_dirs:
        dir_path = unpacked_dir / "ppt" / dir_name
        if not dir_path.exists():
            continue

        for file_path in dir_path.glob("*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(unpacked_dir)
            if rel_path not in referenced:
                file_path.unlink()
                removed.append(str(rel_path))

    theme_dir = unpacked_dir / "ppt" / "theme"
    if theme_dir.exists():
        for file_path in theme_dir.glob("theme*.xml"):
            rel_path = file_path.relative_to(unpacked_dir)
            if rel_path not in referenced:
                file_path.unlink()
                removed.append(str(rel_path))
                theme_rels = theme_dir / "_rels" / f"{file_path.name}.rels"
                if theme_rels.exists():
                    theme_rels.unlink()
                    removed.append(str(theme_rels.relative_to(unpacked_dir)))

    notes_dir = unpacked_dir / "ppt" / "notesSlides"
    if notes_dir.exists():
        for file_path in notes_dir.glob("*.xml"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(unpacked_dir)
            if rel_path not in referenced:
                file_path.unlink()
                removed.append(str(rel_path))

        notes_rels_dir = notes_dir / "_rels"
        if notes_rels_dir.exists():
            for file_path in notes_rels_dir.glob("*.rels"):
                notes_file = notes_dir / file_path.name.replace(".rels", "")
                if not notes_file.exists():
                    file_path.unlink()
                    removed.append(str(file_path.relative_to(unpacked_dir)))

    return removed


def update_content_types(unpacked_dir: Path, removed_files: list[str]) -> None:
    ct_path = unpacked_dir / "[Content_Types].xml"
    if not ct_path.exists():
        return

    dom = defusedxml.minidom.parse(str(ct_path))
    changed = False

    for override in list(dom.getElementsByTagName("Override")):
        part_name = override.getAttribute("PartName").lstrip("/")
        if part_name in removed_files:
            if override.parentNode:
                override.parentNode.removeChild(override)
                changed = True

    if changed:
        with open(ct_path, "wb") as f:
            f.write(dom.toxml(encoding="utf-8"))


def clean_unused_files(unpacked_dir: Path) -> list[str]:
    all_removed = []

    if list(unpacked_dir.rglob("*.rels")) and not get_referenced_files(unpacked_dir):
        raise RefusedToClean(
            "no relationship in this package names a part we can resolve. "
            "Refusing to treat every file as unreferenced."
        )

    slides_removed = remove_orphaned_slides(unpacked_dir)
    all_removed.extend(slides_removed)

    notes_removed = remove_speaker_notes(unpacked_dir)
    all_removed.extend(notes_removed)

    trash_removed = remove_trash_directory(unpacked_dir)
    all_removed.extend(trash_removed)

    while True:
        removed_rels = remove_orphaned_rels_files(unpacked_dir)
        referenced = get_referenced_files(unpacked_dir)
        removed_files = remove_orphaned_files(unpacked_dir, referenced)

        total_removed = removed_rels + removed_files
        if not total_removed:
            break

        all_removed.extend(total_removed)

    if all_removed:
        update_content_types(unpacked_dir, all_removed)

    return all_removed


def _validate_archive_members(archive: zipfile.ZipFile) -> None:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise RefusedToClean("PPTX contains duplicate ZIP member names")
    for name in names:
        member = PurePosixPath(name)
        if name.startswith(("/", "\\")) or "\\" in name or ".." in member.parts:
            raise RefusedToClean(f"unsafe PPTX member path: {name}")


def clean_presentation(presentation_path: Path) -> list[str]:
    """Clean an archive in a temporary tree and replace it atomically."""

    original_mode = stat.S_IMODE(presentation_path.stat().st_mode)
    with tempfile.TemporaryDirectory(prefix="pptx_clean_") as temporary:
        unpacked_dir = Path(temporary) / "unpacked"
        unpacked_dir.mkdir()
        try:
            with zipfile.ZipFile(presentation_path) as archive:
                _validate_archive_members(archive)
                archive.extractall(unpacked_dir)
        except zipfile.BadZipFile as exc:
            raise RefusedToClean("presentation is not a valid PPTX/ZIP") from exc

        removed = clean_unused_files(unpacked_dir)
        if not removed:
            return []

        descriptor, replacement_name = tempfile.mkstemp(
            prefix=f".{presentation_path.name}.",
            suffix=".tmp",
            dir=presentation_path.parent,
        )
        os.close(descriptor)
        replacement_path = Path(replacement_name)
        try:
            with zipfile.ZipFile(
                replacement_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for path in sorted(unpacked_dir.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(unpacked_dir).as_posix())
            replacement_path.chmod(original_mode)
            os.replace(replacement_path, presentation_path)
        finally:
            replacement_path.unlink(missing_ok=True)
    return removed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python clean.py <presentation.pptx|unpacked_dir>", file=sys.stderr)
        print("Example: python clean.py output/presentation.pptx", file=sys.stderr)
        sys.exit(1)

    target = Path(sys.argv[1])

    if not target.exists():
        print(f"Error: {target} not found", file=sys.stderr)
        sys.exit(1)

    try:
        removed = clean_unused_files(target) if target.is_dir() else clean_presentation(target)
    except (OSError, RefusedToClean, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Nothing was deleted.", file=sys.stderr)
        sys.exit(1)

    if removed:
        print(f"Removed {len(removed)} files:")
        for filename in removed:
            print(f"  {filename}")
    else:
        print("No unreferenced files found")
