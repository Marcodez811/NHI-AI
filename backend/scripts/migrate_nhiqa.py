"""Import the eligible NHI-QA corpus into the current document catalog.

The command is intentionally idempotent and dry-run by default. It imports
local files and metadata only; vector-store attachment is performed by the
normal ingestion worker after a news-free vector store is configured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.documents import Document, DocumentCategory, DocumentStatus, IngestionJob
from app.services.documents.storage import safe_filename


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_records(source_root: Path) -> tuple[list[dict], list[str]]:
    manifest_path = source_root / "data/manifests/file_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: list[dict] = []
    skipped_news: list[str] = []
    for item in manifest.get("files", []):
        path = source_root / "data/pdfs" / item["filename"]
        if "news" in item.get("roles", []):
            skipped_news.append(item["filename"])
            continue
        if path.exists():
            records.append({"path": path, "category": item["qa_set"], "priority": item.get("source_priority"), "roles": item.get("roles", []), "pages": item.get("pages"), "tables": item.get("tables")})

    markdown_dir = source_root / "data/markdown_sources"
    for path in sorted(markdown_dir.glob("*.md")) if markdown_dir.exists() else []:
        records.append({"path": path, "category": DocumentCategory.BEI_CAN.value, "priority": None, "roles": ["briefing_reference"], "pages": None, "tables": None})
    return records, skipped_news


def import_records(records: list[dict], database_url: str, storage_root: Path) -> tuple[int, int]:
    engine = create_engine(database_url, connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {"pool_pre_ping": True})
    SQLModel.metadata.create_all(engine)
    created = skipped = 0
    storage_root.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        for item in records:
            path = item["path"]
            digest = checksum(path)
            existing = session.exec(select(Document).where(Document.checksum == digest)).first()
            if existing:
                skipped += 1
                continue
            document_id = uuid5(NAMESPACE_URL, f"nhi-qa:{digest}")
            filename = safe_filename(path.name)
            directory = storage_root / str(document_id)
            directory.mkdir(mode=0o750, parents=True, exist_ok=True)
            shutil.copyfile(path, directory / filename)
            document = Document(
                id=document_id,
                original_filename=path.name,
                display_name=path.name,
                mime_type="application/pdf" if path.suffix.lower() == ".pdf" else "text/markdown",
                extension=path.suffix.lower(),
                size_bytes=path.stat().st_size,
                checksum=digest,
                category=item["category"],
                storage_key=f"{document_id}/{filename}",
                status=DocumentStatus.QUEUED.value,
                stage="queued",
                source_priority=item["priority"],
                roles=item["roles"],
                page_count=item["pages"],
                table_count=item["tables"],
            )
            session.add(document)
            session.add(IngestionJob(document_id=document_id))
            created += 1
        session.commit()
    return created, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path, help="NHI-QA repository root")
    parser.add_argument("--database-url", default="sqlite:///./nhi_ai.db")
    parser.add_argument("--storage-root", type=Path, default=Path("/tmp/slides/documents"))
    parser.add_argument("--apply", action="store_true", help="copy files and create catalog rows")
    args = parser.parse_args()
    records, skipped_news = source_records(args.source_root)
    summary = {"eligible_sources": len(records), "skipped_news": skipped_news, "apply": args.apply}
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    created, skipped_existing = import_records(records, args.database_url, args.storage_root)
    print(json.dumps({**summary, "created": created, "skipped_existing": skipped_existing, "status": "queued_for_indexing"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
