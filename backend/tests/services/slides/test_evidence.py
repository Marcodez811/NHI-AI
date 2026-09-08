from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.services.slides.evidence import (
    EvidenceError,
    freeze_evidence,
    load_frozen_evidence,
    validate_frozen_evidence,
)


_EXTRACTION_PATH = Path(__file__).parents[3] / ".agents" / "skills" / "source-document-extraction" / "scripts" / "source_extraction.py"
_SPEC = importlib.util.spec_from_file_location("source_extraction_for_test", _EXTRACTION_PATH)
assert _SPEC and _SPEC.loader
_EXTRACTION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EXTRACTION)


def test_markdown_and_text_extraction_preserve_order_and_structure(tmp_path: Path) -> None:
    markdown = tmp_path / "brief.md"
    markdown.write_text(
        "# Brief\n\nA source paragraph.\n\n- First point\n2. Second point\n\n| Metric | Value |\n| --- | ---: |\n| Rate | 4.1% |\n",
        encoding="utf-8",
    )
    plain = tmp_path / "notes.txt"
    plain.write_text("First line\nsecond line\n\nFinal paragraph.", encoding="utf-8")
    output = tmp_path / "extracted"

    manifest = _EXTRACTION.extract([str(markdown), str(plain)], str(output))

    assert manifest["status"] == "ok"
    assert [item["source_type"] for item in manifest["documents"]] == ["md", "txt"]
    markdown_index = json.loads((output / manifest["documents"][0]["index"]).read_text(encoding="utf-8"))
    assert [block["kind"] for block in markdown_index["blocks"]] == ["heading", "paragraph", "list_item", "list_item", "table"]
    assert markdown_index["blocks"][-1]["rows"][1][1]["text"] == "4.1%"
    text_index = json.loads((output / manifest["documents"][1]["index"]).read_text(encoding="utf-8"))
    assert [block["text"] for block in text_index["blocks"]] == ["First line\nsecond line", "Final paragraph."]


def test_freeze_consolidates_every_block_and_keeps_warning_and_hashes(tmp_path: Path) -> None:
    source = tmp_path / "brief.md"
    source.write_text("# Title\n\nClaim from the source.", encoding="utf-8")
    extracted = tmp_path / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence_path = tmp_path / "work" / "evidence.json"

    evidence = freeze_evidence(extracted, evidence_path)

    assert evidence["frozen"] is True
    assert evidence["format_version"] == "EvidenceStore v1"
    assert len(evidence["blocks"]) == 2
    assert all(block["provenance"]["document_id"] == block["document_id"] for block in evidence["blocks"])
    assert evidence["documents"][0]["source_sha256"] == evidence["documents"][0]["document_id"]
    assert evidence["integrity"]["manifest_sha256"].startswith("sha256:")
    assert validate_frozen_evidence(evidence_path, extracted_dir=extracted) == []
    assert load_frozen_evidence(evidence_path, extracted_dir=extracted)["blocks"] == evidence["blocks"]


def test_extraction_error_prevents_freezing(tmp_path: Path) -> None:
    source = tmp_path / "bad.txt"
    source.write_bytes(b"\xff\xfe\xfd")
    extracted = tmp_path / "extracted"
    manifest = _EXTRACTION.extract([str(source)], str(extracted))

    assert manifest["status"] == "error"
    with pytest.raises(EvidenceError, match="validation failed"):
        freeze_evidence(extracted, tmp_path / "evidence.json")


def test_tampering_with_frozen_content_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "brief.txt"
    source.write_text("An authoritative claim.", encoding="utf-8")
    extracted = tmp_path / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence_path = tmp_path / "evidence.json"
    freeze_evidence(extracted, evidence_path)

    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["blocks"][0]["text"] = "Tampered claim."
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    assert validate_frozen_evidence(evidence_path) != []
    with pytest.raises(EvidenceError, match="validation failed"):
        load_frozen_evidence(evidence_path)


def test_asset_hashes_and_figure_relationships_are_preserved(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("A figure source.", encoding="utf-8")
    document_id = _EXTRACTION.sha(source.read_bytes())
    extracted = tmp_path / "extracted"
    asset_bytes = b"image bytes"
    asset_id = _EXTRACTION.sha(asset_bytes)
    asset_path = extracted / "assets" / "figure.bin"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(asset_bytes)
    block = _EXTRACTION.make_block(
        document_id,
        "figure",
        "",
        {"type": "markdown", "line_start": 1, "line_end": 1},
        relationship_ids=["rId1"],
    )
    index_path = extracted / "documents" / "index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "format_version": "1.0",
                "document_id": document_id,
                "source": str(source),
                "source_type": "md",
                "status": "warning",
                "warnings": ["figure metadata only"],
                "errors": [],
                "blocks": [block],
                "chunks": [],
                "assets": [
                    {
                        "id": asset_id,
                        "kind": "embedded_media",
                        "source_part": "word/media/image1.bin",
                        "path": "assets/figure.bin",
                        "relationship_ids": ["rId1"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (extracted / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": "1.0",
                "status": "warning",
                "documents": [
                    {
                        "document_id": document_id,
                        "source": str(source),
                        "source_type": "md",
                        "status": "warning",
                        "warnings": ["figure metadata only"],
                        "errors": [],
                        "index": "documents/index.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = freeze_evidence(extracted, tmp_path / "evidence.json")

    assert evidence["assets"][0]["sha256"] == asset_id
    assert evidence["integrity"]["asset_hashes"][asset_id] == asset_id
    assert evidence["blocks"][0]["asset_refs"] == [
        {"relationship_id": "rId1", "asset_id": asset_id, "sha256": asset_id}
    ]
    assert evidence["status"] == "warning"
