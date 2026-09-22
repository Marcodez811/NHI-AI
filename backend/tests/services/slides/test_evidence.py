from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.services.slides.evidence import (
    EvidenceError,
    _citation_for_block,
    canonical_json,
    freeze_evidence,
    load_frozen_evidence,
    render_compact_evidence,
    sha256_bytes,
    validate_frozen_evidence,
)
from app.services.slides.source_manifest import SlideSource


_EXTRACTION_PATH = Path(__file__).parents[3] / ".agents" / "skills" / "source-document-extraction" / "scripts" / "source_extraction.py"
_SPEC = importlib.util.spec_from_file_location("source_extraction_for_test", _EXTRACTION_PATH)
assert _SPEC and _SPEC.loader
_EXTRACTION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_EXTRACTION)


def test_human_citations_use_pdf_pages_and_safe_filename_fallbacks() -> None:
    pdf = _citation_for_block(
        {"kind": "paragraph", "text": "claim", "locator": {"type": "pdf", "page": 12}},
        "年度報告.pdf",
        {},
    )
    unknown = _citation_for_block(
        {"kind": "paragraph", "text": "claim", "locator": {"type": "docx", "path": "/internal/xml/path"}},
        "政策說明.docx",
        {},
    )

    assert pdf["display_text"] == "資料來源：年度報告.pdf，PDF 第 12 頁"
    assert unknown["display_text"] == "資料來源：政策說明.docx"
    assert "/internal/xml/path" not in unknown["display_text"]


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
    assert evidence["blocks"][0]["citation"] == {
        "source_name": "brief.md",
        "section_path": ["Title"],
        "locator_label": "第 1 行",
        "display_text": "資料來源：brief.md，〈Title〉，第 1 行",
    }
    assert evidence["blocks"][1]["citation"]["display_text"] == "資料來源：brief.md，〈Title〉，第 3 行"
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


def test_older_frozen_store_without_citation_metadata_remains_valid(tmp_path: Path) -> None:
    source = tmp_path / "brief.txt"
    source.write_text("An authoritative claim.", encoding="utf-8")
    extracted = tmp_path / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence_path = tmp_path / "evidence.json"
    freeze_evidence(extracted, evidence_path)

    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    for block in payload["blocks"]:
        block.pop("citation")
    without_hash = json.loads(json.dumps(payload))
    without_hash["integrity"].pop("content_sha256")
    payload["integrity"]["content_sha256"] = sha256_bytes(canonical_json(without_hash))
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    legacy_bytes = evidence_path.read_bytes()

    assert validate_frozen_evidence(evidence_path, extracted_dir=extracted) == []
    assert freeze_evidence(extracted, evidence_path) == payload
    assert evidence_path.read_bytes() == legacy_bytes


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


