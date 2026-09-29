"""Chat attachment storage, validation, and synchronous text extraction.

Attachments belong to one chat session and are never indexed into the
knowledge base (docs/9_29_chat_core_and_attachments_plan.md, decision 2).
Storage reuses the atomic-write / no-user-controlled-path-parts pattern from
``app.services.documents.storage.LocalDocumentStorage`` but keys files by
chat session instead of by document id, under its own
``chat_attachments/`` tree so the two are never confused.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from app.config import settings
from app.models.chat import ChatAttachmentKind, ChatAttachmentStatus

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
# PDFs go to the model as the file itself, so they must fit every provider's
# inline-file limits, which are tighter than ours for other documents.
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_EDGE = 1568

DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})
IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


class AttachmentError(RuntimeError):
    """A safe-to-display Chinese message describing a rejected upload."""


def _load_source_extraction():
    """Load the deterministic extractor script as a module.

    It lives under a hyphenated skill directory (staged for Codex to run as a
    CLI tool, see ``app/services/slides/adapter.py``), so it is not an
    importable package; ``importlib`` loads it directly from its file path.
    """

    script_path = (
        Path(__file__).resolve().parents[3]
        / ".agents"
        / "skills"
        / "source-document-extraction"
        / "scripts"
        / "source_extraction.py"
    )
    spec = importlib.util.spec_from_file_location("chat_source_extraction", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load source extraction script at {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_source_extraction = _load_source_extraction()


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass
class ProcessedAttachment:
    """Everything needed to build a ``ChatAttachment`` row after processing."""

    display_name: str
    mime_type: str
    kind: str
    size_bytes: int
    storage_key: str
    status: str
    error: str | None
    text_chars: int | None


class ChatAttachmentStorage:
    """Owns the on-disk layout for chat attachments.

    Files live at ``<documents_root>/chat_attachments/<session_id>/<uuid><ext>``,
    with extracted document text saved alongside as ``<uuid><ext>.txt``.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(settings.documents_root) / "chat_attachments"
        self.root.mkdir(parents=True, exist_ok=True)

    def _root(self) -> Path:
        try:
            return self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AttachmentError("附件儲存空間目前無法使用。") from exc

    def _session_dir(self, session_id: UUID) -> Path:
        root = self._root()
        directory = root / str(session_id)
        if directory.is_symlink():
            raise AttachmentError("附件儲存空間目前無法使用。")
        directory.mkdir(mode=0o750, exist_ok=True)
        resolved = directory.resolve(strict=True)
        if not _is_within(resolved, root):
            raise AttachmentError("附件儲存空間目前無法使用。")
        return directory

    def resolve(self, session_id: UUID, storage_key: str) -> Path:
        root = self._root()
        path = (root / storage_key).resolve()
        if not _is_within(path, root) or not path.is_file():
            raise AttachmentError("附件已無法讀取。")
        return path

    @staticmethod
    def text_path(content_path: Path) -> Path:
        return content_path.with_name(content_path.name + ".txt")

    async def save(self, session_id: UUID, attachment_id: UUID, extension: str, data: bytes) -> tuple[Path, str]:
        directory = self._session_dir(session_id)
        destination = directory / f"{attachment_id}{extension}"

        def _write() -> None:
            fd, temp_name = tempfile.mkstemp(prefix=".upload-", dir=directory)
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, destination)
            except OSError:
                if temp_path.exists():
                    temp_path.unlink()
                raise

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            raise AttachmentError("附件無法儲存，請稍後再試。") from exc
        return destination, f"{session_id}/{destination.name}"

    async def save_text(self, content_path: Path, text: str) -> None:
        await asyncio.to_thread(self.text_path(content_path).write_text, text, encoding="utf-8")

    def read_text(self, content_path: Path, *, start: int = 0, max_chars: int | None = None) -> tuple[str, int]:
        """Return ``(slice, total_chars)`` of a document attachment's saved text."""

        text_path = self.text_path(content_path)
        if not text_path.is_file():
            return "", 0
        full_text = text_path.read_text(encoding="utf-8")
        total = len(full_text)
        end = total if max_chars is None else min(total, start + max_chars)
        return full_text[start:end], total

    async def delete_attachment(self, session_id: UUID, storage_key: str) -> None:
        """Remove one session's saved attachment and any extracted-text sidecar."""

        key = Path(storage_key)
        if len(key.parts) != 2 or key.parts[0] != str(session_id):
            raise AttachmentError("附件儲存位置無效。")
        filename = key.parts[1]
        if filename in (".", "..") or key.suffix not in DOCUMENT_EXTENSIONS | {".png", ".jpg", ".jpeg"}:
            raise AttachmentError("附件儲存位置無效。")
        try:
            attachment_id = UUID(key.stem)
        except ValueError as exc:
            raise AttachmentError("附件儲存位置無效。") from exc
        if str(attachment_id) != key.stem:
            raise AttachmentError("附件儲存位置無效。")

        directory = self._root() / str(session_id)
        if directory.is_symlink():
            raise AttachmentError("附件儲存空間目前無法使用。")
        if not directory.exists():
            return
        if not directory.is_dir():
            raise AttachmentError("附件儲存空間目前無法使用。")
        content_path = directory / filename

        def _delete() -> None:
            content_path.unlink(missing_ok=True)
            self.text_path(content_path).unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_delete)
        except OSError as exc:
            raise AttachmentError("附件無法刪除，請稍後再試。") from exc

    async def delete_session(self, session_id: UUID) -> None:
        root = self._root()
        directory = root / str(session_id)
        if not directory.exists() or not _is_within(directory.resolve(), root):
            return
        await asyncio.to_thread(shutil.rmtree, directory, ignore_errors=True)


