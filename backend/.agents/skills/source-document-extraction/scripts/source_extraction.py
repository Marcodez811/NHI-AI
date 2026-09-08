"""Deterministic, read-only DOCX/PDF extraction helpers."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable
try:
    from defusedxml import ElementTree as ET
except ImportError:  # The runner preflight requires defusedxml; keep unit fixtures runnable.
    from xml.etree import ElementTree as ET

FORMAT_VERSION = "1.0"
MAX_CHARS = 40_000
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "r": R}
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS["a"] = A
KINDS = {"heading", "paragraph", "list_item", "table", "figure", "footnote"}
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
SUPPORTED_SUFFIXES = {".docx", ".pdf", *TEXT_SUFFIXES}


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def normalized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def status_for(warnings: list[str], errors: list[str]) -> str:
    return "error" if errors else "warning" if warnings else "ok"


def safe_docx(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"corrupt DOCX ZIP: {exc}") from exc
    infos = archive.infolist()
    if len(infos) > 10_000 or sum(info.file_size for info in infos) > 1_000_000_000:
        archive.close(); raise ValueError("unsafe DOCX ZIP: archive size or entry count exceeds limits")
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            archive.close(); raise ValueError("unsafe DOCX ZIP: path traversal entry")
        if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
            archive.close(); raise ValueError("unsafe DOCX ZIP: symlink entry")
        if info.compress_size and info.file_size / info.compress_size > 200:
            archive.close(); raise ValueError("unsafe DOCX ZIP: suspicious compression ratio")
    return archive


def xml_from(archive: zipfile.ZipFile, part: str) -> ET.Element | None:
    try:
        return ET.fromstring(archive.read(part))
    except KeyError:
        return None
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML in {part}: {exc}") from exc


def relationship_targets(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    root = xml_from(archive, part)
    if root is None: return {}
    return {r.get("Id", ""): r.get("Target", "") for r in root.findall(f"{{{REL}}}Relationship") if r.get("TargetMode") == "External"}


def relationship_targets_all(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Return internal and external relationship targets for asset linking."""

    root = xml_from(archive, part)
    if root is None:
        return {}
    return {
        relationship.get("Id", ""): relationship.get("Target", "")
        for relationship in root.findall(f"{{{REL}}}Relationship")
        if relationship.get("Id") and relationship.get("Target")
    }


def relationship_part(part: str, target: str) -> str:
    """Resolve an OOXML relationship target to a normalized package part."""

    return posixpath.normpath(posixpath.join(posixpath.dirname(part), target)).lstrip("/")


def accepted_text(element: ET.Element) -> tuple[str, list[dict[str, str]]]:
    pieces: list[str] = []; links: list[dict[str, str]] = []
    for child in list(element):
        if child.tag == f"{{{W}}}del": continue
        # Text boxes are emitted as independently locatable blocks by extract_docx.
        if child.tag == f"{{{W}}}txbxContent": continue
        if child.tag == f"{{{W}}}hyperlink":
            text, _ = accepted_text(child)
            if text: links.append({"text": text, "relationship_id": child.get(f"{{{R}}}id", "")})
            pieces.append(text)
        elif child.tag == f"{{{W}}}tab": pieces.append("\t")
        elif child.tag in (f"{{{W}}}br", f"{{{W}}}cr"): pieces.append("\n")
        elif child.tag == f"{{{W}}}t": pieces.append(child.text or "")
        else:
            text, nested = accepted_text(child); pieces.append(text); links.extend(nested)
    return "".join(pieces), links


def p_style(paragraph: ET.Element) -> str:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    return style.get(f"{{{W}}}val", "") if style is not None else ""


def paragraph_kind(paragraph: ET.Element) -> str:
    style = p_style(paragraph).lower()
    if style.startswith("heading") or style in {"title", "subtitle"}: return "heading"
    return "list_item" if paragraph.find("w:pPr/w:numPr", NS) is not None else "paragraph"


def make_block(document_id: str, kind: str, text: str, locator: dict[str, Any], **extra: Any) -> dict[str, Any]:
    key = {"document_id": document_id, "kind": kind, "text": text, "locator": locator}
    block = {"id": sha(normalized(key)), "kind": kind, "text": text, "locator": locator}
    block.update({k: v for k, v in extra.items() if v not in (None, [], {}, "")})
    return block


