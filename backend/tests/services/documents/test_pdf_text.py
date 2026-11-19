from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.chat.attachments import ChatAttachmentStorage, TEXT_EXTRACTION_VERSION
from app.services.documents.pdf_text import extract_pdf_text


def _page(texts: list[tuple[float, float, str]], lines: list[tuple[float, float, float, float]]) -> bytes:
    ops = [f"BT /F1 10 Tf {x} {y} Td ({t}) Tj ET" for x, y, t in texts]
    ops += [f"{x1} {y1} m {x2} {y2} l S" for x1, y1, x2, y2 in lines]
    return "\n".join(ops).encode()


def make_pdf(path: Path, pages: list[bytes]) -> None:
    objs: list[bytes] = []
    n = len(pages)
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(n))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + n * 2
    for i, content in enumerate(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents {4 + i * 2} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


XS = [50, 130, 210, 290, 370, 450]
HEAD = [
    (55, 686, "Quarter"), (135, 686, "Dental"), (295, 686, "Chinese"),
    (55, 666, "Year"), (135, 666, "Float"), (215, 666, "Avg"), (295, 666, "Float"), (375, 666, "Avg"),
]


def _table_page(rows: list[tuple[str, ...]], with_header: bool = True, intro: str | None = None) -> bytes:
    texts = list(HEAD) if with_header else []
    y_top = 700 if with_header else 680
    first_row_y = 646 if with_header else 666
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            texts.append((55 + j * 80, first_row_y - i * 20, cell))
    if intro:
        texts.append((50, 750, intro))
    bottom = y_top - (40 if with_header else 0) - 20 * len(rows)
    ys = [y_top - 20 * k for k in range(0, (y_top - bottom) // 20 + 1)]
    lines = [(XS[0], y, XS[-1], y) for y in ys]
    # Header row 1 keeps merged groups: no inner verticals at 210 / 370 there.
    for x in XS:
        if with_header:
            lines.append((x, ys[1], x, bottom))
            if x not in (210, 370):
                lines.append((x, ys[0], x, ys[1]))
        else:
            lines.append((x, ys[0], x, bottom))
    if with_header:
        lines.remove((XS[0], ys[0], XS[-1], ys[0])) if False else None
    return _page(texts, lines)


def test_header_merge_and_values(tmp_path):
    pdf = tmp_path / "t.pdf"
    make_pdf(pdf, [_table_page([("105Q1", "0.9980", "0.9981", "1.0010", "1.0011"), ("105Q2", "0.9990", "0.9991", "1.0020", "1.0021")], intro="Report intro text")])
    text = extract_pdf_text(pdf)
    assert "— 第 1 頁 —" in text
    assert "Report intro text" in text
    assert "Dental Float" in text and "Dental Avg" in text and "Chinese Float" in text and "Chinese Avg" in text
    row = next(line for line in text.splitlines() if "105Q2" in line)
    assert [c.strip() for c in row.strip("|").split("|")] == ["105Q2", "0.9990", "0.9991", "1.0020", "1.0021"]
    assert text.index("Report intro text") < text.index("Dental Float")


def test_continuation_reuses_header(tmp_path):
    pdf = tmp_path / "c.pdf"
    make_pdf(pdf, [
        _table_page([("105Q1", "0.9980", "0.9981", "1.0010", "1.0011")]),
        _table_page([("105Q2", "0.9990", "0.9991", "1.0020", "1.0021")], with_header=False),
    ])
    text = extract_pdf_text(pdf)
    assert "— 第 2 頁 —" in text
    page2 = text.split("— 第 2 頁 —")[1]
    assert "Dental Float" in page2 and "105Q2" in page2


def test_no_text_returns_empty(tmp_path):
    pdf = tmp_path / "e.pdf"
    make_pdf(pdf, [b"0 0 10 10 re S"])
    assert extract_pdf_text(pdf) == ""


def test_old_cache_is_reextracted(tmp_path):
    pdf = tmp_path / "doc.pdf"
    make_pdf(pdf, [_page([(50, 700, "Hello world")], [])])
    legacy = tmp_path / "doc.pdf.chat-text"
    legacy.write_text("stale pypdf text", encoding="utf-8")
    old_version = tmp_path / f"doc.pdf.v{TEXT_EXTRACTION_VERSION - 1}.chat-text"
    old_version.write_text("stale", encoding="utf-8")
    storage = ChatAttachmentStorage(tmp_path)
    cached = asyncio.run(storage.ensure_kb_text(pdf))
    assert "Hello world" in cached.read_text(encoding="utf-8")
    assert not legacy.exists() and not old_version.exists()
    assert cached.name == f"doc.pdf.v{TEXT_EXTRACTION_VERSION}.chat-text"


def test_ingest_hook_swallows_errors(tmp_path, monkeypatch):
    from app.tasks import documents as tasks

    calls = []

    async def boom(self, path, extension=None):
        calls.append(path)
        raise RuntimeError("extract failed")

    monkeypatch.setattr(ChatAttachmentStorage, "ensure_kb_text", boom)
    asyncio.run(tasks._prebuild_chat_text(tmp_path / "x.pdf"))
    assert calls == [tmp_path / "x.pdf"]