def _safe_extension(filename: str | None) -> str:
    return Path(filename or "").suffix.lower()


def _extract_docx_text(path: Path, attachment_id: UUID) -> str:
    # ``extract_docx`` raises ``ValueError`` directly for a corrupt/invalid
    # DOCX (rather than returning it in the ``errors`` list, unlike
    # ``extract_pdf``), so both paths are converted to the same safe message.
    try:
        with tempfile.TemporaryDirectory(prefix="chat-attachment-") as scratch:
            blocks, _assets, _warnings, errors = _source_extraction.extract_docx(
                path, Path(scratch), f"chat:{attachment_id}"
            )
    except (ValueError, OSError) as exc:
        raise AttachmentError("無法讀取此 DOCX 檔案內容。") from exc
    if errors:
        raise AttachmentError("無法讀取此 DOCX 檔案內容。")
    return "\n\n".join(block["text"] for block in blocks if block.get("text"))


def _check_pdf(path: Path) -> None:
    """Reject a PDF the model could not read: corrupt, encrypted or too long.

    PDFs are not text-extracted here. The model receives the file itself and
    reads it natively, scanned pages included.
    """

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise AttachmentError("此 PDF 已加密，無法讀取。")
        page_count = len(reader.pages)
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError("無法讀取此 PDF 檔案內容。") from exc
    if page_count > MAX_PDF_PAGES:
        raise AttachmentError(f"PDF 超過 {MAX_PDF_PAGES} 頁上限，請拆分後再上傳。")


