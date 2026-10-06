"""Chat attachment storage, validation, and synchronous text extraction.

Uploaded files belong to the user; conversations only reference them
(docs/9_29_files_and_artifacts_spec.md). They are indexed into the knowledge
base only through an explicit promote. Storage reuses the atomic-write /
no-user-controlled-path-parts pattern from
``app.services.documents.storage.LocalDocumentStorage`` but keys files by
file id under its own ``user_files/`` tree.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from app.config import settings
from app.services import app_settings
from app.models.chat import ChatAttachmentKind, ChatAttachmentStatus
from app.services.documents.storage import CHAT_TEXT_SUFFIX

# Size/page limits come from config.yaml (uploads.chat.*) and are read per call.

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
    """Everything needed to build a ``UserFile`` row after processing."""

    display_name: str
    mime_type: str
    kind: str
    size_bytes: int
    storage_key: str
    status: str
    error: str | None
    text_chars: int | None


class ChatAttachmentStorage:
    """Owns the on-disk layout for user files.

    Everything is addressed by a ``storage_key`` relative to the documents
    root. New files live at ``user_files/<file_id>`` (extracted document text
    beside it as ``<file_id>.txt``); files migrated from the old per-chat
    attachments keep ``chat_attachments/<session_id>/<name>``. Knowledge-base
    documents (``<document_id>/<name>``) resolve through the same root, with
    their extracted text cached as ``<name>.chat-text``.
    """

    USER_FILES_DIR = "user_files"
    LEGACY_DIR = "chat_attachments"

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(settings.documents_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _root(self) -> Path:
        try:
            return self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AttachmentError("附件儲存空間目前無法使用。") from exc

    def _files_dir(self) -> Path:
        root = self._root()
        directory = root / self.USER_FILES_DIR
        if directory.is_symlink():
            raise AttachmentError("附件儲存空間目前無法使用。")
        directory.mkdir(mode=0o750, exist_ok=True)
        if not _is_within(directory.resolve(strict=True), root):
            raise AttachmentError("附件儲存空間目前無法使用。")
        return directory

    def resolve(self, storage_key: str) -> Path:
        root = self._root()
        path = (root / storage_key).resolve()
        if not _is_within(path, root) or not path.is_file():
            raise AttachmentError("附件已無法讀取。")
        return path

    @staticmethod
    def text_path(content_path: Path) -> Path:
        return content_path.with_name(content_path.name + ".txt")

    @staticmethod
    def kb_text_path(content_path: Path) -> Path:
        return content_path.with_name(content_path.name + CHAT_TEXT_SUFFIX)

    async def save(self, file_id: UUID, data: bytes) -> tuple[Path, str]:
        directory = self._files_dir()
        destination = directory / str(file_id)

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
        return destination, f"{self.USER_FILES_DIR}/{file_id}"

    async def save_text(self, content_path: Path, text: str) -> None:
        await asyncio.to_thread(self.text_path(content_path).write_text, text, encoding="utf-8")

    def read_text(
        self,
        content_path: Path,
        *,
        start: int = 0,
        max_chars: int | None = None,
        text_path: Path | None = None,
    ) -> tuple[str, int]:
        """Return ``(slice, total_chars)`` of a document's saved text."""

        text_path = text_path or self.text_path(content_path)
        if not text_path.is_file():
            return "", 0
        full_text = text_path.read_text(encoding="utf-8")
        total = len(full_text)
        end = total if max_chars is None else min(total, start + max_chars)
        return full_text[start:end], total

    async def ensure_kb_text(self, content_path: Path) -> Path:
        """Extract a knowledge-base DOCX/TXT/MD once and cache it beside the document."""

        cached = self.kb_text_path(content_path)
        if cached.is_file():
            return cached
        extension = content_path.suffix.lower()
        text, _status, _error = await asyncio.to_thread(
            _extract_document_text, content_path, extension, uuid5(NAMESPACE_URL, str(content_path))
        )
        try:
            await asyncio.to_thread(cached.write_text, text, encoding="utf-8")
        except OSError as exc:
            raise AttachmentError("附件無法讀取。") from exc
        return cached

    async def delete_file(self, storage_key: str) -> None:
        """Remove one stored user file and its extracted-text sidecar."""

        key = Path(storage_key)
        legacy = len(key.parts) == 3 and key.parts[0] == self.LEGACY_DIR
        current = len(key.parts) == 2 and key.parts[0] == self.USER_FILES_DIR
        if not (legacy or current) or ".." in key.parts:
            raise AttachmentError("附件儲存位置無效。")
        root = self._root()
        content_path = (root / key).resolve()
        if not _is_within(content_path, root / key.parts[0]):
            raise AttachmentError("附件儲存位置無效。")

        def _delete() -> None:
            content_path.unlink(missing_ok=True)
            self.text_path(content_path).unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_delete)
        except OSError as exc:
            raise AttachmentError("附件無法刪除，請稍後再試。") from exc


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
    if page_count > app_settings.value("uploads.chat.max_pdf_pages"):
        raise AttachmentError(f"PDF 超過 {app_settings.value("uploads.chat.max_pdf_pages")} 頁上限，請拆分後再上傳。")


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
            if long_edge > app_settings.value("uploads.chat.max_image_edge"):
                scale = app_settings.value("uploads.chat.max_image_edge") / long_edge
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
    file_id: UUID,
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

    max_bytes = app_settings.value("uploads.chat.max_image_bytes") if is_image else app_settings.value("uploads.chat.max_pdf_bytes") if extension == ".pdf" else app_settings.value("uploads.chat.max_document_bytes")
    data = await upload.read()
    if not data:
        raise AttachmentError("上傳的檔案是空的。")
    if len(data) > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise AttachmentError(f"檔案超過大小上限（{limit_mb} MB）。")

    if is_image:
        reencoded, mime_type = _reencode_image(data)
        store_extension = ".png" if mime_type == "image/png" else ".jpg"
        path, storage_key = await storage.save(file_id, reencoded)
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
    path, storage_key = await storage.save(file_id, data)
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
        text, status_value, error = await asyncio.to_thread(_extract_document_text, path, extension, file_id)
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
]
