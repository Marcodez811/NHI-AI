"""Validate EvidenceMap v1 links against $source-document-extraction output."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from contracts import ContractError, EVIDENCE_MAP_VERSION, extracted_block_ids, load_json, require_list, require_object, require_string, sha256_file


def validate_evidence_map(manifest_path: Path, evidence_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        blocks_by_document = extracted_block_ids(manifest_path)
        evidence = require_object(load_json(evidence_path), "evidence map")
        if evidence.get("format_version") != EVIDENCE_MAP_VERSION:
            raise ContractError(f"evidence map.format_version must be {EVIDENCE_MAP_VERSION!r}")
        source_manifest = require_object(evidence.get("source_manifest"), "evidence map.source_manifest")
        expected_hash = sha256_file(manifest_path)
        if source_manifest.get("sha256") != expected_hash:
            raise ContractError("evidence map.source_manifest.sha256 does not match the extracted manifest")
        claims = require_list(evidence.get("claims"), "evidence map.claims")
        if not claims:
            raise ContractError("evidence map.claims must not be empty")
        seen_claim_ids: set[str] = set()
        for claim_index, claim_value in enumerate(claims, start=1):
            claim = require_object(claim_value, f"claims[{claim_index}]")
            claim_id = require_string(claim.get("claim_id"), f"claims[{claim_index}].claim_id")
            if claim_id in seen_claim_ids:
                raise ContractError(f"Duplicate claim_id: {claim_id}")
            seen_claim_ids.add(claim_id)
            slide = claim.get("slide_number")
            if not isinstance(slide, int) or isinstance(slide, bool) or slide < 1:
                raise ContractError(f"claims[{claim_index}].slide_number must be an integer >= 1")
            require_string(claim.get("claim"), f"claims[{claim_index}].claim")
            if claim.get("claim_type") not in {"source_fact", "derived_calculation", "interpretation"}:
                raise ContractError(f"claims[{claim_index}].claim_type is invalid")
            sources = require_list(claim.get("sources"), f"claims[{claim_index}].sources")
            if not sources:
                raise ContractError(f"claims[{claim_index}].sources must not be empty")
            for source_index, source_value in enumerate(sources, start=1):
                source = require_object(source_value, f"claims[{claim_index}].sources[{source_index}]")
                document_id = require_string(source.get("document_id"), "evidence source.document_id")
                block_id = require_string(source.get("block_id"), "evidence source.block_id")
                if not block_id.startswith("sha256:"):
                    raise ContractError(f"claim {claim_id} block_id must start with sha256:: {block_id}")
                if document_id not in blocks_by_document:
                    raise ContractError(f"claim {claim_id} references unknown document_id: {document_id}")
                if block_id not in blocks_by_document[document_id]:
                    raise ContractError(f"claim {claim_id} references unknown block_id for {document_id}: {block_id}")
    except ContractError as exc:
        errors.append(str(exc))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("evidence_map", type=Path)
    args = parser.parse_args()
    errors = validate_evidence_map(args.manifest, args.evidence_map)
    if errors:
        print("EvidenceMap validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("EvidenceMap validation PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
