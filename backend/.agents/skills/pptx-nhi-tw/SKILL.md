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

## Required evidence-first workflow

1. Run `$source-document-extraction` first and require `work/extracted/manifest.json`. Do not create factual claims, figures, dates, or policy statements without source blocks.
2. Read `references/nhi-zh-tw.md` before drafting. Read `references/generation-gotchas.md` before generating, and `references/template-editing.md` before modifying a template.
3. Create `work/intermediate/evidence_map.json` conforming to `schemas/evidence_map_v1.json`. Cite each claim with `{document_id, block_id}` from the extracted manifest; use exact official terminology and mark a claim as an interpretation when it is not verbatim.
4. Validate the map before generation:

   ```bash
   python .agents/skills/pptx-nhi-tw/scripts/validate_evidence_map.py work/extracted/manifest.json work/intermediate/evidence_map.json
   ```

5. Generate or edit the deck. Preserve native PowerPoint text, tables, and charts; use `addChart()` for chartable data. Use the provided upstream tooling for thumbnails, safe slide duplication/cleanup, Office validation, and LibreOffice conversion.
6. Render the complete deck and perform the mandatory review-and-revision loop in `references/qa.md`. Review the deck both as a sequence and slide by slide for narrative/factual coherence, visual coherence, title branding, and aesthetic quality. Fix every blocking finding, then regenerate the PPTX and all final renders. Repeat until the final review passes.
7. Write `work/intermediate/review_report.json` conforming to `schemas/review_report_v1.json`, then validate it. The report must describe the final review round, bind every reviewed PNG and the PPTX by SHA-256, and contain no open blocking findings.
8. Create and validate `work/intermediate/qa_report.json` only after the review report passes. Both reports bind their SHA-256 and slide count to `output/presentation.pptx`; regenerate both after every deck change. Completion is forbidden until every deterministic check and every review check passes.

## Bundled commands

```bash
python .agents/skills/pptx-nhi-tw/scripts/check_pptx_content.py output/presentation.pptx --output work/intermediate/content_check.json --fail-on-findings
python .agents/skills/pptx-nhi-tw/scripts/office/validate.py output/presentation.pptx [--original template/example.pptx]
python .agents/skills/pptx-nhi-tw/scripts/review_report.py validate --pptx output/presentation.pptx --renders work/rendered/final --report work/intermediate/review_report.json
python .agents/skills/pptx-nhi-tw/scripts/qa_report.py create --pptx output/presentation.pptx --evidence-map work/intermediate/evidence_map.json --content-check work/intermediate/content_check.json --office-status pass --visual-status pass --output work/intermediate/qa_report.json
python .agents/skills/pptx-nhi-tw/scripts/qa_report.py validate --pptx output/presentation.pptx --evidence-map work/intermediate/evidence_map.json --report work/intermediate/qa_report.json
```

- `scripts/thumbnail.py`, `scripts/add_slide.py`, `scripts/clean.py`, and `scripts/office/` retain the authorized upstream PPTX tooling. Pass a named thumbnail prefix; run `clean.py` only after slide ordering is final.
- Content and report validators use Python’s ZIP/XML support and do not depend on MarkItDown. MarkItDown remains useful for human text review.
- Read `references/qa.md` for the review loop, report fields, visual QA, and limitations.

## References

- `references/generation-gotchas.md` — PPTXGenJS, native charts, and CJK layout constraints.
- `references/nhi-zh-tw.md` — terminology, official register, ROC calendar, and typography.
- `references/template-editing.md` — safe template analysis and OOXML editing.
- `references/qa.md` — EvidenceMap/SlideReview/QAReport contracts and release checks.
