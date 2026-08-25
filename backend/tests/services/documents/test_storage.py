from uuid import uuid4

import pytest

from app.services.documents.storage import (
    DocumentTooLargeError,
    LocalDocumentStorage,
    UnsupportedDocumentError,
)


class FakeUpload:
    def __init__(self, name: str, data: bytes, content_type: str = "application/pdf"):
        self.filename = name
        self.content_type = content_type
        self._data = data

    async def read(self, size: int) -> bytes:
        data, self._data = self._data[:size], self._data[size:]
        return data


@pytest.mark.asyncio
async def test_storage_uses_uuid_directory_and_returns_checksum(tmp_path):
    document_id = uuid4()
    source = FakeUpload("../../secret.pdf", b"hello")
    key, size, checksum = await LocalDocumentStorage(tmp_path).save_upload(document_id, source)
    assert key.startswith(f"{document_id}/")
    assert size == 5
    assert LocalDocumentStorage(tmp_path).resolve(document_id).read_bytes() == b"hello"
    assert checksum


@pytest.mark.asyncio
async def test_storage_rejects_type_and_size(tmp_path):
    storage = LocalDocumentStorage(tmp_path)
    invalid = FakeUpload("payload.exe", b"no", "application/octet-stream")
    with pytest.raises(UnsupportedDocumentError):
        await storage.save_upload(uuid4(), invalid)
    oversized = FakeUpload("payload.txt", b"0123456789")
    with pytest.raises(DocumentTooLargeError):
        await storage.save_upload(uuid4(), oversized, max_bytes=3)
