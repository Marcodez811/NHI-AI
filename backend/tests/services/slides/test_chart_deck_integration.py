from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.agentic.runner import bwrap_available, build_bwrap_launch_args
from app.services.slides.adapter import SlidesWorkflowAdapter
from app.services.slides.artifacts import BACKEND_ROOT, create_job_workspace, stage_required_skills
from app.services.slides.evidence import freeze_evidence, load_frozen_evidence
from app.services.slides.source_manifest import write_source_manifest
from app.services.slides.validation import ValidationStatus, build_deck_snapshot, validate_candidate_deck


SKILL = BACKEND_ROOT / ".agents" / "skills" / "pptx-nhi-tw"
CHART_RENDERER = SKILL / "scripts" / "render_chart_image.js"
SLIDE_RENDERER = SKILL / "scripts" / "render_slides.py"
FIXTURE = Path(__file__).parent / "fixtures" / "generate_mixed_chart_deck.js"
EXTRACTION_SCRIPT = BACKEND_ROOT / ".agents" / "skills" / "source-document-extraction" / "scripts" / "source_extraction.py"


def _chart(source: Path, output: Path) -> None:
    result = subprocess.run(
        ["node", str(CHART_RENDERER), "--svg", str(source), "--output", str(output), "--width-in", "8", "--height-in", "4.5", "--ppi", "200"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _mixed_deck(tmp_path: Path) -> Path:
    heatmap = tmp_path / "heatmap.png"
    dual_axis = tmp_path / "dual-axis.png"
    deck = tmp_path / "presentation.pptx"
    _chart(SKILL / "examples" / "heatmap.svg", heatmap)
    _chart(SKILL / "examples" / "dual-axis.svg", dual_axis)
    result = subprocess.run(
        ["node", str(FIXTURE), str(deck), str(heatmap), str(dual_axis)],
        capture_output=True,
        text=True,
        check=False,
        cwd=BACKEND_ROOT,
    )
    assert result.returncode == 0, result.stderr
    return deck


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sandbox_command(workspace: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    adapter = SlidesWorkflowAdapter()
    args = list(
        build_bwrap_launch_args(
            workspace,
            codex_bin=Path("/usr/bin/true"),
            writable=True,
            backend_root=BACKEND_ROOT,
            skill_names=("pptx-nhi-tw",),
            read_only_paths=adapter.stage_read_only_paths("author", workspace),
            writable_paths=adapter.stage_writable_paths("author", workspace),
            restrict_workspace=True,
        )
    )
    del args[-4:]
    args.extend(command)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        cwd=BACKEND_ROOT,
        env={"PATH": "/usr/bin:/bin", "NODE_PATH": str(BACKEND_ROOT / "node_modules"), "PPTX_CJK_FONT": "Noto Sans CJK TC"},
        timeout=180,
    )


def test_mixed_chart_deck_snapshot_preserves_editable_chart_citations_and_notes(tmp_path: Path) -> None:
    deck = _mixed_deck(tmp_path)

    snapshot = build_deck_snapshot(deck)

    assert snapshot["slide_count"] == 4
    assert len(snapshot["slides"][1]["charts"]) == 1
    assert len(snapshot["slides"][2]["images"]) == 1
    assert len(snapshot["slides"][3]["images"]) == 1
    assert any("年度報告.pdf，PDF 第 12 頁" in item["text"] for item in snapshot["slides"][1]["text"])
    assert any("80、100、120件" in note for note in snapshot["slides"][1]["notes"])
    assert any("1.2、2.4、3.6" in note for note in snapshot["slides"][2]["notes"])
    assert any("比率：6%、9%、11%" in note for note in snapshot["slides"][3]["notes"])
    cover_text = " ".join(item["text"] for item in snapshot["slides"][0]["text"])
    assert "EvidenceStore" not in cover_text and "evidence.json" not in cover_text


@pytest.mark.skipif(
    not (bwrap_available() and Path("/usr/bin/node").is_file()),
    reason="worker-style Bubblewrap and /usr/bin/node are required",
)
def test_chart_generation_runs_in_author_bubblewrap_mounts(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    skill = workspace / ".agents" / "skills" / "pptx-nhi-tw"
    images = workspace / "work" / "images"
    evidence = workspace / "work" / "evidence.json"
    shutil.copytree(SKILL, skill)
    images.mkdir(parents=True)
    evidence.write_text("{}", encoding="utf-8")
    output = images / "heatmap.png"
    args = list(
        build_bwrap_launch_args(
            workspace,
            codex_bin=Path("/usr/bin/true"),
            writable=True,
            backend_root=BACKEND_ROOT,
            skill_names=("pptx-nhi-tw",),
            read_only_paths=(evidence,),
            writable_paths=(images,),
            restrict_workspace=True,
        )
    )
    del args[-4:]
    args.extend(("/usr/bin/node", str(skill / "scripts" / "render_chart_image.js"), "--svg", str(skill / "examples" / "heatmap.svg"), "--output", str(output)))
    environment = {"PATH": "/usr/bin:/bin", "NODE_PATH": str(BACKEND_ROOT / "node_modules"), "PPTX_CJK_FONT": "Noto Sans CJK TC"}

    result = subprocess.run(args, capture_output=True, text=True, check=False, env=environment, timeout=30)

    assert result.returncode == 0, result.stderr
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.skipif(
    not (shutil.which("soffice") and shutil.which("pdftoppm")),
    reason="LibreOffice and Poppler are required for the real render smoke test",
)
def test_mixed_chart_deck_real_preview_render_replaces_stale_pages(tmp_path: Path) -> None:
    deck = _mixed_deck(tmp_path)
    preview = tmp_path / "preview"
    pdf_dir = tmp_path / "preview-pdf"
    preview.mkdir()
    (preview / "slide-9.png").write_bytes(b"stale")
    command = [sys.executable, str(SLIDE_RENDERER), str(deck), "--output-dir", str(preview), "--pdf-dir", str(pdf_dir)]

    first = subprocess.run(command, cwd=BACKEND_ROOT, capture_output=True, text=True, check=False, timeout=180)
    second = subprocess.run(command, cwd=BACKEND_ROOT, capture_output=True, text=True, check=False, timeout=180)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert sorted(path.name for path in preview.glob("slide-*.png")) == ["slide-1.png", "slide-2.png", "slide-3.png", "slide-4.png"]


@pytest.mark.skipif(
    not (bwrap_available() and Path("/usr/bin/node").is_file() and shutil.which("soffice") and shutil.which("pdftoppm")),
    reason="worker Bubblewrap, Node, LibreOffice, and Poppler are required",
)
def test_changed_chart_data_survives_author_correction_and_backend_validation(tmp_path: Path) -> None:
    workspace = create_job_workspace("correction-job", tmp_path)
    stage_required_skills(workspace)
    source = tmp_path / "政策說明.md"
    source.write_text("# 給付範圍\n\n正確熱圖值為 3.6。", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("correction_source_extraction", EXTRACTION_SCRIPT)
    assert spec and spec.loader
    extraction = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extraction)
    extraction.extract([str(source)], str(workspace / "work" / "extracted"))
    evidence_path = workspace / "work" / "evidence.json"
    freeze_evidence(workspace / "work" / "extracted", evidence_path)
    write_source_manifest(
        workspace / "work" / "sources.json",
        ["annual.pdf", "policy.docx", "appendix.md"],
        ["年度報告.pdf", "政策說明.docx", "政策附件.md"],
    )
    evidence_bytes = evidence_path.read_bytes()

    skill = workspace / ".agents" / "skills" / "pptx-nhi-tw"
    chart_json = workspace / "work" / "images" / "chart.json"
    chart_png = workspace / "work" / "images" / "heatmap.png"
    dual_png = workspace / "work" / "images" / "dual-axis.png"
    deck = workspace / "output" / "presentation.pptx"
    preview = workspace / "work" / "rendered" / "preview"
    preview_pdf = workspace / "work" / "rendered" / "preview-pdf"

    def author_attempt(value: float) -> None:
        chart_json.write_text(json.dumps({
            "kind": "bar", "title": "給付範圍", "unit": "示範分數",
            "categories": ["甲區", "乙區", "丙區"],
            "series": [{"name": "分數", "values": [1.2, 2.4, value]}],
        }, ensure_ascii=False), encoding="utf-8")
        chart = _sandbox_command(workspace, ["/usr/bin/node", str(skill / "scripts" / "render_chart_image.js"), "--input", str(chart_json), "--output", str(chart_png)])
        assert chart.returncode == 0, chart.stderr
        dual = _sandbox_command(workspace, ["/usr/bin/node", str(skill / "scripts" / "render_chart_image.js"), "--svg", str(skill / "examples" / "dual-axis.svg"), "--output", str(dual_png)])
        assert dual.returncode == 0, dual.stderr
        generated = _sandbox_command(workspace, ["/usr/bin/node", str(FIXTURE), str(deck), str(chart_png), str(dual_png), str(skill / "assets" / "nhi_logo_large.png"), str(value)])
        assert generated.returncode == 0, generated.stderr
        rendered = _sandbox_command(workspace, [sys.executable, str(skill / "scripts" / "render_slides.py"), str(deck), "--output-dir", str(preview), "--pdf-dir", str(preview_pdf)])
        assert rendered.returncode == 0, rendered.stderr

    author_attempt(9.9)
    first = validate_candidate_deck(workspace, expected_slide_count=4, requested_title="圖表與引用整合測試", evidence_path=evidence_path)
    assert first.status is ValidationStatus.PASS, [item.as_dict() for item in first.findings]
    first_pptx = _sha(deck)
    first_preview = _sha(preview / "slide-3.png")
    first_final = _sha(workspace / "work" / "rendered" / "final" / "slide-3.png")
    (workspace / "work" / "rendered" / "final" / "slide-9.png").write_bytes(b"stale")

    author_attempt(3.6)
    second = validate_candidate_deck(workspace, expected_slide_count=4, requested_title="圖表與引用整合測試", evidence_path=evidence_path)
    assert second.status is ValidationStatus.PASS, [item.as_dict() for item in second.findings]
    assert _sha(deck) != first_pptx
    assert _sha(preview / "slide-3.png") != first_preview
    assert _sha(workspace / "work" / "rendered" / "final" / "slide-3.png") != first_final
    assert sorted(path.name for path in (workspace / "work" / "rendered" / "final").glob("slide-*.png")) == [f"slide-{index}.png" for index in range(1, 5)]
    assert evidence_path.read_bytes() == evidence_bytes
    assert load_frozen_evidence(evidence_path, extracted_dir=workspace / "work" / "extracted")["frozen"] is True
    assert any("3.6" in note and "9.9" not in note for note in second.deck_snapshot["slides"][2]["notes"])
    assert second.pptx_sha256 == _sha(deck)
    assert second.content_check["validator_binding"]["pptx_sha256"] == _sha(deck)
    assert list(preview.glob("slide-*.png"))
    assert not list((workspace / "work" / "rendered").glob(".backend_render_*"))

    denied = _sandbox_command(workspace, ["/usr/bin/node", "-e", "const fs=require('fs');for(const p of process.argv.slice(1)){try{fs.writeFileSync(p,'x');process.exit(2)}catch{}}", str(workspace / "work" / "rendered" / "final" / "forbidden"), str(workspace / "input" / "forbidden")])
    assert denied.returncode == 0, denied.stderr
