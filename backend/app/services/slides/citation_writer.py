"""Resolve author-declared evidence citations into editable PPTX paragraphs."""

from __future__ import annotations

import copy
import json
import os
import posixpath
import re
import tempfile
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from lxml import etree

from .source_manifest import SlideSource, load_source_manifest
from .validation import PptxPackageError, _ordered_slide_names


_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_A = f"{{{_A_NS}}}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_CITATION = "{{CITATION}}"
_REFERENCES = "{{REFERENCES}}"
_SLIDE_NUMBER = re.compile(r"^[1-9][0-9]*$")


def _problem(code: str, message: str, *, slide_number: int | None = None, **details: Any) -> dict[str, object]:
    result: dict[str, object] = {"code": code, "message": message}
    if slide_number is not None:
        result["slide_number"] = slide_number
    if details:
        result["details"] = details
    return result


def _read_json(path: Path, label: str) -> tuple[Any, list[dict[str, object]]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return None, [_problem("missing_json", f"{label} is missing", path=label)]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [_problem("invalid_json", f"{label} is not valid JSON: {exc}", path=label)]


def _source_filename(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return posixpath.basename(value.strip().replace("\\", "/")) or None


def _locator(block: dict[str, Any]) -> str:
    locator = block.get("provenance", {}).get("locator")
    if not isinstance(locator, dict):
        locator = block.get("locator")
    if not isinstance(locator, dict):
        locator = {}
    section = block.get("citation", {}).get("section_path")
    if not isinstance(section, list):
        section = []
    section_text = "／".join(f"〈{value.strip()}〉" for value in section if isinstance(value, str) and value.strip())
    kind = locator.get("type")
    if kind == "pdf":
        page = locator.get("page")
        page_text = f"PDF 第 {page} 頁" if isinstance(page, int) and not isinstance(page, bool) and page > 0 else ""
    elif kind in {"markdown", "text"}:
        start, end = locator.get("line_start"), locator.get("line_end")
        if isinstance(start, int) and not isinstance(start, bool) and start > 0:
            page_text = f"第 {start} 行" if not isinstance(end, int) or isinstance(end, bool) or end <= start else f"第 {start}–{end} 行"
        else:
            page_text = ""
    else:
        page_text = ""
    if not page_text:
        citation = block.get("citation")
        if isinstance(citation, dict) and isinstance(citation.get("locator_label"), str):
            page_text = citation["locator_label"].strip()
    return "，".join(part for part in (section_text, page_text) if part)


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{_A}t"))


def _paragraph_with_text(template: etree._Element, text: str) -> etree._Element:
    paragraph = copy.deepcopy(template)
    ppr = paragraph.find(f"{_A}pPr")
    first_run = paragraph.find(f"{_A}r")
    run = copy.deepcopy(first_run) if first_run is not None else etree.Element(f"{_A}r")
    for child in list(run):
        if child.tag != f"{_A}rPr":
            run.remove(child)
    text_node = etree.SubElement(run, f"{_A}t")
    text_node.text = text
    for child in list(paragraph):
        paragraph.remove(child)
    if ppr is not None:
        paragraph.append(ppr)
    paragraph.append(run)
    end_properties = template.find(f"{_A}endParaRPr")
    if end_properties is not None:
        paragraph.append(copy.deepcopy(end_properties))
    return paragraph


def _replace_placeholder(paragraph: etree._Element, texts: list[str]) -> list[etree._Element]:
    parent = paragraph.getparent()
    if parent is None:
        return []
    index = parent.index(paragraph)
    replacements = [_paragraph_with_text(paragraph, text) for text in texts]
    parent.remove(paragraph)
    for offset, replacement in enumerate(replacements):
        parent.insert(index + offset, replacement)
    return replacements


def write_citations(workspace: Path) -> list[dict[str, object]]:
    """Validate citation declarations, then atomically update citation paragraphs."""
    workspace = Path(workspace)
    declaration, problems = _read_json(workspace / "work" / "slide_citations.json", "work/slide_citations.json")
    evidence, evidence_problems = _read_json(workspace / "work" / "evidence.json", "work/evidence.json")
    problems.extend(evidence_problems)
    if problems:
        return problems

    if not isinstance(declaration, dict) or set(declaration) != {"slides"} or not isinstance(declaration.get("slides"), dict):
        return [_problem("invalid_declaration", "slide_citations.json must contain only a slides object keyed by slide number")]
    raw_slides: dict[int, list[str]] = {}
    for raw_number, evidence_ids in declaration["slides"].items():
        if not isinstance(raw_number, str) or not _SLIDE_NUMBER.fullmatch(raw_number):
            problems.append(_problem("invalid_slide_number", "citation slide keys must be positive integer strings", slide_number=None, value=raw_number))
            continue
        number = int(raw_number)
        if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(value, str) or not value for value in evidence_ids):
            problems.append(_problem("invalid_evidence_ids", "each cited slide must have a non-empty array of evidence ids", slide_number=number))
            continue
        if len(evidence_ids) != len(set(evidence_ids)):
            problems.append(_problem("duplicate_evidence_id", "a slide cannot cite the same evidence id more than once", slide_number=number))
        raw_slides[number] = evidence_ids

    if not isinstance(evidence, dict) or not isinstance(evidence.get("blocks"), list) or not isinstance(evidence.get("documents"), list):
        return [_problem("invalid_evidence", "work/evidence.json has no valid blocks/documents collections")]
    blocks: dict[str, dict[str, Any]] = {}
    for block in evidence["blocks"]:
        if isinstance(block, dict) and isinstance(block.get("id"), str):
            blocks[block["id"]] = block
    for number, ids in raw_slides.items():
        for evidence_id in ids:
            if evidence_id not in blocks:
                problems.append(_problem("unknown_evidence_id", f"evidence id {evidence_id!r} is not present in work/evidence.json", slide_number=number, evidence_id=evidence_id))

    # The source manifest is backend-owned data. Its malformed state is an infrastructure error.
    sources = load_source_manifest(workspace / "work" / "sources.json")
    source_by_filename: dict[str, SlideSource] = {item.staged_filename: item for item in sources}
    document_sources: dict[str, str] = {}
    for document in evidence["documents"]:
        if isinstance(document, dict) and isinstance(document.get("document_id"), str):
            filename = _source_filename(document.get("source"))
            if filename:
                document_sources[document["document_id"]] = filename
    for block in blocks.values():
        provenance = block.get("provenance")
        filename = _source_filename(provenance.get("source")) if isinstance(provenance, dict) else None
        if filename is None:
            filename = document_sources.get(block.get("document_id"))
        if filename is None or filename not in source_by_filename:
            continue
        block["_citation_filename"] = filename

    deck = workspace / "output" / "presentation.pptx"
    # Package parsing is read-only; author input errors are returned before any package write.
    try:
        with zipfile.ZipFile(deck, "r") as archive:
            slide_names, _ = _ordered_slide_names(archive)
            slide_roots = [etree.fromstring(archive.read(name), parser=etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)) for name in slide_names]
    except (FileNotFoundError, zipfile.BadZipFile, KeyError, PptxPackageError, etree.XMLSyntaxError) as exc:
        return [_problem("citation_candidate_package_invalid", f"presentation.pptx cannot be read: {exc}")]

    slide_count = len(slide_roots)
    for number in raw_slides:
        if number >= slide_count:
            problems.append(_problem("slide_out_of_range", f"slide {number} is outside the content slides (1–{slide_count - 1})", slide_number=number))
    citation_placeholders: dict[int, etree._Element] = {}
    reference_placeholders: list[tuple[int, etree._Element]] = []
    for number, root in enumerate(slide_roots, start=1):
        shape_texts = [
            (shape, [_paragraph_text(paragraph) for paragraph in shape.iter(f"{_A}p")])
            for shape in root.iter(f"{_P}sp")
        ]
        citations = [
            next(shape.iter(f"{_A}p"))
            for shape, texts in shape_texts
            if "".join(texts) == _CITATION and len(texts) == 1
        ]
        references = [
            next(shape.iter(f"{_A}p"))
            for shape, texts in shape_texts
            if "".join(texts) == _REFERENCES and len(texts) == 1
        ]
        malformed_citations = sum(
            _CITATION in "".join(texts) and texts != [_CITATION]
            for _, texts in shape_texts
        )
        malformed_references = sum(
            _REFERENCES in "".join(texts) and texts != [_REFERENCES]
            for _, texts in shape_texts
        )
        if malformed_citations:
            problems.append(_problem("citation_placeholder_not_whole_text", "{{CITATION}} must be the entire text of one box", slide_number=number, count=malformed_citations))
        if malformed_references:
            problems.append(_problem("references_placeholder_not_whole_text", "{{REFERENCES}} must be the entire text of one box", slide_number=number, count=malformed_references))
        if number in raw_slides:
            if len(citations) != 1:
                problems.append(_problem("citation_placeholder_missing" if not citations else "citation_placeholder_ambiguous", "{{CITATION}} must occur exactly once as a whole paragraph on this declared slide", slide_number=number, count=len(citations)))
            else:
                citation_placeholders[number] = citations[0]
        elif citations:
            problems.append(_problem("undeclared_citation_placeholder", "{{CITATION}} exists on a slide with no citation declaration", slide_number=number, count=len(citations)))
        if references:
            reference_placeholders.extend((number, p) for p in references)
    if len(reference_placeholders) != 1:
        problems.append(_problem("references_placeholder_missing" if not reference_placeholders else "references_placeholder_ambiguous", "the presentation must contain exactly one whole-paragraph {{REFERENCES}} placeholder", slide_number=reference_placeholders[0][0] if reference_placeholders else None, count=len(reference_placeholders)))
    elif reference_placeholders[0][0] != slide_count:
        problems.append(_problem("references_placeholder_wrong_slide", "{{REFERENCES}} must be on the final slide", slide_number=reference_placeholders[0][0]))

    source_order: OrderedDict[str, None] = OrderedDict()
    per_slide_sources: dict[int, list[str]] = {}
    for number in sorted(raw_slides):
        ids = raw_slides[number]
        filenames: list[str] = []
        for evidence_id in ids:
            block = blocks.get(evidence_id)
            if block is None:
                continue
            filename = block.get("_citation_filename")
            if not isinstance(filename, str) or filename not in source_by_filename:
                problems.append(_problem("unresolved_evidence_source", f"evidence id {evidence_id!r} cannot be matched to a staged source", slide_number=number, evidence_id=evidence_id))
                continue
            if filename not in filenames:
                filenames.append(filename)
                source_order.setdefault(filename, None)
        per_slide_sources[number] = filenames
    if problems:
        return problems

    source_numbers = {filename: number for number, filename in enumerate(source_order, start=1)}
    source_locators: dict[str, list[str]] = {filename: [] for filename in source_order}
    for number in sorted(raw_slides):
        ids = raw_slides[number]
        for evidence_id in ids:
            block = blocks[evidence_id]
            filename = block["_citation_filename"]
            locator = _locator(block)
            if locator and locator not in source_locators[filename]:
                source_locators[filename].append(locator)

    replacements: dict[str, bytes] = {}
    for number in sorted(raw_slides):
        ids = raw_slides[number]
        entries = []
        for filename in per_slide_sources[number]:
            source = source_by_filename[filename]
            locator_set: list[str] = []
            for evidence_id in ids:
                block = blocks.get(evidence_id)
                if block is not None and block.get("_citation_filename") == filename:
                    label = _locator(block)
                    if label and label not in locator_set:
                        locator_set.append(label)
            detail = "、".join(locator_set)
            suffix = f"，{detail}" if detail else ""
            entries.append(f"[{source_numbers[filename]}] 資料來源：{source.display_name}{suffix}")
        _replace_placeholder(citation_placeholders[number], entries)
        root = slide_roots[number - 1]
        replacements[slide_names[number - 1]] = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
    references_number, references_paragraph = reference_placeholders[0]
    reference_entries = []
    for filename in source_order:
        source = source_by_filename[filename]
        locators = "、".join(source_locators[filename])
        suffix = f"，{locators}" if locators else ""
        reference_entries.append(f"[{source_numbers[filename]}] {source.display_name}{suffix}")
    _replace_placeholder(references_paragraph, reference_entries)
    references_root = slide_roots[references_number - 1]
    replacements[slide_names[references_number - 1]] = etree.tostring(references_root, encoding="UTF-8", xml_declaration=True, standalone=True)

    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{deck.name}.", suffix=".tmp", dir=deck.parent, delete=False) as handle:
            temporary = handle.name
        with zipfile.ZipFile(deck, "r") as source_archive, zipfile.ZipFile(temporary, "w") as destination_archive:
            for info in source_archive.infolist():
                data = replacements.get(info.filename)
                if data is None:
                    data = source_archive.read(info.filename)
                destination_archive.writestr(info, data)
        os.chmod(temporary, deck.stat().st_mode)
        os.replace(temporary, deck)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    return []
