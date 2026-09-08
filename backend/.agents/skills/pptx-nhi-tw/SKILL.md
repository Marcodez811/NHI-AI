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

## Author workflow

1. Treat `work/evidence.json` as the frozen, authoritative factual source. Do not open original source documents or extraction chunks, and do not modify the EvidenceStore. Referenced source images are available under `work/extracted/assets/`.
2. Read `references/nhi-zh-tw.md` before drafting. Read `references/generation-gotchas.md` before generating, and `references/template-editing.md` before modifying a template.
3. Design the narrative and generate or edit `output/presentation.pptx`. Preserve native PowerPoint text, tables, and charts; use `addChart()` for chartable data. Keep factual synthesis traceable to block IDs from the EvidenceStore in your working reasoning.
4. Render the complete deck with the backend-compatible pipeline below. Inspect the sequence and every slide for clipping, overlap, unreadable text, weak hierarchy, inconsistent layout, and visual defects. Fix problems and regenerate the deck and every render until the candidate is visually sound.

   ```bash
   mkdir -p work/rendered/pdf work/rendered/final
   python .agents/skills/pptx-nhi-tw/scripts/office/soffice.py --headless --convert-to pdf --outdir work/rendered/pdf output/presentation.pptx
   pdftoppm -png -r 150 work/rendered/pdf/presentation.pdf work/rendered/final/slide
   ```

5. Finish with exactly one `work/rendered/final/slide-<number>.png` per slide. Remove stale renders after slide-count or ordering changes.

The backend owns deterministic validation, `content_check.json`, `deck_snapshot.json`, semantic review, retry decisions, and publication. Do not create or edit those artifacts.

## Bundled commands

The validation scripts remain bundled for backend use. The author must not generate validator-owned reports.

- `scripts/thumbnail.py`, `scripts/add_slide.py`, `scripts/clean.py`, and `scripts/office/` retain the authorized upstream PPTX tooling. Pass a named thumbnail prefix; run `clean.py` only after slide ordering is final.
- Content and report validators use Python’s ZIP/XML support and do not depend on MarkItDown. MarkItDown remains useful for human text review.
- Use the visual-inspection guidance in `references/qa.md`; ignore its legacy report-writing steps because reports are now backend-owned.

## References

- `references/generation-gotchas.md` — PPTXGenJS, native charts, and CJK layout constraints.
- `references/nhi-zh-tw.md` — terminology, official register, ROC calendar, and typography.
- `references/template-editing.md` — safe template analysis and OOXML editing.
- `references/qa.md` — EvidenceMap/SlideReview/QAReport contracts and release checks.
