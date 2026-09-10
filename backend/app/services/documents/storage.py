"""Secure shared-volume storage for uploaded source documents."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile

from app.models.documents import (
    DEFAULT_MAX_UPLOAD_BYTES,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    SUPPORTED_DOCUMENT_MIME_TYPES,
)


class DocumentStorageError(RuntimeError):
    """Expected, safe-to-display storage failure."""


class UnsupportedDocumentError(DocumentStorageError):
    pass


class DocumentTooLargeError(DocumentStorageError):
    pass


class DocumentStorage:
    """Storage protocol implemented by local shared-volume storage."""

    async def save_upload(
        self,
        document_id: UUID,
        upload: UploadFile,
        *,
        max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> tuple[str, int, str]:
        raise NotImplementedError

    async def delete(self, document_id: UUID) -> None:
        raise NotImplementedError

    def resolve(self, document_id: UUID) -> Path:
        raise NotImplementedError


_UNSAFE_FILENAME = re.compile(r"[^\w._ -]+", re.UNICODE) 


def safe_filename(filename: str | None) -> str:
    source = Path(filename or "source").name
    source = _UNSAFE_FILENAME.sub("_", source).strip(" .")
    if not source:
        source = "source"
    extension = Path(source).suffix.lower()
    if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise UnsupportedDocumentError("This file type is not supported.")
    # Enforce the DB column length (512).  Truncate the stem, never the extension.
    max_stem = 512 - len(extension)
    stem = Path(source).stem
    if len(stem) > max_stem:
        stem = stem[:max_stem]
    return stem + extension


def validate_upload_metadata(filename: str | None, content_type: str | None) -> str:
    safe_name = safe_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if content_type and content_type.lower() not in SUPPORTED_DOCUMENT_MIME_TYPES:
        # Some clients send application/octet-stream for files that are safe
        # based on their extension, but reject a known incompatible MIME type.
        if content_type.lower() != "application/octet-stream":
            raise UnsupportedDocumentError("This file type is not supported.")
    return extension


class LocalDocumentStorage(DocumentStorage):
    """Store exactly one regular source file at ``<root>/<uuid>/<name>``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _root(self) -> Path:
        try:
            return self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DocumentStorageError("Document storage is unavailable.") from exc

    def _directory(self, document_id: UUID) -> Path:
        root = self._root()
        directory = root / str(document_id)
        if directory.is_symlink():
            raise DocumentStorageError("Document storage is unavailable.")
        directory.mkdir(mode=0o750, parents=False, exist_ok=True)
        resolved = directory.resolve(strict=True)
        if not _is_within(resolved, root):
            raise DocumentStorageError("Document storage is unavailable.")
        return directory

    async def save_upload(self, document_id: UUID, upload: UploadFile, *, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES):
        extension = validate_upload_metadata(upload.filename, upload.content_type)
        directory = self._directory(document_id)
        filename = safe_filename(upload.filename)
        temporary_path: Path | None = None
        digest = hashlib.sha256()
        total = 0
        try:
            with tempfile.NamedTemporaryFile(prefix=".upload-", dir=directory, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise DocumentTooLargeError("The uploaded file is too large.")
                    digest.update(chunk)
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            if total == 0:
                raise UnsupportedDocumentError("The uploaded file is empty.")
            destination = directory / filename
            # There must never be two source entries in a document directory.
            for entry in directory.iterdir():
                if entry != temporary_path and entry.is_file():
                    entry.unlink()
            os.replace(temporary_path, destination)
            return f"{document_id}/{filename}", total, digest.hexdigest()
        except DocumentStorageError:
            raise
        except (OSError, ValueError) as exc:
            raise DocumentStorageError("Document could not be stored.") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    async def delete(self, document_id: UUID) -> None:
        root = self._root()
        directory = root / str(document_id)
        if directory.is_symlink() or not directory.exists():
            return
        resolved = directory.resolve(strict=True)
        if not _is_within(resolved, root) or not directory.is_dir():
            raise DocumentStorageError("Document storage is unavailable.")
        try:
            entries = list(directory.iterdir())
            for entry in entries:
                if entry.is_symlink() or not entry.is_file() or not _is_within(entry.resolve(strict=True), root):
                    raise DocumentStorageError("Document storage is unavailable.")
                entry.unlink()
            directory.rmdir()
        except (OSError, ValueError) as exc:
            raise DocumentStorageError("Document could not be deleted.") from exc

    def resolve(self, document_id: UUID) -> Path:
        root = self._root()
        directory = root / str(document_id)
        if directory.is_symlink() or not directory.is_dir():
            raise DocumentStorageError("Source document is unavailable.")
        resolved_directory = directory.resolve(strict=True)
        if not _is_within(resolved_directory, root):
            raise DocumentStorageError("Source document is unavailable.")
        entries = list(directory.iterdir())
        if len(entries) != 1:
            raise DocumentStorageError("Source document is unavailable.")
        source = entries[0]
        if source.is_symlink() or not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
            raise DocumentStorageError("Source document is unavailable.")
        if source.suffix.lower() not in SUPPORTED_DOCUMENT_EXTENSIONS:
            raise DocumentStorageError("Source document is unavailable.")
        resolved_source = source.resolve(strict=True)
        if not _is_within(resolved_source, root):
            raise DocumentStorageError("Source document is unavailable.")
        return resolved_source


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
