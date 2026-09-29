from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.models.chat import ChatAttachmentKind, ChatAttachmentStatus
from app.services.chat import attachments as attachments_module
from app.services.chat.attachments import (
    MAX_DOCUMENT_BYTES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_EDGE,
    MAX_PDF_PAGES,
    AttachmentError,
    ChatAttachmentStorage,
    process_upload,
)


class FakeUpload:
    def __init__(self, name: str, data: bytes, content_type: str = "application/octet-stream"):
        self.filename = name
        self.content_type = content_type
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data, self._data = self._data, b""
            return data
        data, self._data = self._data[:size], self._data[size:]
        return data


def _png_bytes(size: tuple[int, int] = (4, 4), mode: str = "RGB") -> bytes:
    image = Image.new(mode, size, color=(10, 20, 30, 255) if mode == "RGBA" else (10, 20, 30))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ── Upload validation: type, size, magic bytes ──────────────────────────────


@pytest.mark.asyncio
async def test_unsupported_extension_is_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(AttachmentError):
        await process_upload(storage, uuid4(), uuid4(), FakeUpload("virus.exe", b"stuff"))


@pytest.mark.asyncio
async def test_empty_file_is_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(AttachmentError):
        await process_upload(storage, uuid4(), uuid4(), FakeUpload("empty.txt", b""))


@pytest.mark.asyncio
async def test_oversized_document_is_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    data = b"%PDF-" + b"0" * MAX_DOCUMENT_BYTES
    with pytest.raises(AttachmentError):
        await process_upload(storage, uuid4(), uuid4(), FakeUpload("big.pdf", data, "application/pdf"))


@pytest.mark.asyncio
async def test_oversized_image_is_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    data = b"\x89PNG\r\n" + b"0" * MAX_IMAGE_BYTES
    with pytest.raises(AttachmentError):
        await process_upload(storage, uuid4(), uuid4(), FakeUpload("big.png", data, "image/png"))


@pytest.mark.asyncio
async def test_pdf_with_wrong_magic_bytes_is_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(AttachmentError):
        await process_upload(storage, uuid4(), uuid4(), FakeUpload("fake.pdf", b"not a pdf", "application/pdf"))


