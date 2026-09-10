"""Delete every File attached to one OpenAI vector store, then optionally the store.

The command is a dry run unless ``--execute`` is supplied. Deleting the
underlying File objects also removes their attachments from any vector store.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from typing import Any

from openai import OpenAI

from app.config import settings


def _items(page: Any) -> Iterable[Any]:
    while True:
        yield from page.data
        if not page.has_next_page():
            return
        page = page.get_next_page()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Permanently purge files from one OpenAI vector store.",
    )
    parser.add_argument("--vector-store-id", required=True)
    parser.add_argument(
        "--delete-store",
        action="store_true",
        help="Delete the empty vector store after its files are purged.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform deletion. Without this flag, only show the targets.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    store = client.vector_stores.retrieve(args.vector_store_id)
    page = client.vector_stores.files.list(
        vector_store_id=args.vector_store_id,
        limit=100,
        order="asc",
    )
    file_ids = [str(item.id) for item in _items(page)]

    print(f"Vector store: {store.id} ({store.name!r})")
    print(f"Attached files: {len(file_ids)}")
    for file_id in file_ids:
        print(f"- {file_id}")

    if not args.execute:
        print("Dry run only; pass --execute to delete these resources.")
        return 0

    failures: list[tuple[str, str]] = []
    for file_id in file_ids:
        try:
            client.files.delete(file_id)
            print(f"Deleted file {file_id}")
        except Exception as exc:  # report every target before exiting nonzero
            failures.append((file_id, type(exc).__name__))
            print(f"Failed to delete file {file_id}: {type(exc).__name__}", file=sys.stderr)

    if failures:
        print(
            f"Purge incomplete: {len(failures)} file deletion(s) failed; "
            "the vector store was retained for a safe retry.",
            file=sys.stderr,
        )
        return 1

    if args.delete_store:
        client.vector_stores.delete(args.vector_store_id)
        print(f"Deleted vector store {args.vector_store_id}")
    else:
        print(f"Retained empty vector store {args.vector_store_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
