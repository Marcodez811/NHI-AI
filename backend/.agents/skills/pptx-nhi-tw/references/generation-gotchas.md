# Generation gotchas

- Set PPTXGenJS layout before adding slides. Use six-digit uppercase hex without `#`; use `transparency` for fills/images and `opacity` for shadows.
- Use one fresh PPTXGenJS instance and fresh options/shadow objects per output. Set text `margin: 0` when alignment is exact; do not add CJK character spacing by default.
- Keep simple bar, column, line, pie, and ordinary stacked charts editable with `addChart()`. Supply titles, zh-TW labels, data labels, and deliberate colors. For stacked charts use `ctr`, `inEnd`, or `inBase`.
- Use `scripts/render_chart_image.js --input` for its built-in bar/line layouts. Use `--svg` with a self-contained custom SVG for a combination or dual-axis chart, heatmap, dense labels or annotations, or a native chart that is still wrong after one layout correction. The script renders through the pinned `sharp` dependency; save the data or SVG, invocation, and PNG in `work/images/`. Do not use generated illustrations for numerical charts, substitute missing values with zero, rasterize a whole slide, or stretch/crop a chart image.
- Add only the chart PNG to the slide. Keep titles, labels outside the image when practical, and source footers editable. Put the chart's exact values, units, missing-value treatment, and calculations in speaker notes so the semantic reviewer can assess the image chart.

Example source-backed image chart:

```json
{
  "kind": "line",
  "title": "年度給付趨勢",
  "unit": "新臺幣百萬元",
  "categories": ["112年", "113年", "114年"],
  "series": [{"name": "給付金額", "values": [120.5, 132.1, null], "color": "#005BAC"}]
}
```

```bash
node .agents/skills/pptx-nhi-tw/scripts/render_chart_image.js \
  --input work/images/giving-trend.json \
  --output work/images/giving-trend.png \
  --width-in 8 --height-in 4.5 --ppi 200
```

For a heatmap or dual-axis combination chart, copy the appropriate self-contained
SVG from `examples/` into `work/images/`, then replace every demonstration label
and value with exact evidence-backed content. Never copy the demonstration values
into a real briefing. The SVG must have a viewBox matching the requested output
aspect ratio and may not contain scripts, entities, external images, stylesheets,
links, or `url()` references.

```bash
node .agents/skills/pptx-nhi-tw/scripts/render_chart_image.js \
  --svg work/images/policy-heatmap.svg \
  --output work/images/policy-heatmap.png \
  --width-in 8 --height-in 4.5 --ppi 200
```

Embed the chart at the exact dimensions used for rendering:

```javascript
slide.addImage({ path: "work/images/policy-heatmap.png", x: 0.8, y: 1.35, w: 8, h: 4.5 });
slide.addText(citation.display_text, { x: 0.8, y: 6.25, w: 8, h: 0.25, fontSize: 9, color: "617385", margin: 0 });
slide.addNotes(`圖表資料：${JSON.stringify(chartData)}\n${citation.display_text}`);
```
- Never pass ICNS, JXL, HEIF, or HEIC files directly to PPTXGenJS. Its transitive `image-size` parser has known denial-of-service advisories for those formats; convert them to PNG with the pinned `sharp` release before calling `addImage()`.
- Favor restrained government-briefing design: source-informed palette, generous whitespace, readable contrast, and a consistent motif. Avoid marketing language, decorative stripes, dense text-only slides, and italic CJK emphasis.
- Generate to `output/presentation.pptx`, then run the evidence and QA workflow before release.
