"""Build (or rebuild) the chat text cache for ready knowledge-base documents.

    uv run python -m app.scripts.backfill_document_text [--force]

Safe to re-run: up-to-date caches are skipped unless --force is given.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.models.documents import Document, DocumentStatus
from app.services.chat.attachments import ChatAttachmentStorage
from app.services.documents.storage import DocumentStorageError, LocalDocumentStorage


async def backfill(force: bool = False) -> tuple[int, int, int]:
    with Session(engine) as session:
        docs = list(session.exec(select(Document).where(Document.status == DocumentStatus.READY.value)))
    storage = ChatAttachmentStorage()
    documents = LocalDocumentStorage(settings.documents_root)
    built = skipped = failed = 0
    for index, doc in enumerate(docs, 1):
        label = f"[{index}/{len(docs)}] {doc.display_name}"
        try:
            source = documents.resolve(doc.id)
            cache = storage.kb_text_path(source)
            if cache.is_file() and not force:
                skipped += 1
                print(f"{label}: up to date", flush=True)
                continue
            if force:
                cache.unlink(missing_ok=True)
            started = time.monotonic()
            await storage.ensure_kb_text(source)
            built += 1
            print(f"{label}: built in {time.monotonic() - started:.1f}s", flush=True)
        except (DocumentStorageError, Exception) as exc:  # keep going
            failed += 1
            print(f"{label}: FAILED ({exc})", file=sys.stderr, flush=True)
    print(f"done: built={built} skipped={skipped} failed={failed}")
    return built, skipped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rebuild even if the cache is current")
    args = parser.parse_args()
    asyncio.run(backfill(args.force))


if __name__ == "__main__":
    main()
