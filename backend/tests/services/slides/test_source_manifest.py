"""Source-name manifest and staging regressions for the slides workflow."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.documents import Document, DocumentCategory, DocumentStatus
from app.models.slides import SlidesTaskPayload
from app.services.slides import adapter as adapter_module
from app.services.slides.adapter import SlidesWorkflowAdapter, _document_display_names
from app.services.slides.artifacts import create_job_workspace, stage_uploads
from app.services.slides.contracts import JobError
from app.services.slides.runtime import build_prompt
from app.services.slides.source_manifest import (
    SourceManifestError,
    load_source_manifest,
    write_source_manifest,
)


def _document(document_id, display_name: str) -> Document:
    return Document(
        id=document_id,
        original_filename="stored.pdf",
        display_name=display_name,
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        checksum=uuid4().hex,
        category=DocumentCategory.BEI_CAN.value,
        storage_key="ignored",
        status=DocumentStatus.READY.value,
    )


def _payload(document_ids) -> SlidesTaskPayload:
    return SlidesTaskPayload(
        job_id=uuid4(),
        title="來源名稱測試",
        document_ids=list(document_ids),
        slides_count=5,
        guidance="",
        tone="formal",
    )


@pytest.mark.asyncio
async def test_document_display_names_come_from_catalog_in_request_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'documents.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    first_id, second_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(_document(first_id, "2025報告.pdf"))
        session.add(_document(second_id, "2025報告.pdf"))
        session.commit()
    monkeypatch.setattr("app.db.engine", engine)

    assert await _document_display_names([second_id, first_id]) == [
        "2025報告.pdf",
        "2025報告.pdf",
    ]
    with pytest.raises(JobError, match="metadata"):
        await _document_display_names([uuid4()])


def test_source_manifest_preserves_staged_identity_and_duplicate_display_names(
    tmp_path: Path,
) -> None:
    path = tmp_path / "work" / "sources.json"

    written = write_source_manifest(
        path,
        ["report.pdf", "report__2.pdf"],
        ["年度報告.pdf", "年度報告.pdf"],
    )

    assert load_source_manifest(path) == written
    assert [source.staged_filename for source in written] == [
        "report.pdf",
        "report__2.pdf",
    ]
    assert [source.display_name for source in written] == [
        "年度報告.pdf",
        "年度報告.pdf",
    ]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"format_version", "sources"}
    assert "document_id" not in path.read_text(encoding="utf-8")

    path.write_text('{"format_version":"SlideSources v1","sources":[]}', encoding="utf-8")
    with pytest.raises(SourceManifestError, match="non-empty"):
        load_source_manifest(path)


def test_stage_uploads_never_overwrites_a_generated_collision_name(tmp_path: Path) -> None:
    workspace = create_job_workspace("job", tmp_path / "jobs")
    source_directories = [tmp_path / f"source-{number}" for number in range(1, 4)]
    for directory in source_directories:
        directory.mkdir()
    sources = [
        source_directories[0] / "a.pdf",
        source_directories[1] / "a__3.pdf",
        source_directories[2] / "a.pdf",
    ]
    for number, source in enumerate(sources, start=1):
        source.write_bytes(f"source-{number}".encode())

    staged = stage_uploads(workspace, [source.resolve() for source in sources])

    assert staged == ["a.pdf", "a__3.pdf", "a__4.pdf"]
    assert [
        (workspace / "input" / filename).read_bytes() for filename in staged
    ] == [b"source-1", b"source-2", b"source-3"]


@pytest.mark.asyncio
async def test_prepare_input_reuses_staged_names_on_resume_and_refreshes_display_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = uuid4()
    documents_root = tmp_path / "documents"
    document_directory = documents_root / str(document_id)
    document_directory.mkdir(parents=True)
    (document_directory / "stored.pdf").write_bytes(b"source")
    workspace = create_job_workspace("job", tmp_path / "jobs")
    (workspace / "work" / "slide_context.json").write_text(
        json.dumps({"fontconfig": None}),
        encoding="utf-8",
    )
    names = ["知識庫報告.pdf"]

    async def display_names(_document_ids):
        return list(names)

    monkeypatch.setattr(adapter_module, "_document_display_names", display_names)
    monkeypatch.setattr(adapter_module.settings, "documents_root", documents_root)
    monkeypatch.setattr(adapter_module, "preflight", lambda *_args, **_kwargs: "Noto Sans TC")
    adapter = SlidesWorkflowAdapter()
    request = _payload([document_id])

    await adapter.prepare_input(request, workspace)
    (workspace / "work" / "evidence.json").write_text("{}", encoding="utf-8")
    (document_directory / "stored.pdf").unlink()
    names[0] = "重新命名報告.pdf"
    await adapter.prepare_input(request, workspace)

    manifest = load_source_manifest(workspace / "work" / "sources.json")
    assert [(source.staged_filename, source.display_name) for source in manifest] == [
        ("stored.pdf", "重新命名報告.pdf")
    ]
    assert sorted(path.name for path in (workspace / "input").iterdir()) == ["stored.pdf"]


@pytest.mark.asyncio
async def test_source_manifest_grants_and_prompts_are_exact(tmp_path: Path) -> None:
    adapter = SlidesWorkflowAdapter()
    sources_path = tmp_path / "work" / "sources.json"

    assert sources_path in adapter.stage_read_only_paths("planner", tmp_path)
    assert sources_path in adapter.stage_read_only_paths("author", tmp_path)
    assert sources_path not in adapter.stage_read_only_paths("reviewer", tmp_path)
    assert sources_path not in adapter.stage_writable_paths("author", tmp_path)
    assert tmp_path / "work" not in adapter.stage_read_only_paths("planner", tmp_path)

    request = _payload([uuid4()])
    planning_prompt = await adapter.build_planning_prompt(request, tmp_path)
    author_prompt = build_prompt(request, [], font_family="Noto Sans TC")
    assert "work/sources.json" in planning_prompt
    assert "knowledge-base" in planning_prompt
    assert "work/sources.json" in author_prompt
    assert "collision suffix" in author_prompt
    assert "[N] 資料來源：<allowlisted display_name>，<locator>" in author_prompt
    assert "numbers that agree with the content footers" in author_prompt
    assert "Every chart must carry a descriptive title" in author_prompt
    assert "units on the relevant\naxis label" in author_prompt