@pytest.mark.asyncio
async def test_docx_with_wrong_magic_bytes_is_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(AttachmentError):
        await process_upload(
            storage,
            uuid4(),
            uuid4(),
            FakeUpload("fake.docx", b"not a docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        )


@pytest.mark.asyncio
async def test_txt_with_invalid_utf8_is_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(AttachmentError):
        await process_upload(storage, uuid4(), uuid4(), FakeUpload("bad.txt", b"\xff\xfe\x00garbage"))


@pytest.mark.asyncio
async def test_garbage_image_bytes_are_rejected(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(AttachmentError):
        await process_upload(storage, uuid4(), uuid4(), FakeUpload("bad.png", b"not an image", "image/png"))


# ── Text documents ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_txt_is_stored_and_extracted_verbatim(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    session_id, attachment_id = uuid4(), uuid4()
    content = "健保給付規定\n第二段落".encode("utf-8")
    processed = await process_upload(storage, session_id, attachment_id, FakeUpload("note.txt", content, "text/plain"))

    assert processed.status == ChatAttachmentStatus.READY.value
    assert processed.kind == ChatAttachmentKind.DOCUMENT.value
    assert processed.text_chars == len(content.decode("utf-8"))
    path = storage.resolve(session_id, processed.storage_key)
    text, total = storage.read_text(path)
    assert text == content.decode("utf-8")
    assert total == len(text)


# ── DOCX / PDF extraction outcomes (source_extraction is stubbed: it is a
# separately maintained script, not part of this feature) ──────────────────


@pytest.mark.asyncio
async def test_docx_extraction_joins_block_text(tmp_path, monkeypatch):
    def fake_extract_docx(path, output, document_id):
        return (
            [{"kind": "paragraph", "text": "第一段"}, {"kind": "paragraph", "text": "第二段"}],
            [],
            [],
            [],
        )

    monkeypatch.setattr(attachments_module._source_extraction, "extract_docx", fake_extract_docx)
    storage = ChatAttachmentStorage(tmp_path)
    session_id, attachment_id = uuid4(), uuid4()
    processed = await process_upload(
        storage, session_id, attachment_id, FakeUpload("brief.docx", b"PK\x03\x04rest", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    )

    assert processed.status == ChatAttachmentStatus.READY.value
    path = storage.resolve(session_id, processed.storage_key)
    text, _total = storage.read_text(path)
    assert text == "第一段\n\n第二段"


@pytest.mark.asyncio
async def test_docx_extraction_failure_marks_the_attachment_failed(tmp_path, monkeypatch):
    def fake_extract_docx(path, output, document_id):
        return [], [], [], ["invalid DOCX: word/document.xml is missing"]

    monkeypatch.setattr(attachments_module._source_extraction, "extract_docx", fake_extract_docx)
    storage = ChatAttachmentStorage(tmp_path)
    processed = await process_upload(
        storage, uuid4(), uuid4(), FakeUpload("broken.docx", b"PK\x03\x04rest", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    )

    assert processed.status == ChatAttachmentStatus.FAILED.value
    assert processed.error


def _pdf_bytes(pages: int = 1, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_pdf_is_stored_as_is_for_the_model_to_read(tmp_path):
    # No text extraction: a scanned PDF is as readable to the model as any other.
    storage = ChatAttachmentStorage(tmp_path)
    processed = await process_upload(storage, uuid4(), uuid4(), FakeUpload("scan.pdf", _pdf_bytes(), "application/pdf"))

    assert processed.status == ChatAttachmentStatus.READY.value
    assert processed.mime_type == "application/pdf"
    assert processed.text_chars is None
    assert processed.error is None


@pytest.mark.asyncio
async def test_encrypted_pdf_is_marked_failed(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    data = _pdf_bytes(password="secret")
    processed = await process_upload(storage, uuid4(), uuid4(), FakeUpload("locked.pdf", data, "application/pdf"))

    assert processed.status == ChatAttachmentStatus.FAILED.value
    assert "加密" in processed.error


@pytest.mark.asyncio
async def test_corrupt_pdf_is_marked_failed(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    processed = await process_upload(storage, uuid4(), uuid4(), FakeUpload("broken.pdf", b"%PDF-1.4\nrest", "application/pdf"))

    assert processed.status == ChatAttachmentStatus.FAILED.value


@pytest.mark.asyncio
async def test_pdf_over_the_page_limit_is_marked_failed(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    data = _pdf_bytes(pages=MAX_PDF_PAGES + 1)
    processed = await process_upload(storage, uuid4(), uuid4(), FakeUpload("long.pdf", data, "application/pdf"))

    assert processed.status == ChatAttachmentStatus.FAILED.value
    assert str(MAX_PDF_PAGES) in processed.error


# ── Images: re-encode, strip metadata, downscale ────────────────────────────


@pytest.mark.asyncio
async def test_image_with_alpha_is_reencoded_as_png(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    data = _png_bytes(mode="RGBA")
    processed = await process_upload(storage, uuid4(), uuid4(), FakeUpload("logo.png", data, "image/png"))

    assert processed.kind == ChatAttachmentKind.IMAGE.value
    assert processed.mime_type == "image/png"
    assert processed.status == ChatAttachmentStatus.READY.value


@pytest.mark.asyncio
async def test_opaque_image_is_reencoded_as_jpeg(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    data = _png_bytes(mode="RGB")
    processed = await process_upload(storage, uuid4(), uuid4(), FakeUpload("photo.png", data, "image/png"))

    assert processed.mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_large_image_is_downscaled_to_the_max_edge(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    large = Image.new("RGB", (2000, 100), color=(1, 2, 3))
    buffer = BytesIO()
    large.save(buffer, format="PNG")
    session_id, attachment_id = uuid4(), uuid4()
    processed = await process_upload(
        storage, session_id, attachment_id, FakeUpload("wide.png", buffer.getvalue(), "image/png")
    )

    path = storage.resolve(session_id, processed.storage_key)
    with Image.open(path) as stored:
        assert max(stored.size) <= MAX_IMAGE_EDGE


# ── Storage path safety ──────────────────────────────────────────────────────


def test_resolve_rejects_a_storage_key_outside_the_root(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    with pytest.raises(AttachmentError):
        storage.resolve(uuid4(), "../../etc/passwd")


@pytest.mark.asyncio
async def test_delete_session_removes_its_files(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    session_id, attachment_id = uuid4(), uuid4()
    processed = await process_upload(storage, session_id, attachment_id, FakeUpload("note.txt", b"hello", "text/plain"))
    path = storage.resolve(session_id, processed.storage_key)
    assert path.is_file()

    await storage.delete_session(session_id)

    with pytest.raises(AttachmentError):
        storage.resolve(session_id, processed.storage_key)


@pytest.mark.asyncio
async def test_delete_attachment_removes_content_and_text_but_preserves_siblings(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    session_id = uuid4()
    removed = await process_upload(storage, session_id, uuid4(), FakeUpload("old.txt", b"old", "text/plain"))
    kept = await process_upload(storage, session_id, uuid4(), FakeUpload("keep.txt", b"keep", "text/plain"))
    removed_path = storage.resolve(session_id, removed.storage_key)
    removed_text_path = storage.text_path(removed_path)
    kept_path = storage.resolve(session_id, kept.storage_key)
    assert removed_text_path.is_file()

    await storage.delete_attachment(session_id, removed.storage_key)

    assert not removed_path.exists()
    assert not removed_text_path.exists()
    assert kept_path.read_bytes() == b"keep"
    assert storage.read_text(kept_path)[0] == "keep"


@pytest.mark.asyncio
async def test_delete_attachment_cannot_remove_another_sessions_file(tmp_path):
    storage = ChatAttachmentStorage(tmp_path)
    owner_id = uuid4()
    processed = await process_upload(storage, owner_id, uuid4(), FakeUpload("private.txt", b"secret", "text/plain"))
    owned_path = storage.resolve(owner_id, processed.storage_key)

    with pytest.raises(AttachmentError):
        await storage.delete_attachment(uuid4(), processed.storage_key)
    with pytest.raises(AttachmentError):
        await storage.delete_attachment(owner_id, f"{owner_id}/../{owned_path.name}")

    assert owned_path.read_bytes() == b"secret"
    assert storage.text_path(owned_path).is_file()
