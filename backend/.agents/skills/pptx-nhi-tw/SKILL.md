---
name: pptx-nhi-tw
description: Create, edit, inspect, and quality-assure Traditional-Chinese PowerPoint (.pptx/.potx) briefings for Taiwan National Health Insurance (全民健康保險), 健保署, 衛生福利部, and other Taiwanese government healthcare-policy audiences. Use for NHI/健保 policy decks, government briefings, template-based slide work, and evidence-traceable zh-TW presentations.
---

# NHI Taiwan PPTX

Create formal, editable, Traditional-Chinese policy briefings. Keep the final artifact at `output/presentation.pptx`.

## Non-negotiable visual identity

- Use a light theme across the entire deck: white, off-white, or very light neutral surfaces with restrained NHI blue/green accents. Do not introduce dark-theme slides or dark title-page backgrounds.
- The first slide must include exactly one unmodified official NHI logo from `assets/nhi_logo_large.png` or `assets/nhi_logo_small.png`. Prefer the large logo when horizontal space permits; use the small seal for compact layouts. Preserve aspect ratio, transparency, colors, and clear space. Never redraw, recolor, crop, stretch, or synthesize the logo.
- Treat the title slide as a deliberate cover, not a content slide: clear title hierarchy, generous whitespace, readable date/office metadata, and no decorative element competing with the logo.
- Keep typography, grid, margins, palette, icon/chart treatment, and information density coherent from cover through conclusion.
- Ground the visual design in the bundled examples: read `references/reference-style.md` and inspect the selected family's preview in `slides_reference/previews/` before authoring. Default to the teal policy-briefing family; follow an explicitly supplied template or user-selected reference instead when present. Use the examples' visual grammar, not their facts, speakers, dates, photographs, or logos.

## Author workflow

1. Treat `work/evidence.json` as the frozen, authoritative factual source. Do not open original source documents or extraction chunks, and do not modify the EvidenceStore. Referenced source images are available under `work/extracted/assets/`.
2. Read `references/nhi-zh-tw.md` and `references/reference-style.md` before drafting. Choose one reference family, inspect its bundled preview, and select layouts by the evidence relationships (trend, comparison, care pathway, or policy architecture). Reference PDFs are design references only and are exempt from the restriction on opening original factual source documents; they cannot supply claims or citations. Read `references/generation-gotchas.md` before generating, and `references/template-editing.md` before modifying a template.
3. Design the narrative and generate or edit `output/presentation.pptx`. The requested slide count means content slides: create exactly that many, then append one final slide titled exactly `參考資料`. Keep factual synthesis traceable to EvidenceStore block IDs in your working reasoning, but never expose block IDs, hashes, `EvidenceStore`, or `work/evidence.json` in the delivered deck. Treat `work/sources.json` as the authoritative mapping from staged evidence filenames to knowledge-base `display_name` values: every visible citation must use the mapped `display_name`, even when `citation.source_name`, `citation.display_text`, a parsed document title, or a collision-suffixed staged filename disagrees. On the final references slide, list each source cited by the content slides exactly once as `[1]`, `[2]`, and so on; a source used on several slides keeps one number. Each entry uses the allowlisted `display_name` plus every available evidence-backed section path and PDF-page or text-line locator. If a retained block has no `citation`, use `provenance.source` only to select the matching staged filename in `work/sources.json`, then use that entry's `display_name` plus any reliable provenance locator, falling back to the allowlisted name alone only when no reliable locator exists. The references slide must be derived from frozen evidence; never fabricate a bibliography entry, expose XML paths, or invent source metadata. Do not create or write speaker notes: the delivered PPTX must contain no `ppt/notesSlides/` parts. Do not add citations to a cover or section divider that contains no factual claim.
4. Keep simple bar, column, line, pie, and ordinary stacked charts editable with `addChart()`. Render combination or dual-axis charts, heatmaps, dense annotations, and a native chart that remains visually incorrect after one correction as a PNG from exact source-backed values. Use `scripts/render_chart_image.js --input` for its built-in bar/line layouts and `--svg` for a self-contained custom SVG such as a heatmap or dual-axis chart. Supply the image's intended PowerPoint width and height to the renderer, use the same dimensions with `addImage()`, and create the PNG in `work/images/`. Rasterize only the chart and keep surrounding text and citations editable.
5. Render the complete deck with the backend-compatible pipeline below. Compare the cover, one data slide, and one policy diagram against the chosen reference preview, checking headline hierarchy, visual density, color roles, and recurring header/footer placement. Inspect the sequence and every slide for clipping, overlap, unreadable text, weak hierarchy, inconsistent layout, citations, and visual defects. Fix problems and regenerate the deck and every render until the candidate is visually sound.

   ```bash
   python .agents/skills/pptx-nhi-tw/scripts/render_slides.py output/presentation.pptx
   ```

6. Finish with exactly one `work/rendered/preview/slide-<number>.png` per slide for your own inspection. The bundled renderer accepts `pdftoppm`'s padded names, writes canonical unpadded names, removes stale previews, and records non-authoritative diagnostics in `work/rendered/preview/render_metadata.json`. The backend independently creates `work/rendered/final/` from the PPTX after deterministic validation; never create or edit that directory.

The backend owns deterministic validation, `content_check.json`, `deck_snapshot.json`, semantic review, retry decisions, and publication. Do not create or edit those artifacts.

## Bundled commands

The validation scripts remain bundled for backend use. The author must not generate validator-owned reports.

- `scripts/thumbnail.py`, `scripts/add_slide.py`, `scripts/clean.py`, and `scripts/office/` retain the authorized upstream PPTX tooling. Pass a named thumbnail prefix; run `clean.py` only after slide ordering is final.
- Content and report validators use Python’s ZIP/XML support and do not depend on MarkItDown. MarkItDown remains useful for human text review.
- Use the visual-inspection guidance in `references/qa.md`; ignore its legacy report-writing steps because reports are now backend-owned.

## References

- `references/reference-style.md` — reference PDF catalog, visual families, reusable layouts, and comparison criteria; read for every new deck.
- `references/generation-gotchas.md` — PPTXGenJS, native charts, and CJK layout constraints.
- `references/nhi-zh-tw.md` — terminology, official register, ROC calendar, and typography.
- `references/template-editing.md` — safe template analysis and OOXML editing.
- `references/qa.md` — author visual-review procedure and backend artifact ownership.
