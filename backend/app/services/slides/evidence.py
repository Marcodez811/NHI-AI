"""Frozen EvidenceStore construction and integrity checks for slide jobs.

The extraction skill writes a manifest, document indexes, chunks, and copied
assets.  This module is deliberately independent of agent execution: it
validates those files, flattens every extracted block into one provenance
aware store, and freezes that store atomically before an author is started.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


EXTRACTION_FORMAT_VERSION = "1.0"
EVIDENCE_STORE_VERSION = "EvidenceStore v1"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_KINDS = {"heading", "paragraph", "list_item", "table", "figure", "footnote"}


class EvidenceError(ValueError):
    """Raised when extraction artifacts cannot become authoritative evidence."""


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for content and block identities."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{label} is invalid JSON: {exc.msg}") from exc
    except UnicodeError as exc:
        raise EvidenceError(f"{label} is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise EvidenceError(f"{label} could not be read: {exc}") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be a JSON array")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    values = _list(value, label)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise EvidenceError(f"{label} must contain non-empty strings")
    return values


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{label} must be a non-empty string")
    return value


def _safe_relative(root: Path, raw_path: str, label: str) -> Path:
    """Resolve an artifact path while rejecting absolute and traversal paths."""

    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise EvidenceError(f"{label} must be relative: {raw_path}")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise EvidenceError(f"{label} escapes extraction output: {raw_path}") from exc
    return resolved


def _manifest(extracted_dir: Path) -> tuple[dict[str, Any], Path]:
    root = Path(extracted_dir)
    manifest_path = root / "manifest.json"
    manifest = _object(_read_json(manifest_path, "extraction manifest"), "extraction manifest")
    return manifest, manifest_path


def _load_index(extracted_dir: Path, index_value: Any, label: str) -> tuple[dict[str, Any], Path | None]:
    if isinstance(index_value, dict):
        return _object(index_value, label), None
    index_name = _string(index_value, label)
    index_path = _safe_relative(extracted_dir, index_name, label)
    return _object(_read_json(index_path, label), label), index_path


def _iter_index_blocks(extracted_dir: Path, index: dict[str, Any], index_path: Path | None, label: str) -> Iterable[dict[str, Any]]:
    chunks = index.get("chunks", [])
    if not isinstance(chunks, list):
        raise EvidenceError(f"{label}.chunks must be a JSON array")
    if chunks:
        for number, chunk_value in enumerate(chunks, start=1):
            if isinstance(chunk_value, dict):
                chunk = _object(chunk_value, f"{label}.chunks[{number}]")
            else:
                chunk_name = _string(chunk_value, f"{label}.chunks[{number}]")
                # Chunk paths are relative to the extraction root in the
                # extractor contract.  Accepting index-relative paths keeps
                # hand-authored fixtures compatible with older artifacts.
                candidates = [_safe_relative(extracted_dir, chunk_name, f"{label}.chunks[{number}]")]
                if index_path is not None:
                    try:
                        candidates.insert(0, _safe_relative(index_path.parent, chunk_name, f"{label}.chunks[{number}]"))
                    except EvidenceError:
                        # The canonical extractor writes root-relative chunk
                        # paths; older hand-authored fixtures may use paths
                        # relative to the index, so try both safely.
                        pass
                chunk_path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
                chunk = _object(_read_json(chunk_path, f"{label}.chunks[{number}]"), f"{label}.chunks[{number}]")
            if chunk.get("format_version") != EXTRACTION_FORMAT_VERSION:
                raise EvidenceError(f"{label}.chunks[{number}] has an unsupported format_version")
            block_list = _list(chunk.get("blocks"), f"{label}.chunks[{number}].blocks")
            for block_number, block in enumerate(block_list, start=1):
                yield _object(block, f"{label}.chunks[{number}].blocks[{block_number}]")
        return
    for number, block in enumerate(_list(index.get("blocks", []), f"{label}.blocks"), start=1):
        yield _object(block, f"{label}.blocks[{number}]")


def _validate_block(block: dict[str, Any], document_id: str, label: str) -> None:
    block_id = _string(block.get("id"), f"{label}.id")
    if not SHA256_PATTERN.fullmatch(block_id):
        raise EvidenceError(f"{label}.id is not a sha256 id")
    kind = block.get("kind")
    if kind not in _KINDS:
        raise EvidenceError(f"{label}.kind is invalid")
    text = block.get("text")
    if not isinstance(text, str):
        raise EvidenceError(f"{label}.text must be a string")
    locator = block.get("locator")
    if not isinstance(locator, dict):
        raise EvidenceError(f"{label}.locator must be an object")
    expected = sha256_bytes(canonical_json({"document_id": document_id, "kind": kind, "text": text, "locator": locator}))
    if block_id != expected:
        raise EvidenceError(f"{label}.id does not match its document, kind, text, and locator")


def _validate_assets(extracted_dir: Path, assets: Any, label: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for number, raw_asset in enumerate(_list(assets, label), start=1):
        asset = _object(raw_asset, f"{label}[{number}]")
        asset_id = _string(asset.get("id"), f"{label}[{number}].id")
        if not SHA256_PATTERN.fullmatch(asset_id):
            raise EvidenceError(f"{label}[{number}].id is not a sha256 id")
        asset_path = _string(asset.get("path"), f"{label}[{number}].path")
        resolved = _safe_relative(extracted_dir, asset_path, f"{label}[{number}].path")
        if not resolved.is_file():
            raise EvidenceError(f"{label}[{number}].path does not exist: {asset_path}")
        actual = sha256_file(resolved)
        if actual != asset_id:
            raise EvidenceError(f"{label}[{number}].id does not match asset bytes")
        normalized.append(asset)
    return normalized


def validate_extraction(extracted_dir: Path, *, verify_source_hashes: bool = False) -> list[str]:
    """Return deterministic problems found in a complete extraction tree.

    Source paths are intentionally optional during validation.  A retained
    extraction bundle can be validated after the original files are gone; if
    a declared source is still present, callers can opt into byte verification
    with ``verify_source_hashes=True``.
    """

    problems: list[str] = []
    try:
        manifest, _manifest_path = _manifest(Path(extracted_dir))
        if manifest.get("format_version") != EXTRACTION_FORMAT_VERSION:
            problems.append("manifest has an unsupported format_version")
        if manifest.get("status") not in {"ok", "warning", "error"}:
            problems.append("manifest has an invalid status")
        try:
            _string_list(manifest.get("warnings", []), "manifest.warnings")
            manifest_errors = _string_list(manifest.get("errors", []), "manifest.errors")
            if manifest_errors:
                problems.append("manifest contains errors")
        except EvidenceError as exc:
            problems.append(str(exc))
        documents = _list(manifest.get("documents"), "manifest.documents")
        seen_documents: set[str] = set()
        for number, raw_document in enumerate(documents, start=1):
            label = f"manifest.documents[{number}]"
            document = _object(raw_document, label)
            document_id = _string(document.get("document_id"), f"{label}.document_id")
            if not SHA256_PATTERN.fullmatch(document_id):
                problems.append(f"{label}.document_id is not a sha256 id")
            if document_id in seen_documents:
                problems.append(f"duplicate document_id: {document_id}")
            seen_documents.add(document_id)
            source = document.get("source")
            if not isinstance(source, str):
                problems.append(f"{label}.source must be a string")
            status = document.get("status")
            if status not in {"ok", "warning", "error"}:
                problems.append(f"{label}.status is invalid")
            elif status == "error":
                problems.append(f"{label}.status is error")
            index_value = document.get("index")
            if not index_value:
                problems.append(f"{label}.index is missing")
                continue
            try:
                index, index_path = _load_index(Path(extracted_dir), index_value, f"{label}.index")
                if index.get("format_version") != EXTRACTION_FORMAT_VERSION:
                    raise EvidenceError(f"{label}.index has an unsupported format_version")
                if index.get("document_id") != document_id:
                    raise EvidenceError(f"{label}.index document_id does not match manifest")
                index_status = index.get("status")
                if index_status not in {"ok", "warning", "error"}:
                    raise EvidenceError(f"{label}.index status is invalid")
                if index_status == "error":
                    raise EvidenceError(f"{label}.index status is error")
                _string_list(index.get("warnings", []), f"{label}.index.warnings")
                _string_list(index.get("errors", []), f"{label}.index.errors")
                block_ids: list[str] = []
                for block_number, block in enumerate(_iter_index_blocks(Path(extracted_dir), index, index_path, str(index_value)), start=1):
                    _validate_block(block, document_id, f"{label}.block[{block_number}]")
                    block_ids.append(block["id"])
                if len(block_ids) != len(set(block_ids)):
                    raise EvidenceError(f"{label}.index contains duplicate block IDs")
                _validate_assets(Path(extracted_dir), index.get("assets", []), f"{label}.index.assets")
                if verify_source_hashes and isinstance(source, str) and Path(source).is_file():
                    if sha256_file(Path(source)) != document_id:
                        raise EvidenceError(f"{label}.source bytes do not match document_id")
            except EvidenceError as exc:
                problems.append(str(exc))
        if manifest.get("status") == "error":
            problems.append("manifest status is error")
    except EvidenceError as exc:
        problems.append(str(exc))
    except OSError as exc:
        problems.append(f"extraction output could not be inspected: {exc}")
    return problems


def _document_blocks(extracted_dir: Path, document: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    index, index_path = _load_index(extracted_dir, document["index"], "document index")
    blocks = list(_iter_index_blocks(extracted_dir, index, index_path, "document index"))
    return index, blocks


def _source_name(source: object) -> str:
    """Return a portable display name without exposing a host path."""

    value = str(source or "").strip().replace("\\", "/")
    return value.rsplit("/", 1)[-1] or "來源文件"


def _locator_scope(locator: dict[str, Any]) -> tuple[str, str]:
    """Keep document-body headings out of footnote and other source parts."""

    return (str(locator.get("type", "")), str(locator.get("part", "")))


def _line_locator_label(locator: dict[str, Any]) -> str:
    start, end = locator.get("line_start"), locator.get("line_end")
    if not isinstance(start, int) or start < 1:
        return ""
    if not isinstance(end, int) or end <= start:
        return f"第 {start} 行"
    return f"第 {start}–{end} 行"


def _citation_for_block(
    block: dict[str, Any],
    source_name: str,
    sections: dict[tuple[str, str], list[tuple[int, str]]],
) -> dict[str, Any]:
    """Build a human-facing citation while keeping provenance IDs internal."""

    locator = block["locator"]
    scope = _locator_scope(locator)
    section_stack = sections.setdefault(scope, [])
    if block.get("kind") == "heading":
        heading = " ".join(str(block.get("text", "")).split())
        level = block.get("heading_level")
        if heading and isinstance(level, int) and level > 0:
            section_stack[:] = [item for item in section_stack if item[0] < level]
            section_stack.append((level, heading))

    section_path = [heading for _, heading in section_stack]
    locator_type = locator.get("type")
    if locator_type == "pdf" and isinstance(locator.get("page"), int) and locator["page"] > 0:
        locator_label = f"PDF 第 {locator['page']} 頁"
    elif locator_type in {"markdown", "text"}:
        locator_label = _line_locator_label(locator)
    else:
        locator_label = ""
    components = [source_name]
    if section_path:
        components.append("／".join(f"〈{heading}〉" for heading in section_path))
    if locator_label:
        components.append(locator_label)
    return {
        "source_name": source_name,
        "section_path": section_path,
        "locator_label": locator_label,
        "display_text": "資料來源：" + "，".join(components),
    }


def _validate_citation(value: object, label: str) -> None:
    citation = _object(value, label)
    _string(citation.get("source_name"), f"{label}.source_name")
    _string(citation.get("display_text"), f"{label}.display_text")
    if not isinstance(citation.get("locator_label"), str):
        raise EvidenceError(f"{label}.locator_label must be a string")
    section_path = _list(citation.get("section_path"), f"{label}.section_path")
    if any(not isinstance(heading, str) or not heading.strip() for heading in section_path):
        raise EvidenceError(f"{label}.section_path must contain non-empty strings")


def _build_store(extracted_dir: Path) -> dict[str, Any]:
    root = Path(extracted_dir)
    manifest, manifest_path = _manifest(root)
    problems = validate_extraction(root)
    if problems:
        raise EvidenceError("extraction validation failed: " + "; ".join(problems))
    if manifest.get("status") == "error":
        raise EvidenceError("extraction manifest has error status")

    documents: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_hashes: dict[str, str] = {}
    asset_hashes: dict[str, str] = {}
    seen_block_ids: set[str] = set()
    asset_by_relationship: dict[tuple[str, str], list[str]] = {}
    warnings.extend(_string_list(manifest.get("warnings", []), "manifest.warnings"))

    for raw_document in manifest["documents"]:
        document = _object(raw_document, "manifest document")
        document_id = _string(document.get("document_id"), "manifest document.document_id")
        index, document_blocks = _document_blocks(root, document)
        document_warnings = list(
            dict.fromkeys(
                _string_list(document.get("warnings", []), "document warnings")
                + _string_list(index.get("warnings", []), "document index warnings")
            )
        )
        document_errors = list(
            dict.fromkeys(
                _string_list(document.get("errors", []), "document errors")
                + _string_list(index.get("errors", []), "document index errors")
            )
        )
        warnings.extend(f"{document_id}: {item}" for item in document_warnings)
        source_hashes[document_id] = document_id
        document_assets = _validate_assets(root, index.get("assets", []), f"document {document_id}.assets")
        normalized_assets: list[dict[str, Any]] = []
        for raw_asset in document_assets:
            asset = copy.deepcopy(raw_asset)
            asset["document_id"] = document_id
            asset["sha256"] = asset["id"]
            normalized_assets.append(asset)
            assets.append(asset)
            asset_hashes[asset["id"]] = asset["id"]
            for relationship_id in asset.get("relationship_ids", []):
                if isinstance(relationship_id, str):
                    asset_by_relationship.setdefault((document_id, relationship_id), []).append(asset["id"])

        documents.append(
            {
                "document_id": document_id,
                "source": document.get("source", index.get("source", "")),
                "source_type": document.get("source_type", index.get("source_type", "")),
                "source_sha256": document_id,
                "status": index.get("status", document.get("status", "ok")),
                "warnings": document_warnings,
                "errors": document_errors,
                "index": document.get("index"),
                "assets": normalized_assets,
            }
        )
        sections: dict[tuple[str, str], list[tuple[int, str]]] = {}
        display_source_name = _source_name(document.get("source", index.get("source", "")))
        for raw_block in document_blocks:
            block = copy.deepcopy(raw_block)
            block_id = block["id"]
            if block_id in seen_block_ids:
                raise EvidenceError(f"duplicate block id across extraction documents: {block_id}")
            seen_block_ids.add(block_id)
            block["document_id"] = document_id
            block["provenance"] = {
                "document_id": document_id,
                "block_id": block_id,
                "source": document.get("source", index.get("source", "")),
                "locator": copy.deepcopy(block["locator"]),
            }
            block["citation"] = _citation_for_block(block, display_source_name, sections)
            refs: list[dict[str, str]] = []
            for relationship_id in block.get("relationship_ids", []):
                for asset_id in asset_by_relationship.get((document_id, relationship_id), []):
                    refs.append({"relationship_id": relationship_id, "asset_id": asset_id, "sha256": asset_id})
            if refs:
                block["asset_refs"] = refs
            blocks.append(block)

    payload: dict[str, Any] = {
        "format_version": EVIDENCE_STORE_VERSION,
        "frozen": True,
        "status": "warning" if warnings or manifest.get("status") == "warning" or any(document["status"] == "warning" for document in documents) else "ok",
        "manifest": {
            "format_version": manifest.get("format_version"),
            "path": "work/extracted/manifest.json",
            "sha256": sha256_file(manifest_path),
        },
        "documents": documents,
        "blocks": blocks,
        "assets": assets,
        "warnings": warnings,
        "errors": [],
        "integrity": {
            "manifest_sha256": sha256_file(manifest_path),
            "source_hashes": source_hashes,
            "asset_hashes": asset_hashes,
        },
    }
    payload["integrity"]["content_sha256"] = sha256_bytes(canonical_json(payload))
    return payload


def _without_content_hash(evidence: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(evidence)
    integrity = value.get("integrity")
    if isinstance(integrity, dict):
        integrity.pop("content_sha256", None)
    return value


def _without_derived_citations(evidence: dict[str, Any]) -> dict[str, Any]:
    """Create a comparison copy for idempotent legacy-store detection."""

    value = _without_content_hash(evidence)
    blocks = value.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict):
                block.pop("citation", None)
    return value


def validate_frozen_evidence(evidence_path: Path, *, extracted_dir: Path | None = None) -> list[str]:
    """Return integrity problems for one frozen EvidenceStore JSON file."""

    problems: list[str] = []
    try:
        evidence = _object(_read_json(Path(evidence_path), "EvidenceStore"), "EvidenceStore")
        if evidence.get("format_version") != EVIDENCE_STORE_VERSION:
            problems.append("EvidenceStore has an unsupported format_version")
        if evidence.get("frozen") is not True:
            problems.append("EvidenceStore is not frozen")
        if evidence.get("status") not in {"ok", "warning"}:
            problems.append("EvidenceStore has an invalid status")
        if evidence.get("errors"):
            problems.append("EvidenceStore contains errors")
        documents = _list(evidence.get("documents"), "EvidenceStore.documents")
        blocks = _list(evidence.get("blocks"), "EvidenceStore.blocks")
        assets = _list(evidence.get("assets"), "EvidenceStore.assets")
        integrity = _object(evidence.get("integrity"), "EvidenceStore.integrity")
        expected_content_hash = _string(integrity.get("content_sha256"), "EvidenceStore.integrity.content_sha256")
        if expected_content_hash != sha256_bytes(canonical_json(_without_content_hash(evidence))):
            problems.append("EvidenceStore content hash does not match its JSON content")
        manifest_info = _object(evidence.get("manifest"), "EvidenceStore.manifest")
        manifest_hash = _string(manifest_info.get("sha256"), "EvidenceStore.manifest.sha256")
        if integrity.get("manifest_sha256") != manifest_hash:
            problems.append("EvidenceStore manifest hashes disagree")
        source_hashes = _object(integrity.get("source_hashes"), "EvidenceStore.integrity.source_hashes")
        for source_id, source_hash in source_hashes.items():
            if not SHA256_PATTERN.fullmatch(source_id) or source_hash != source_id:
                problems.append(f"EvidenceStore source hash is invalid for {source_id}")
        document_ids = [
            _string(_object(value, f"EvidenceStore.documents[{number}]").get("document_id"), f"EvidenceStore.documents[{number}].document_id")
            for number, value in enumerate(documents, start=1)
        ]
        if set(document_ids) != set(source_hashes):
            problems.append("EvidenceStore source hashes do not cover exactly the listed documents")
        for number, raw_document in enumerate(documents, start=1):
            document = _object(raw_document, f"EvidenceStore.documents[{number}]")
            document_id = _string(document.get("document_id"), f"EvidenceStore.documents[{number}].document_id")
            if not SHA256_PATTERN.fullmatch(document_id):
                problems.append(f"EvidenceStore.documents[{number}].document_id is invalid")
            if document.get("source_sha256") != document_id:
                problems.append(f"EvidenceStore.documents[{number}] source hash does not match document_id")
            if document.get("status") not in {"ok", "warning"}:
                problems.append(f"EvidenceStore.documents[{number}] has an invalid status")
            try:
                _string_list(document.get("warnings", []), f"EvidenceStore.documents[{number}].warnings")
                if _string_list(document.get("errors", []), f"EvidenceStore.documents[{number}].errors"):
                    problems.append(f"EvidenceStore.documents[{number}] contains errors")
            except EvidenceError as exc:
                problems.append(str(exc))
        seen_blocks: set[str] = set()
        for number, raw_block in enumerate(blocks, start=1):
            block = _object(raw_block, f"EvidenceStore.blocks[{number}]")
            document_id = _string(block.get("document_id"), f"EvidenceStore.blocks[{number}].document_id")
            if document_id not in source_hashes:
                problems.append(f"EvidenceStore.blocks[{number}] references an unknown document")
            try:
                _validate_block(block, document_id, f"EvidenceStore.blocks[{number}]")
                # Citation metadata was added after EvidenceStore v1 shipped.
                # It is optional for retained stores, but must be well-formed
                # whenever a newer consolidation includes it.
                if "citation" in block:
                    _validate_citation(block["citation"], f"EvidenceStore.blocks[{number}].citation")
            except EvidenceError as exc:
                problems.append(str(exc))
            block_id = block.get("id")
            if block_id in seen_blocks:
                problems.append(f"duplicate EvidenceStore block id: {block_id}")
            seen_blocks.add(block_id)
        asset_hashes = _object(integrity.get("asset_hashes"), "EvidenceStore.integrity.asset_hashes")
        for asset_id, asset_hash in asset_hashes.items():
            if not SHA256_PATTERN.fullmatch(asset_id) or asset_hash != asset_id:
                problems.append(f"EvidenceStore asset hash is invalid for {asset_id}")
        known_asset_ids = set(asset_hashes)
        for number, raw_asset in enumerate(assets, start=1):
            asset = _object(raw_asset, f"EvidenceStore.assets[{number}]")
            asset_id = _string(asset.get("id"), f"EvidenceStore.assets[{number}].id")
            if asset.get("sha256") != asset_id or asset_hashes.get(asset_id) != asset_id:
                problems.append(f"EvidenceStore.assets[{number}] has inconsistent hashes")
            if asset.get("document_id") not in source_hashes:
                problems.append(f"EvidenceStore.assets[{number}] references an unknown document")
        for number, raw_block in enumerate(blocks, start=1):
            block = _object(raw_block, f"EvidenceStore.blocks[{number}]")
            for ref_number, raw_ref in enumerate(block.get("asset_refs", []), start=1):
                ref = _object(raw_ref, f"EvidenceStore.blocks[{number}].asset_refs[{ref_number}]")
                if ref.get("asset_id") not in known_asset_ids or ref.get("sha256") != ref.get("asset_id"):
                    problems.append(f"EvidenceStore.blocks[{number}] contains an invalid asset reference")
        if extracted_dir is not None:
            root = Path(extracted_dir)
            manifest_path = root / "manifest.json"
            if not manifest_path.is_file():
                problems.append("EvidenceStore extraction manifest is missing")
            elif sha256_file(manifest_path) != manifest_hash:
                problems.append("EvidenceStore manifest hash does not match extracted manifest")
            for number, raw_asset in enumerate(assets, start=1):
                asset = _object(raw_asset, f"EvidenceStore.assets[{number}]")
                try:
                    asset_path = _safe_relative(root, _string(asset.get("path"), "EvidenceStore asset path"), "EvidenceStore asset path")
                    if not asset_path.is_file() or sha256_file(asset_path) != asset["id"]:
                        problems.append(f"EvidenceStore asset bytes do not match asset {asset.get('id')}")
                except EvidenceError as exc:
                    problems.append(str(exc))
    except EvidenceError as exc:
        problems.append(str(exc))
    return problems


def _atomic_write_json(path: Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard link is an exclusive create on the same filesystem.  Using
        # it here preserves the first frozen store if two workers race to
        # freeze the same job; ``os.replace`` would atomically overwrite it.
        try:
            os.link(temporary_name, destination)
        except FileExistsError as exc:
            raise EvidenceError("EvidenceStore destination already exists") from exc
        os.unlink(temporary_name)
        temporary_name = None
        try:
            directory_fd = os.open(destination.parent, os.O_DIRECTORY)
        except (AttributeError, OSError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def freeze_evidence(extracted_dir: Path, evidence_path: Path) -> dict[str, Any]:
    """Validate, consolidate, and atomically freeze extraction artifacts.

    A second call is idempotent only when the candidate bytes are identical.
    A changed candidate cannot overwrite an existing frozen store, which keeps
    every author revision tied to the same factual source.
    """

    candidate = _build_store(Path(extracted_dir))
    destination = Path(evidence_path)
    if destination.exists():
        existing_problems = validate_frozen_evidence(destination, extracted_dir=Path(extracted_dir))
        if existing_problems:
            raise EvidenceError("existing frozen EvidenceStore is invalid: " + "; ".join(existing_problems))
        existing = _object(_read_json(destination, "EvidenceStore"), "EvidenceStore")
        if canonical_json(existing) == canonical_json(candidate):
            return existing
        existing_blocks = _list(existing.get("blocks"), "EvidenceStore.blocks")
        is_legacy = all(isinstance(block, dict) and "citation" not in block for block in existing_blocks)
        if is_legacy and canonical_json(_without_derived_citations(existing)) == canonical_json(_without_derived_citations(candidate)):
            # Preserve the original bytes and integrity binding. Citations are
            # derived presentation hints, not a reason to rewrite frozen facts.
            return existing
        raise EvidenceError("frozen EvidenceStore already exists and cannot be replaced")
    _atomic_write_json(destination, candidate)
    problems = validate_frozen_evidence(destination, extracted_dir=Path(extracted_dir))
    if problems:
        raise EvidenceError("written EvidenceStore failed integrity validation: " + "; ".join(problems))
    return candidate


def consolidate_evidence(extracted_dir: Path, evidence_path: Path | None = None) -> dict[str, Any]:
    """Build the EvidenceStore, optionally writing it through ``freeze_evidence``."""

    if evidence_path is None:
        return _build_store(Path(extracted_dir))
    return freeze_evidence(Path(extracted_dir), Path(evidence_path))


def load_frozen_evidence(evidence_path: Path, *, extracted_dir: Path | None = None) -> dict[str, Any]:
    """Load a frozen store only after all available integrity checks pass."""

    problems = validate_frozen_evidence(Path(evidence_path), extracted_dir=extracted_dir)
    if problems:
        raise EvidenceError("frozen EvidenceStore validation failed: " + "; ".join(problems))
    return _object(_read_json(Path(evidence_path), "EvidenceStore"), "EvidenceStore")


# Names used by integrations that prefer explicit verbs are kept as aliases;
# all entry points share the same validation and atomic-freeze behavior.
validate_extracted_artifacts = validate_extraction
verify_frozen_evidence = load_frozen_evidence


__all__ = [
    "EVIDENCE_STORE_VERSION",
    "EvidenceError",
    "canonical_json",
    "consolidate_evidence",
    "freeze_evidence",
    "load_frozen_evidence",
    "sha256_bytes",
    "sha256_file",
    "validate_extraction",
    "validate_extracted_artifacts",
    "validate_frozen_evidence",
    "verify_frozen_evidence",
]