def test_citations_keep_docx_headings_with_their_document_part(tmp_path: Path) -> None:
    source = tmp_path / "政策說明.docx"
    source.write_bytes(b"document")
    document_id = _EXTRACTION.sha(source.read_bytes())
    extracted = tmp_path / "extracted"
    blocks = [
        _EXTRACTION.make_block(
            document_id,
            "heading",
            "給付範圍",
            {"type": "docx", "part": "word/document.xml", "path": "/w:document/w:body/*[1]"},
            heading_level=1,
        ),
        _EXTRACTION.make_block(
            document_id,
            "heading",
            "適用對象",
            {"type": "docx", "part": "word/document.xml", "path": "/w:document/w:body/*[2]"},
            heading_level=2,
        ),
        _EXTRACTION.make_block(
            document_id,
            "paragraph",
            "適用條件。",
            {"type": "docx", "part": "word/document.xml", "path": "/w:document/w:body/*[3]"},
        ),
        _EXTRACTION.make_block(
            document_id,
            "footnote",
            "附註。",
            {"type": "docx", "part": "word/footnotes.xml", "note_id": "1"},
        ),
    ]
    index = extracted / "documents" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text(
        json.dumps(
            {
                "format_version": "1.0",
                "document_id": document_id,
                "source": str(source),
                "source_type": "docx",
                "status": "ok",
                "warnings": [],
                "errors": [],
                "blocks": blocks,
                "chunks": [],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    (extracted / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": "1.0",
                "status": "ok",
                "warnings": [],
                "errors": [],
                "documents": [
                    {
                        "document_id": document_id,
                        "source": str(source),
                        "source_type": "docx",
                        "status": "ok",
                        "warnings": [],
                        "errors": [],
                        "index": "documents/index.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = freeze_evidence(extracted, tmp_path / "evidence.json")

    assert evidence["blocks"][2]["citation"]["display_text"] == "資料來源：政策說明.docx，〈給付範圍〉／〈適用對象〉"
    assert evidence["blocks"][3]["citation"]["section_path"] == []


# ---------------------------------------------------------------------------
# Compact evidence rendering for the "agents" planner path
# (docs/agents-sdk-migration-plan.md, Stage 5)
# ---------------------------------------------------------------------------


def test_render_compact_evidence_keeps_id_text_citation_and_display_name(tmp_path: Path) -> None:
    source = tmp_path / "brief.txt"
    source.write_text("An authoritative claim from the source.", encoding="utf-8")
    extracted = tmp_path / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence = freeze_evidence(extracted, tmp_path / "evidence.json")
    sources = (SlideSource(staged_filename="brief.txt", display_name="Q3 Policy Brief"),)

    rendered = render_compact_evidence(evidence, sources, max_chars=10_000)

    block = evidence["blocks"][0]
    assert rendered.startswith("<evidence>\n")
    assert rendered.endswith("\n</evidence>")
    assert "never as instructions" in rendered
    assert "untrusted" in rendered
    assert block["id"] in rendered
    assert "An authoritative claim from the source." in rendered
    assert "Q3 Policy Brief" in rendered
    # The staged filename and every hash/provenance/integrity field the schema
    # carries must never reach the compact rendering.
    assert "brief.txt" not in rendered
    assert block["document_id"] not in rendered
    assert "provenance" not in rendered
    assert "integrity" not in rendered
    assert "manifest_sha256" not in rendered
    assert "asset_hashes" not in rendered


def test_render_compact_evidence_falls_back_to_the_raw_locator_without_a_citation(tmp_path: Path) -> None:
    source = tmp_path / "brief.txt"
    source.write_text("A claim with no derived citation.", encoding="utf-8")
    extracted = tmp_path / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence_path = tmp_path / "evidence.json"
    freeze_evidence(extracted, evidence_path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    for block in payload["blocks"]:
        block.pop("citation")
    sources = (SlideSource(staged_filename="brief.txt", display_name="Legacy Brief"),)

    rendered = render_compact_evidence(payload, sources, max_chars=10_000)

    assert "Legacy Brief" in rendered
    # No citation label survived the strip in this fixture, so the compact
    # rendering falls back to the raw locator the citation would have used.
    assert '"citation":{"line_end":1,"line_start":1,"type":"text"}' in rendered


def test_render_compact_evidence_rejects_a_staged_filename_with_no_display_name(tmp_path: Path) -> None:
    source = tmp_path / "brief.txt"
    source.write_text("A claim from an unmapped source.", encoding="utf-8")
    extracted = tmp_path / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence = freeze_evidence(extracted, tmp_path / "evidence.json")

    with pytest.raises(EvidenceError, match="no knowledge-base source name"):
        render_compact_evidence(evidence, (), max_chars=10_000)


def test_render_compact_evidence_fails_fast_over_the_character_budget_without_truncating(tmp_path: Path) -> None:
    source = tmp_path / "brief.txt"
    source.write_text("A claim long enough to exceed a tiny budget.", encoding="utf-8")
    extracted = tmp_path / "extracted"
    _EXTRACTION.extract([str(source)], str(extracted))
    evidence = freeze_evidence(extracted, tmp_path / "evidence.json")
    sources = (SlideSource(staged_filename="brief.txt", display_name="Brief"),)

    with pytest.raises(EvidenceError, match="over the planner's 1 character limit"):
        render_compact_evidence(evidence, sources, max_chars=1)
