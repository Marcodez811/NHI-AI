"""Shared, dependency-free helpers for evidence and PPTX QA contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


EVIDENCE_MAP_VERSION = "EvidenceMap v1"
QA_REPORT_VERSION = "QAReport v1"
REVIEW_REPORT_VERSION = "SlideReview v1"
CONTENT_CHECK_VERSION = "PPTXContentCheck v1"


class ContractError(ValueError):
    """A deterministic contract-validation failure."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"Missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"Invalid JSON in {path}: {exc.msg}") from exc


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be a JSON array")
    return value


def _candidate_paths(raw_path: str, manifest_path: Path, index_path: Path | None) -> Iterable[Path]:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        yield candidate
        return
    if index_path is not None:
        yield index_path.parent / candidate
    yield manifest_path.parent / candidate


def _load_path_or_object(value: Any, manifest_path: Path, index_path: Path | None, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raw_path = require_string(value, label)
    for candidate in _candidate_paths(raw_path, manifest_path, index_path):
        if candidate.is_file():
            return require_object(load_json(candidate), str(candidate))
    raise ContractError(f"{label} does not resolve to a JSON file: {raw_path}")


def _iter_blocks(index: dict[str, Any], manifest_path: Path, index_path: Path | None) -> Iterable[dict[str, Any]]:
    chunks = index.get("chunks", [])
    if chunks:
        for item in chunks:
            chunk = _load_path_or_object(item, manifest_path, index_path, "document index chunk")
            for block in chunk.get("blocks", []):
                if isinstance(block, dict):
                    yield block
        return
    for block in index.get("blocks", []):
        if isinstance(block, dict):
            yield block


def extracted_block_ids(manifest_path: Path) -> dict[str, set[str]]:
    """Resolve source-document-extraction v1 indexes/chunks to document block IDs."""
    manifest = require_object(load_json(manifest_path), "manifest")
    require_string(manifest.get("format_version"), "manifest.format_version")
    documents = require_list(manifest.get("documents"), "manifest.documents")
    result: dict[str, set[str]] = {}
    for position, document_value in enumerate(documents, start=1):
        document = require_object(document_value, f"manifest.documents[{position}]")
        document_id = require_string(document.get("document_id"), f"manifest.documents[{position}].document_id")
        if document_id in result:
            raise ContractError(f"Duplicate document_id in manifest: {document_id}")
        index_value = document.get("index")
        if index_value is None:
            raise ContractError(f"manifest document {document_id} has no index")
        index_path: Path | None = None
        if isinstance(index_value, str):
            candidates = list(_candidate_paths(index_value, manifest_path, None))
            index_path = next((item for item in candidates if item.is_file()), None)
            if index_path is None:
                raise ContractError(f"manifest document {document_id} index does not resolve: {index_value}")
            index = require_object(load_json(index_path), str(index_path))
        else:
            index = require_object(index_value, f"manifest document {document_id}.index")
        index_document_id = require_string(index.get("document_id"), f"index for {document_id}.document_id")
        if index_document_id != document_id:
            raise ContractError(f"index document_id {index_document_id} does not match manifest document_id {document_id}")
        block_ids: set[str] = set()
        for block in _iter_blocks(index, manifest_path, index_path):
            block_id = require_string(block.get("id"), f"block id in {document_id}")
            if not block_id.startswith("sha256:"):
                raise ContractError(f"block id for {document_id} must start with sha256:: {block_id}")
            if block_id in block_ids:
                raise ContractError(f"Duplicate block id in {document_id}: {block_id}")
            block_ids.add(block_id)
        if not block_ids:
            raise ContractError(f"No extracted blocks found for document {document_id}")
        result[document_id] = block_ids
    return result
