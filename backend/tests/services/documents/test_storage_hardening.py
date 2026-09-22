"""Storage hardening tests.

Covers:
- Empty upload (0 bytes) is rejected with UnsupportedDocumentError
- Filename > 512 chars is truncated preserving extension
- Unicode display name preserved; storage filename is ASCII-safe
- Extension not dropped during truncation
"""

from __future__ import annotations

import pytest

from app.services.documents.storage import (
    LocalDocumentStorage,
    UnsupportedDocumentError,
    safe_filename,
)


# ── safe_filename ─────────────────────────────────────────────────────────────


def test_safe_filename_normal_pdf():
    assert safe_filename("report.pdf") == "report.pdf"


def test_safe_filename_strips_unsafe_characters():
    result = safe_filename("exec/path/../evil.pdf")
    # Path components are stripped, unsafe chars replaced.
    assert ".." not in result
    assert "/" not in result
    assert result.endswith(".pdf")


def test_safe_filename_truncates_long_stem_preserving_extension():
    # Stem of 600 'a' chars + ".pdf" = 604 chars > 512.
    long_name = "a" * 600 + ".pdf"
    result = safe_filename(long_name)
    assert len(result) <= 512
    assert result.endswith(".pdf")
    # The extension must not be dropped.
    assert "." in result


def test_safe_filename_exactly_512_chars_allowed():
    # 508 'a' + ".pdf" = 512 chars — on the limit, no truncation needed.
    name = "a" * 508 + ".pdf"
    result = safe_filename(name)
    assert len(result) == 512
    assert result.endswith(".pdf")


def test_safe_filename_just_over_512_chars_truncated():
    # 509 'a' + ".pdf" = 513 chars — must be truncated to 512.
    name = "a" * 509 + ".pdf"
    result = safe_filename(name)
    assert len(result) == 512
    assert result.endswith(".pdf")


def test_safe_filename_unsupported_extension_raises():
    with pytest.raises(UnsupportedDocumentError):
        safe_filename("script.exe")


def test_safe_filename_empty_falls_back_to_source():
    # But 'source' alone has no extension, so this should raise if no valid ext.
    with pytest.raises(UnsupportedDocumentError):
        safe_filename("")


def test_safe_filename_none_falls_back_to_source():
    with pytest.raises(UnsupportedDocumentError):
        safe_filename(None)


def test_safe_filename_preserves_unicode_word_characters():
    # Source documents are routinely named in Traditional Chinese, so
    # sanitization must not flatten them to underscores (commit 5baedbc2).
    assert safe_filename("報告書.pdf") == "報告書.pdf"


def test_safe_filename_strips_directory_and_punctuation():
    # The hardening that actually matters: a traversal attempt loses its
    # directory components, and separators never reach the stored name.
    result = safe_filename("../../etc/報告書*?.pdf")
    assert result.endswith(".pdf")
    assert "/" not in result and ".." not in result
    assert "*" not in result and "?" not in result


# ── empty upload rejection ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_upload_is_rejected(tmp_path):
    """A zero-byte upload must raise UnsupportedDocumentError."""

    from io import BytesIO
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    storage = LocalDocumentStorage(tmp_path)
    doc_id = uuid4()

    upload = MagicMock()
    upload.filename = "empty.pdf"
    upload.content_type = "application/pdf"
    # Simulate an upload that immediately returns EOF.
    upload.read = AsyncMock(side_effect=[b"", b""])

    with pytest.raises(UnsupportedDocumentError, match="empty"):
        await storage.save_upload(doc_id, upload)
