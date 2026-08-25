from uuid import uuid4

import pytest

from app.services.virtual_fs import DocumentResolutionError, SharedVolumeDocumentResolver


@pytest.mark.asyncio
async def test_resolves_one_regular_supported_file(tmp_path):
    document_id = uuid4()
    document_dir = tmp_path / str(document_id)
    document_dir.mkdir()
    source = document_dir / "source.pdf"
    source.write_bytes(b"pdf")

    resolved = await SharedVolumeDocumentResolver(tmp_path).resolve_many([document_id])

    assert resolved == [source.resolve()]


@pytest.mark.asyncio
@pytest.mark.parametrize("entries", [[], ["one.txt", "two.md"], ["source.exe"]])
async def test_rejects_missing_multiple_or_unsupported_files(tmp_path, entries):
    document_id = uuid4()
    document_dir = tmp_path / str(document_id)
    document_dir.mkdir()
    for name in entries:
        (document_dir / name).write_text("source", encoding="utf-8")

    with pytest.raises(DocumentResolutionError, match="Source documents are unavailable"):
        await SharedVolumeDocumentResolver(tmp_path).resolve_many([document_id])


@pytest.mark.asyncio
async def test_rejects_symlinked_source(tmp_path):
    document_id = uuid4()
    document_dir = tmp_path / str(document_id)
    document_dir.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"pdf")
    (document_dir / "source.pdf").symlink_to(outside)

    with pytest.raises(DocumentResolutionError):
        await SharedVolumeDocumentResolver(tmp_path).resolve_many([document_id])
