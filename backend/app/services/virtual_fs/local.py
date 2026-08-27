"""Worker-local resolution of shared-volume source documents."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from uuid import UUID

from app.models.slides import DocumentResolver, SUPPORTED_SLIDE_SOURCE_EXTENSIONS

# Backwards-compatible import surface for callers that used the resolver's
# old module-level constant. The value itself is owned by models.slides.
SUPPORTED_SOURCE_EXTENSIONS = SUPPORTED_SLIDE_SOURCE_EXTENSIONS


class DocumentResolutionError(RuntimeError):
    """Stable, deliberately non-diagnostic document-resolution failure."""

    public_message = "Source documents are unavailable."

    def __init__(self) -> None:
        super().__init__(self.public_message)


class SharedVolumeDocumentResolver(DocumentResolver):
    """Resolve ``<documents_root>/<uuid>/<single-source-file>`` safely."""

    def __init__(self, documents_root: Path) -> None:
        self._documents_root = documents_root

    async def resolve_many(self, document_ids: list[UUID]) -> list[Path]:
        # Resolution performs synchronous filesystem metadata calls.  Run each
        # lookup in a worker thread so a Taskiq event loop can serve its other
        # in-flight jobs while shared-volume I/O is underway.
        return list(await asyncio.gather(*(asyncio.to_thread(self._resolve_one, document_id) for document_id in document_ids)))

    def _resolve_one(self, document_id: UUID) -> Path:
        try:
            root = self._documents_root.resolve(strict=True)
            document_dir = self._documents_root / str(document_id)
            if document_dir.is_symlink() or not document_dir.is_dir():
                raise DocumentResolutionError
            resolved_dir = document_dir.resolve(strict=True)
            if not _is_within(resolved_dir, root):
                raise DocumentResolutionError

            entries = list(document_dir.iterdir())
            if len(entries) != 1:
                raise DocumentResolutionError
            source = entries[0]
            if source.is_symlink() or not source.is_file():
                raise DocumentResolutionError
            if not stat.S_ISREG(source.stat().st_mode):
                raise DocumentResolutionError
            if source.suffix.lower() not in SUPPORTED_SOURCE_EXTENSIONS:
                raise DocumentResolutionError
            resolved_source = source.resolve(strict=True)
            if not _is_within(resolved_source, root):
                raise DocumentResolutionError
            return resolved_source
        except DocumentResolutionError:
            raise
        except (OSError, ValueError):
            raise DocumentResolutionError from None


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