def _reencode_image(data: bytes) -> tuple[bytes, str]:
    """Strip metadata, downscale, and return ``(bytes, mime_type)``.

    Re-encoding through Pillow neutralizes malformed files that only claim to
    be an image; ``Image.open`` + a full ``load()`` is what actually
    decodes the pixel data rather than just the header.
    """

    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            long_edge = max(image.size)
            if long_edge > MAX_IMAGE_EDGE:
                scale = MAX_IMAGE_EDGE / long_edge
                new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
                image = image.resize(new_size, Image.LANCZOS)
            buffer = BytesIO()
            if has_alpha:
                image.save(buffer, format="PNG", optimize=True)
                return buffer.getvalue(), "image/png"
            image.save(buffer, format="JPEG", quality=85, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AttachmentError("這個圖片檔案無法讀取，請確認檔案未毀損。") from exc


async def process_upload(
    storage: ChatAttachmentStorage,
    session_id: UUID,
    attachment_id: UUID,
    upload: UploadFile,
) -> ProcessedAttachment:
    """Validate, store, and (for DOCX/TXT/MD) extract text from one upload.

    PDFs are stored as-is and sent to the model as files (see ``_check_pdf``).

    Raises :class:`AttachmentError` for a rejected upload (wrong type, too
    large, unreadable magic bytes/content) -- the route turns that into a 400,
    the same way ``app.api.routes.documents`` handles
    ``UnsupportedDocumentError``. A file that is an allowed type but whose
    *content* cannot be read (an encrypted or corrupt PDF, a corrupt DOCX)
    still becomes a stored attachment row with ``status="failed"`` -- that is
    not an upload rejection.
    """

    display_name = (upload.filename or "附件")[:512]
    extension = _safe_extension(upload.filename)
    declared_type = (upload.content_type or "").lower()
    is_document = extension in DOCUMENT_EXTENSIONS
    is_image = extension in {".png", ".jpg", ".jpeg", ".webp"} or declared_type in IMAGE_MIME_TYPES
    if not is_document and not is_image:
        raise AttachmentError("不支援此檔案類型，僅支援 PDF、DOCX、TXT、MD 及圖片。")
    if is_document and is_image:
        # A filename claiming both a document extension and an image content
        # type is inconsistent input; trust the extension since it decides
        # how the bytes are parsed below.
        is_image = False

    max_bytes = MAX_IMAGE_BYTES if is_image else MAX_PDF_BYTES if extension == ".pdf" else MAX_DOCUMENT_BYTES
    data = await upload.read()
    if not data:
        raise AttachmentError("上傳的檔案是空的。")
    if len(data) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise AttachmentError(f"檔案超過大小上限（{limit_mb} MB）。")

    if is_image:
        reencoded, mime_type = _reencode_image(data)
        store_extension = ".png" if mime_type == "image/png" else ".jpg"
        path, storage_key = await storage.save(session_id, attachment_id, store_extension, reencoded)
        return ProcessedAttachment(
            display_name=display_name,
            mime_type=mime_type,
            kind=ChatAttachmentKind.IMAGE.value,
            size_bytes=len(reencoded),
            storage_key=storage_key,
            status=ChatAttachmentStatus.READY.value,
            error=None,
            text_chars=None,
        )

    _validate_document_magic_bytes(extension, data)
    path, storage_key = await storage.save(session_id, attachment_id, extension, data)
    mime_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }[extension]

    if extension == ".pdf":
        try:
            await asyncio.to_thread(_check_pdf, path)
        except AttachmentError as exc:
            status_value, error = ChatAttachmentStatus.FAILED.value, str(exc)
        else:
            status_value, error = ChatAttachmentStatus.READY.value, None
        return ProcessedAttachment(
            display_name=display_name,
            mime_type=mime_type,
            kind=ChatAttachmentKind.DOCUMENT.value,
            size_bytes=len(data),
            storage_key=storage_key,
            status=status_value,
            error=error,
            text_chars=None,
        )

    try:
        text, status_value, error = await asyncio.to_thread(_extract_document_text, path, extension, attachment_id)
    except AttachmentError as exc:
        return ProcessedAttachment(
            display_name=display_name,
            mime_type=mime_type,
            kind=ChatAttachmentKind.DOCUMENT.value,
            size_bytes=len(data),
            storage_key=storage_key,
            status=ChatAttachmentStatus.FAILED.value,
            error=str(exc),
            text_chars=None,
        )
    await storage.save_text(path, text)
    return ProcessedAttachment(
        display_name=display_name,
        mime_type=mime_type,
        kind=ChatAttachmentKind.DOCUMENT.value,
        size_bytes=len(data),
        storage_key=storage_key,
        status=status_value,
        error=error,
        text_chars=len(text),
    )


def _validate_document_magic_bytes(extension: str, data: bytes) -> None:
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise AttachmentError("這個檔案不是有效的 PDF。")
    if extension == ".docx" and not data.startswith(b"PK\x03\x04"):
        raise AttachmentError("這個檔案不是有效的 DOCX。")
    if extension in {".txt", ".md"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentError("這個檔案不是有效的文字檔。") from exc


def _extract_document_text(path: Path, extension: str, attachment_id: UUID) -> tuple[str, str, str | None]:
    """Return ``(text, status, error)`` for a validated, stored document."""

    if extension == ".docx":
        text = _extract_docx_text(path, attachment_id)
        return text, ChatAttachmentStatus.READY.value, None
    # .txt / .md: already validated as UTF-8 text.
    return path.read_text(encoding="utf-8"), ChatAttachmentStatus.READY.value, None


__all__ = [
    "AttachmentError",
    "ChatAttachmentStorage",
    "ProcessedAttachment",
    "process_upload",
    "DOCUMENT_EXTENSIONS",
    "IMAGE_MIME_TYPES",
    "MAX_DOCUMENT_BYTES",
    "MAX_IMAGE_BYTES",
]
