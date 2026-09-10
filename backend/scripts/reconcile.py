"""Reconcile provider vector-store attachments with the local catalog.

The command is intentionally dry-run by default. ``--apply`` is required for
any provider deletion. It only deletes an attachment when no document or
ingestion job references that attachment, and only deletes the underlying
OpenAI file when no local record references that file.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from openai import OpenAI
from sqlmodel import Session, select

from app.config import settings
from app.db import engine
from app.models.documents import Document, IngestionJob
from app.models.retrieval import RetrievalIndex, RetrievalIndexState


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _items(page: Any) -> Iterable[Any]:
    data = _value(page, "data")
    if data is not None:
        return data
    if isinstance(page, (list, tuple)):
        return page
    return ()


def _local_references(session: Session) -> tuple[set[str], set[str]]:
    """Return locally referenced attachment IDs and file IDs.

    The fallback includes the legacy owner column as an attachment reference;
    this is conservative and prevents reconciliation from deleting a resource
    that may still be represented by an older application version.
    """

    attachment_ids: set[str] = set()
    file_ids: set[str] = set()
    for document in session.exec(select(Document)).all():
        for name in ("remote_vector_store_file_id", "candidate_remote_vector_store_file_id", "remote_vector_store_id"):
            value = getattr(document, name, None)
            if value:
                attachment_ids.add(str(value))
        if document.remote_file_id:
            file_ids.add(str(document.remote_file_id))
    for job in session.exec(select(IngestionJob)).all():
        for name in (
            "candidate_remote_vector_store_file_id",
            "cleanup_remote_vector_store_file_id",
        ):
            value = getattr(job, name, None)
            if value:
                attachment_ids.add(str(value))
        for name in ("candidate_remote_file_id", "cleanup_remote_file_id"):
            value = getattr(job, name, None)
            if value:
                file_ids.add(str(value))
    return attachment_ids, file_ids


def _store_id(session: Session, override: str | None) -> str | None:
    if override:
        return override
    record = session.get(RetrievalIndex, RetrievalIndex.PRIMARY_KEY)
    if record and record.state == RetrievalIndexState.READY.value:
        return record.vector_store_id
    return None


def reconcile(*, apply: bool = False, store_id: str | None = None, client: Any | None = None) -> int:
    """Print reconciliation findings and optionally delete safe orphans."""

    with Session(engine) as session:
        resolved_store_id = _store_id(session, store_id)
        attachment_refs, file_refs = _local_references(session)
    if not resolved_store_id:
        print("No READY retrieval index is recorded; nothing to reconcile.", file=sys.stderr)
        return 2
    if client is None:
        if not settings.openai_api_key:
            print("OPENAI_API_KEY is not configured.", file=sys.stderr)
            return 2
        client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    try:
        remote_page = client.vector_stores.files.list(vector_store_id=resolved_store_id, limit=100)
        remote_items = list(_items(remote_page))
    except Exception as exc:
        print(f"Unable to list vector-store attachments: {type(exc).__name__}", file=sys.stderr)
        return 1

    orphaned = [item for item in remote_items if str(_value(item, "id", "")) not in attachment_refs]
    print(f"store={resolved_store_id} remote_attachments={len(remote_items)} orphaned={len(orphaned)} apply={apply}")
    failed = False
    for item in orphaned:
        attachment_id = str(_value(item, "id", ""))
        file_id = _value(item, "file_id") or _value(item, "file")
        file_id = str(file_id) if file_id else None
        print(f"orphan attachment={attachment_id} file={file_id or '-'}")
        if not apply:
            continue
        try:
            client.vector_stores.files.delete(
                vector_store_id=resolved_store_id,
                file_id=attachment_id,
            )
            if file_id and file_id not in file_refs:
                client.files.delete(file_id=file_id)
        except Exception as exc:
            failed = True
            print(f"failed attachment={attachment_id} error={type(exc).__name__}", file=sys.stderr)
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete confirmed orphaned provider resources")
    parser.add_argument("--store-id", help="override the READY registry store ID for operator recovery")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return reconcile(apply=args.apply, store_id=args.store_id)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