def paragraph_block(document_id: str, paragraph: ET.Element, locator: dict[str, Any], targets: dict[str, str]) -> dict[str, Any] | None:
    text, links = accepted_text(paragraph)
    if not text.strip(): return None
    for link in links:
        if link["relationship_id"] in targets: link["target"] = targets[link["relationship_id"]]
    note_refs = [
        {"type": kind, "note_id": node.get(f"{{{W}}}id", "")}
        for kind, tag in (("footnote", "footnoteReference"), ("endnote", "endnoteReference"))
        for node in paragraph.findall(f".//w:{tag}", NS)
    ]
    style = p_style(paragraph)
    heading_match = re.search(r"(\d+)$", style)
    return make_block(
        document_id,
        paragraph_kind(paragraph),
        text,
        locator,
        style=style,
        heading_level=int(heading_match.group(1)) if heading_match else None,
        hyperlinks=links,
        note_references=note_refs,
    )


def table_block(document_id: str, table: ET.Element, locator: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for ri, row in enumerate(table.findall("w:tr", NS)):
        out = []
        for ci, cell in enumerate(row.findall("w:tc", NS)):
            props = cell.find("w:tcPr", NS); span = props.find("w:gridSpan", NS) if props is not None else None; merge = props.find("w:vMerge", NS) if props is not None else None
            out.append({"row": ri, "column": ci, "text": "\n".join(t for t in (accepted_text(p)[0] for p in cell.findall("w:p", NS)) if t), "colspan": int(span.get(f"{{{W}}}val", "1")) if span is not None else 1, "vertical_merge": (merge.get(f"{{{W}}}val") or "continue") if merge is not None else None})
        rows.append(out)
    return make_block(document_id, "table", "\n".join(" | ".join(c["text"] for c in r) for r in rows), locator, rows=rows)


def extract_docx(path: Path, output: Path, document_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    blocks: list[dict[str, Any]] = []; assets: list[dict[str, Any]] = []; warnings: list[str] = []
    with safe_docx(path) as archive:
        document = xml_from(archive, "word/document.xml")
        if document is None: raise ValueError("invalid DOCX: word/document.xml is missing")
        rels_part = "word/_rels/document.xml.rels"
        targets = relationship_targets(archive, rels_part)
        all_relationships = relationship_targets_all(archive, rels_part)
        body = document.find("w:body", NS)
        if body is None: raise ValueError("invalid DOCX: document body is missing")
        for index, element in enumerate(list(body)):
            locator = {"type": "docx", "part": "word/document.xml", "path": f"/w:document/w:body/*[{index + 1}]"}
            if element.tag == f"{{{W}}}p":
                block = paragraph_block(document_id, element, locator, targets)
                if block: blocks.append(block)
                for di, drawing in enumerate(element.findall(".//w:drawing", NS)):
                    relationship_ids = sorted(
                        {
                            blip.get(f"{{{R}}}embed", "")
                            for blip in drawing.findall(".//a:blip", NS)
                            if blip.get(f"{{{R}}}embed")
                        }
                    )
                    blocks.append(
                        make_block(
                            document_id,
                            "figure",
                            "",
                            {
                                "type": "docx",
                                "part": "word/document.xml",
                                "path": locator["path"] + f"/w:drawing[{di + 1}]",
                            },
                            relationship_ids=relationship_ids,
                        )
                    )
                for ti, textbox in enumerate(element.findall(".//w:txbxContent", NS)):
                    for pi, text_p in enumerate(textbox.findall("w:p", NS)):
                        block = paragraph_block(document_id, text_p, {"type": "docx", "part": "word/document.xml", "path": locator["path"] + f"/w:txbxContent[{ti + 1}]/w:p[{pi + 1}]"}, targets)
                        if block: blocks.append(block)
            elif element.tag == f"{{{W}}}tbl": blocks.append(table_block(document_id, element, locator))
        for prefix, label in (("word/header", "header"), ("word/footer", "footer")):
            for part in sorted(n for n in archive.namelist() if n.startswith(prefix) and n.endswith(".xml")):
                root = xml_from(archive, part)
                if root is not None:
                    for i, p in enumerate(root.findall("w:p", NS)):
                        block = paragraph_block(document_id, p, {"type": "docx", "part": part, "path": f"/{label}/w:p[{i + 1}]"}, {})
                        if block: block["region"] = label; blocks.append(block)
        for part in ("word/footnotes.xml", "word/endnotes.xml"):
            root = xml_from(archive, part)
            if root is not None:
                for note in list(root):
                    note_id = note.get(f"{{{W}}}id", ""); text, _ = accepted_text(note)
                    if text.strip() and not note_id.startswith("-"): blocks.append(make_block(document_id, "footnote", text, {"type": "docx", "part": part, "note_id": note_id}))
        asset_dir = output / "assets" / document_id.split(":", 1)[1]
        for name in sorted(n for n in archive.namelist() if n.startswith("word/media/") and not n.endswith("/")):
            content = archive.read(name)
            digest = sha(content).split(":", 1)[1]
            suffix = Path(name).suffix.lower() or ".bin"
            target = asset_dir / f"{digest}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            relationship_ids = [
                relationship_id
                for relationship_id, relationship_target in all_relationships.items()
                if relationship_part("word/document.xml", relationship_target) == name
            ]
            assets.append(
                {
                    "id": sha(content),
                    "kind": "embedded_media",
                    "source_part": name,
                    "path": str(target.relative_to(output)),
                    "mime_hint": suffix.lstrip("."),
                    "relationship_ids": relationship_ids,
                }
            )
    return blocks, assets, warnings, []


def import_optional(name: str) -> Any | None:
    try: return __import__(name)
    except ImportError: return None


def pdf_locator(page: int, bbox: list[float] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "pdf", "page": page}
    if bbox is not None: out["bbox"] = [round(float(n), 3) for n in bbox]
    return out


def ocr_page(pdfium: Any, pytesseract: Any, path: Path, number: int) -> tuple[str, list[float] | None, float]:
    pdf = pdfium.PdfDocument(str(path)); bitmap = pdf[number - 1].render(scale=300 / 72); image = bitmap.to_pil()
    data = pytesseract.image_to_data(image, lang="chi_tra+eng", output_type=pytesseract.Output.DICT)
    active = [(i, w) for i, w in enumerate(data["text"]) if w.strip()]; conf = [float(data["conf"][i]) for i, _ in active if float(data["conf"][i]) >= 0]
    bbox = None
    if active:
        bbox = [min(data["left"][i] for i, _ in active) * 72 / 300, min(data["top"][i] for i, _ in active) * 72 / 300, max(data["left"][i] + data["width"][i] for i, _ in active) * 72 / 300, max(data["top"][i] + data["height"][i] for i, _ in active) * 72 / 300]
    return " ".join(w for _, w in active), bbox, sum(conf) / len(conf) if conf else 0.0


def extract_pdf(path: Path, output: Path, document_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    del output
    warnings: list[str] = []; blocks: list[dict[str, Any]] = []; pdfplumber = import_optional("pdfplumber"); pypdf = import_optional("pypdf")
    if not pdfplumber and not pypdf: return blocks, [], warnings, ["PDF extraction requires optional dependency pdfplumber or pypdf"]
    if pdfplumber:
        try:
            with pdfplumber.open(str(path)) as pdf:
                for number, page in enumerate(pdf.pages, 1):
                    words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []; text = " ".join(w.get("text", "") for w in words).strip()
                    if text:
                        bbox = [min(w["x0"] for w in words), min(w["top"] for w in words), max(w["x1"] for w in words), max(w["bottom"] for w in words)]
                        blocks.append(make_block(document_id, "paragraph", text, pdf_locator(number, bbox), extraction="pdfplumber_words"))
                    detected_tables = page.find_tables()
                    for ti, detected in enumerate(detected_tables):
                        table = detected.extract()
                        rows = [[{"row": ri, "column": ci, "text": cell or "", "colspan": 1, "vertical_merge": None} for ci, cell in enumerate(row)] for ri, row in enumerate(table)]
                        blocks.append(make_block(document_id, "table", "\n".join(" | ".join(c["text"] for c in row) for row in rows), {**pdf_locator(number, list(detected.bbox)), "table_index": ti}, rows=rows))
                    for ii, image in enumerate(page.images or []):
                        bbox = [image.get("x0", 0), image.get("top", 0), image.get("x1", 0), image.get("bottom", 0)]
                        blocks.append(make_block(document_id, "figure", "", {**pdf_locator(number, bbox), "image_index": ii}, metadata={"width": image.get("width"), "height": image.get("height")}))
                    if len(text) < 8:
                        pdfium, tess = import_optional("pypdfium2"), import_optional("pytesseract")
                        if not pdfium or not tess: return blocks, [], warnings, [f"page {number} has low/no text; OCR requires pypdfium2 and pytesseract with chi_tra+eng data"]
                        try: ocr_text, bbox, confidence = ocr_page(pdfium, tess, path, number)
                        except Exception as exc: return blocks, [], warnings, [f"page {number} OCR unavailable: {exc}"]
                        if ocr_text: blocks.append(make_block(document_id, "paragraph", ocr_text, pdf_locator(number, bbox), extraction="ocr_300dpi_chi_tra+eng", confidence=round(confidence, 2)))
                        if confidence < 60: warnings.append(f"page {number} OCR confidence {confidence:.1f} is below 60")
            return blocks, [], warnings, []
        except Exception as exc:
            message = str(exc).lower()
            return blocks, [], warnings, ["encrypted PDF cannot be extracted without a password" if "password" in message or "encrypt" in message else f"corrupt or unreadable PDF: {exc}"]
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted: return blocks, [], warnings, ["encrypted PDF cannot be extracted without a password"]
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if not text: return blocks, [], warnings, [f"page {number} has low/no text; install pdfplumber plus pypdfium2 and pytesseract for OCR"]
            blocks.append(make_block(document_id, "paragraph", text, pdf_locator(number), extraction="pypdf_text_only"))
        warnings.append("pypdf fallback has no reliable text bounding boxes, tables, or figure detection")
        return blocks, [], warnings, []
    except Exception as exc: return blocks, [], warnings, [f"corrupt or unreadable PDF: {exc}"]


def _markdown_table_cells(line: str) -> list[str]:
    """Split one Markdown table row while tolerating optional edge pipes."""

    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if character == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
            continue
        if character == "\\" and not escaped:
            escaped = True
            current.append(character)
            continue
        escaped = False
        current.append(character)
    cells.append("".join(current).strip())
    return cells


def _is_markdown_table_delimiter(line: str) -> bool:
    cells = _markdown_table_cells(line)
    return len(cells) >= 1 and all(re.fullmatch(r":?-{1,}:?", cell.replace(" ", "")) for cell in cells)


def _is_table_row(line: str) -> bool:
    value = line.strip()
    return "|" in value and len(_markdown_table_cells(value)) >= 2


def extract_text(path: Path, _output: Path, document_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    """Extract Markdown or plain text into ordered, line-addressable blocks."""

    try:
        # UTF-8 with an optional BOM is the portable interchange format for
        # these artifacts.  Decoding errors are extraction errors, rather than
        # silently replacing source bytes with a different factual value.
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return [], [], [], [f"text source could not be decoded as UTF-8: {exc}"]

    source_type = "markdown" if path.suffix.lower() in {".md", ".markdown"} else "text"
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        # A Markdown table is recognized only when a header is followed by a
        # delimiter row.  This avoids classifying ordinary prose containing a
        # single pipe as a table.
        if source_type == "markdown" and index + 1 < len(lines) and _is_table_row(line) and _is_markdown_table_delimiter(lines[index + 1]):
            start = index
            index += 2
            while index < len(lines) and lines[index].strip() and _is_table_row(lines[index]):
                index += 1
            # The delimiter row is Markdown syntax, not source data. Preserve
            # the header and data rows while keeping the locator over the full
            # source range for auditability.
            table_lines = [lines[start], *lines[start + 2 : index]]
            rows = [
                [
                    {"row": row_index, "column": column_index, "text": cell, "colspan": 1, "vertical_merge": None}
                    for column_index, cell in enumerate(_markdown_table_cells(row))
                ]
                for row_index, row in enumerate(table_lines)
            ]
            locator = {"type": source_type, "line_start": start + 1, "line_end": index}
            blocks.append(make_block(document_id, "table", "\n".join(table_lines), locator, rows=rows))
            continue

        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level = len(heading.group(1))
            blocks.append(
                make_block(
                    document_id,
                    "heading",
                    heading.group(2).strip(),
                    {"type": source_type, "line_start": index + 1, "line_end": index + 1},
                    heading_level=level,
                )
            )
            index += 1
            continue

        list_item = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$", line)
        if list_item:
            blocks.append(
                make_block(
                    document_id,
                    "list_item",
                    list_item.group(1).strip(),
                    {"type": source_type, "line_start": index + 1, "line_end": index + 1},
                )
            )
            index += 1
            continue

        start = index
        paragraph_lines = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip():
            if re.match(r"^\s{0,3}#{1,6}\s+", lines[index]) or re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", lines[index]):
                break
            if source_type == "markdown" and index + 1 < len(lines) and _is_table_row(lines[index]) and _is_markdown_table_delimiter(lines[index + 1]):
                break
            paragraph_lines.append(lines[index].strip())
            index += 1
        blocks.append(
            make_block(
                document_id,
                "paragraph",
                "\n".join(paragraph_lines),
                {"type": source_type, "line_start": start + 1, "line_end": index},
            )
        )

    if not blocks:
        warnings.append("source contains no non-empty extractable blocks")
    return blocks, [], warnings, []


def chunks_for(document_id: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []; current: list[dict[str, Any]] = []; count = 0
    for block in blocks:
        size = len(block["text"])
        if current and count + size > MAX_CHARS:
            chunks.append({"format_version": FORMAT_VERSION, "document_id": document_id, "chunk": len(chunks) + 1, "blocks": current, "character_count": count}); current, count = [], 0
        current.append(block); count += size
    if current: chunks.append({"format_version": FORMAT_VERSION, "document_id": document_id, "chunk": len(chunks) + 1, "blocks": current, "character_count": count})
    return chunks


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_paths(inputs: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            paths.extend(c for c in path.rglob("*") if c.is_file() and c.suffix.lower() in SUPPORTED_SUFFIXES)
        elif path.is_file(): paths.append(path)
        else: raise FileNotFoundError(f"source does not exist: {path}")
    return sorted(set(paths), key=lambda value: str(value.resolve()))


def extract_one(path: Path, output: Path) -> dict[str, Any]:
    document_id = sha(path.read_bytes()); kind = path.suffix.lower().lstrip("."); warnings: list[str] = []; errors: list[str] = []; blocks: list[dict[str, Any]] = []; assets: list[dict[str, Any]] = []
    try:
        if kind == "docx":
            blocks, assets, warnings, errors = extract_docx(path, output, document_id)
        elif kind == "pdf":
            blocks, assets, warnings, errors = extract_pdf(path, output, document_id)
        elif path.suffix.lower() in TEXT_SUFFIXES:
            blocks, assets, warnings, errors = extract_text(path, output, document_id)
        else: errors = [f"unsupported source type: {path.suffix}"]
    except (OSError, ValueError) as exc: errors = [str(exc)]
    oversized = [block for block in blocks if len(block["text"]) >= MAX_CHARS]
    if oversized and not errors:
        non_tables = [block for block in oversized if block["kind"] != "table"]
        if non_tables:
            errors = [f"block {block['id']} is {len(block['text'])} characters and cannot be chunked below {MAX_CHARS} without splitting it" for block in non_tables]
        else:
            warnings.extend(f"table {block['id']} is {len(block['text'])} characters and was kept intact rather than split" for block in oversized)
    digest = document_id.split(":", 1)[1]; index_path = Path("documents") / digest / "index.json"; chunk_paths = []
    if not errors:
        for chunk in chunks_for(document_id, blocks):
            relative = Path("chunks") / digest / f"chunk-{chunk['chunk']:04d}.json"; write_json(output / relative, chunk); chunk_paths.append(str(relative))
    index = {"format_version": FORMAT_VERSION, "document_id": document_id, "source": str(path), "source_type": kind, "status": status_for(warnings, errors), "warnings": warnings, "errors": errors, "blocks": blocks, "chunks": chunk_paths, "assets": assets}
    write_json(output / index_path, index)
    return {"document_id": document_id, "source": str(path), "source_type": kind, "status": index["status"], "warnings": warnings, "errors": errors, "index": str(index_path)}


def extract(inputs: Iterable[str], output_dir: str) -> dict[str, Any]:
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True); documents: list[dict[str, Any]] = []
    try: paths = source_paths(inputs)
    except FileNotFoundError as exc: documents.append({"document_id": sha(str(exc)), "source": "", "source_type": "unknown", "status": "error", "warnings": [], "errors": [str(exc)], "index": ""}); paths = []
    if not paths and not documents: documents.append({"document_id": sha("no sources"), "source": "", "source_type": "unknown", "status": "error", "warnings": [], "errors": ["no supported source files found"], "index": ""})
    for path in paths: documents.append(extract_one(path, output))
    manifest = {"format_version": FORMAT_VERSION, "status": status_for([w for d in documents for w in d["warnings"]], [e for d in documents for e in d["errors"]]), "documents": documents}; write_json(output / "manifest.json", manifest); return manifest


def validate_output(output_dir: str) -> list[str]:
    output = Path(output_dir); problems: list[str] = []
    try: manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: return [f"cannot read manifest.json: {exc}"]
    if manifest.get("format_version") != FORMAT_VERSION: problems.append("manifest has an unsupported format_version")
    if manifest.get("status") not in {"ok", "warning", "error"}: problems.append("manifest has invalid status")
    for document in manifest.get("documents", []):
        doc_id = document.get("document_id", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", doc_id): problems.append(f"invalid document id: {doc_id}")
        index_rel = document.get("index", "")
        if not index_rel: continue
        try: index = json.loads((output / index_rel).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: problems.append(f"cannot read index {index_rel}: {exc}"); continue
        if index.get("document_id") != doc_id: problems.append(f"index id mismatch: {index_rel}")
        index_block_ids: list[str] = []
        for block in index.get("blocks", []):
            if block.get("kind") not in KINDS or not isinstance(block.get("text"), str) or not isinstance(block.get("locator"), dict): problems.append(f"invalid block in {index_rel}"); continue
            expected = sha(normalized({"document_id": doc_id, "kind": block["kind"], "text": block["text"], "locator": block["locator"]}))
            if block.get("id") != expected: problems.append(f"unstable block id {block.get('id')} in {index_rel}")
            index_block_ids.append(block.get("id", ""))
        chunk_block_ids: list[str] = []
        for chunk_rel in index.get("chunks", []):
            chunk_path = output / chunk_rel
            if not chunk_path.is_file():
                problems.append(f"missing chunk {chunk_rel}")
                continue
            try: chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"cannot read chunk {chunk_rel}: {exc}")
                continue
            if chunk.get("format_version") != FORMAT_VERSION or chunk.get("document_id") != doc_id:
                problems.append(f"chunk contract mismatch: {chunk_rel}")
            chunk_blocks = chunk.get("blocks")
            if not isinstance(chunk_blocks, list):
                problems.append(f"chunk blocks must be an array: {chunk_rel}")
                continue
            actual_char_count = sum(len(block.get("text", "")) for block in chunk_blocks if isinstance(block, dict))
            if chunk.get("character_count") != actual_char_count:
                problems.append(f"chunk character_count mismatch: {chunk_rel}")
            chunk_block_ids.extend(block.get("id", "") for block in chunk_blocks if isinstance(block, dict))
        if chunk_block_ids != index_block_ids:
            problems.append(f"chunk block order/content does not match index blocks: {index_rel}")
        if len(index_block_ids) != len(set(index_block_ids)):
            problems.append(f"duplicate block id in index: {index_rel}")
    return problems


def extract_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract DOCX, PDF, Markdown, and text files into structured source artifacts.")
    parser.add_argument("inputs", nargs="+", help="source files or directories")
    parser.add_argument("--output-dir", required=True, help="artifact output directory")
    args = parser.parse_args(argv)
    manifest = extract(args.inputs, args.output_dir); print(json.dumps({"status": manifest["status"], "manifest": str(Path(args.output_dir) / "manifest.json")}, ensure_ascii=False)); return 2 if manifest["status"] == "error" else 0


def validate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate source extraction artifacts."); parser.add_argument("output_dir", help="directory containing manifest.json"); args = parser.parse_args(argv)
    problems = validate_output(args.output_dir)
    if problems: print("\n".join(problems), file=sys.stderr); return 1
    print("valid"); return 0
