from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image


SKILL = Path(__file__).resolve().parents[3] / ".agents" / "skills" / "pptx-nhi-tw"
SCRIPT = SKILL / "scripts" / "render_chart_image.js"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["node", str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _write_chart(path: Path, **overrides: object) -> None:
    value: dict[str, object] = {
        "kind": "line",
        "title": "年度給付趨勢",
        "unit": "新臺幣百萬元",
        "categories": ["112年", "113年", "114年"],
        "series": [{"name": "給付金額", "values": [120.5, 132.1, None]}],
    }
    value.update(overrides)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _node_json(expression: str) -> object:
    result = subprocess.run(
        ["node", "-e", f"const m=require(process.argv[1]); (async()=>console.log(JSON.stringify(await ({expression}))))().catch(e=>{{console.error(e.message);process.exit(1)}});", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_builtin_chart_uses_requested_slide_size_and_keeps_missing_values(tmp_path: Path) -> None:
    source = tmp_path / "trend.json"
    output = tmp_path / "trend.png"
    _write_chart(source)

    result = _run("--input", str(source), "--output", str(output), "--width-in", "7.5", "--height-in", "4", "--ppi", "200")

    assert result.returncode == 0, result.stderr
    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.size == (1500, 800)
    svg = _node_json("m.builtInSvg({kind:'line',categories:['甲','乙','丙'],series:[{name:'值',values:[1,null,3]}]},1600,900)")
    assert str(svg).count("M") == 2  # the missing value creates two line segments
    assert 'data-value="null"' not in str(svg)


def test_small_and_large_ranges_produce_distinct_axis_labels() -> None:
    labels = _node_json("(()=>{const a=m.axis([0.001,0.002,0.003,0.004],false); const f=m.formatter(a.step); return a.ticks.map(f)})()")
    assert len(labels) == len(set(labels))
    assert any(label not in {"0", "0.00"} for label in labels)
    large = _node_json("(()=>{const a=m.axis([1e9,2e9],false); const f=m.formatter(a.step); return a.ticks.map(f)})()")
    assert len(large) == len(set(large))


def test_axis_handles_zero_constant_negative_and_mixed_ranges() -> None:
    domains = _node_json("[m.axis([0,0],true),m.axis([5,5],true),m.axis([-8,-2],true),m.axis([-2,7],true)]")

    for domain in domains:
        assert domain["lower"] < domain["upper"]
        assert all(isinstance(value, (int, float)) for value in domain["ticks"])
    assert domains[0]["lower"] <= 0 <= domains[0]["upper"]
    assert domains[2]["lower"] <= 0 <= domains[2]["upper"]
    assert domains[3]["lower"] <= 0 <= domains[3]["upper"]


@pytest.mark.parametrize("example", ["heatmap.svg", "dual-axis.svg"])
def test_custom_svg_examples_render_without_stretching(tmp_path: Path, example: str) -> None:
    output = tmp_path / f"{example}.png"

    result = _run("--svg", str(SKILL / "examples" / example), "--output", str(output), "--width-in", "8", "--height-in", "4.5", "--ppi", "200")

    assert result.returncode == 0, result.stderr
    with Image.open(output) as image:
        assert image.size == (1600, 900)


def test_invalid_custom_svg_does_not_replace_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "external.svg"
    output = tmp_path / "chart.png"
    source.write_text('<svg viewBox="0 0 1600 900"><image href="https://example.test/chart.png"/></svg>', encoding="utf-8")
    output.write_bytes(b"existing")

    result = _run("--svg", str(source), "--output", str(output))

    assert result.returncode != 0
    assert "self-contained" in result.stderr
    assert output.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".chart.png.*.tmp"))


def test_builtin_layout_rejects_content_that_cannot_fit(tmp_path: Path) -> None:
    source = tmp_path / "long.json"
    output = tmp_path / "long.png"
    _write_chart(
        source,
        categories=["這是一個遠超過內建圖表配置容量而且無法安全換行的分類名稱" * 10],
        series=[{"name": "值", "values": [1]}],
    )

    result = _run("--input", str(source), "--output", str(output))

    assert result.returncode != 0
    assert "too wide" in result.stderr
    assert not output.exists()


def test_measured_layout_distinguishes_wide_and_narrow_glyphs() -> None:
    widths = _node_json("(()=>{const f=m.resolveFontFamily();const x=m.createTextMeasurer(f,200);return Promise.all([x('WWWW',10),x('iiii',10)])})()")

    assert widths[0]["width"] > widths[1]["width"] * 2


def test_measured_layout_wraps_cjk_and_mixed_labels_and_scales_with_ppi() -> None:
    chart = "{kind:'bar',title:'全民健康保險年度給付趨勢 Annual Trend',unit:'新臺幣百萬元',categories:['臺北市年度申報量','New Taipei 年度申報量','桃園市年度申報量','臺中市年度申報量','臺南市年度申報量','高雄市年度申報量'],series:[{name:'核定給付 Approved',values:[120,150,130,160,140,170]}]}"
    layouts = _node_json(f"Promise.all([m.layoutBuiltIn({chart},1600,900,200),m.layoutBuiltIn({chart},2400,1350,300)])")

    first, second = layouts
    assert any(len(lines) > 1 for lines in first["categoryLines"])
    assert second["left"] / first["left"] == pytest.approx(1.5, rel=0.04)
    assert second["plotWidth"] / first["plotWidth"] == pytest.approx(1.5, rel=0.04)
    assert second["plotHeight"] / first["plotHeight"] == pytest.approx(1.5, rel=0.04)


def test_measured_layout_accounts_for_wide_ticks_units_and_legend_rows() -> None:
    layout = _node_json("m.layoutBuiltIn({kind:'line',title:'A measured title that needs wrapping across the available width',unit:'每一百萬名被保險人的核定案件數',categories:['112年','113年'],series:[{name:'第一個很長的系列名稱',values:[1000000000,1200000000]},{name:'第二個很長的系列名稱',values:[900000000,1100000000]},{name:'第三系列',values:[800000000,1000000000]},{name:'第四系列',values:[700000000,900000000]}]},1600,900,200)")

    assert layout["left"] > layout["pad"]
    assert layout["legendRows"] == 2
    assert layout["plotWidth"] >= 800
    assert layout["plotHeight"] >= 360


def test_missing_explicit_font_is_actionable(tmp_path: Path) -> None:
    source = tmp_path / "trend.json"
    output = tmp_path / "trend.png"
    _write_chart(source)
    result = subprocess.run(
        ["node", str(SCRIPT), "--input", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
        env={**__import__("os").environ, "PPTX_CJK_FONT": "Definitely Missing Chart Font 987654"},
    )

    assert result.returncode != 0
    assert "unavailable" in result.stderr
    assert not output.exists()


def test_chart_cli_rejects_low_resolution_and_conflicting_inputs(tmp_path: Path) -> None:
    source = tmp_path / "trend.json"
    output = tmp_path / "trend.png"
    _write_chart(source)

    low_resolution = _run("--input", str(source), "--output", str(output), "--ppi", "150")
    conflicting = _run("--input", str(source), "--svg", str(SKILL / "examples" / "heatmap.svg"), "--output", str(output))

    assert low_resolution.returncode != 0 and "at least 200" in low_resolution.stderr
    assert conflicting.returncode != 0 and "exactly one" in conflicting.stderr
