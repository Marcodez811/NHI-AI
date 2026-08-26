# QA and contracts

`EvidenceMap v1` is required before generation. It records each displayed claim, its slide number, claim type, and one or more `{document_id, block_id}` sources. `block_id` must be an extracted `sha256:<hex>` ID; validate it against the extraction manifest and its document indexes/chunks.

## Mandatory review-and-revision loop

Run this loop after a complete draft is generated. This is an agent task, not an optional human follow-up.

1. Render every slide to `work/rendered/review-round-N/` and create a contact sheet that preserves presentation order.
2. Review the contact sheet for deck-wide coherence:
   - narrative: the title promises the story that follows; sections progress logically; transitions are understandable; the conclusion answers the opening objective;
   - factual: terminology, names, dates, units, denominators, and policy positions remain consistent; apparent conflicts are explicitly resolved; every displayed factual claim still matches the EvidenceMap;
   - design: the entire deck uses one light-theme system with stable typography hierarchy, grid, margins, palette, chart/icon language, footer treatment, and density rhythm;
   - branding: slide 1 has a light background and one official NHI logo from `assets/`, shown without alteration and with adequate clear space.
3. Inspect every full-size slide for overflow, clipping, overlap, low contrast, illegible labels, awkward line breaks, inconsistent alignment, unsupported decorative elements, template leftovers, and excessive text density.
4. Record each finding with its slide number(s), category, severity, and resolution. Any factual contradiction, broken narrative transition, missing/altered logo, dark-theme slide, unreadable content, clipping/overlap, or inconsistent core design system is blocking.
5. If any blocking finding exists, revise the deck, discard stale renders and reports, render a new numbered review round, and repeat all checks. Do not mark an unresolved problem as advisory merely to pass the gate.
6. When a round has no open blocking findings, copy that round's final PNGs to `work/rendered/final/` and write `work/intermediate/review_report.json` as `SlideReview v1`. Include a substantive note for every required check, the SHA-256 of every final PNG, and all findings/resolutions from the final round.
7. Validate the report:

   ```bash
   python .agents/skills/pptx-nhi-tw/scripts/review_report.py validate \
     --pptx output/presentation.pptx \
     --renders work/rendered/final \
     --report work/intermediate/review_report.json
   ```

The validator rejects stale PPTX/render hashes, missing slide renders, a missing official logo on the first slide, non-pass review checks, and open blocking findings. A passing report is the release gate for the agent's narrative, aesthetic, and deck-wide coherence review.

Use this shape (fill it with actual final-round observations and hashes; do not copy the example notes verbatim):

```json
{
  "format_version": "SlideReview v1",
  "presentation": {"path": "output/presentation.pptx", "sha256": "<64 hex>", "slide_count": 15},
  "review_round": 2,
  "checks": {
    "narrative_coherence": {"status": "pass", "notes": "<what was checked across the sequence>"},
    "factual_coherence": {"status": "pass", "notes": "<terminology, dates, units, claims>"},
    "design_coherence": {"status": "pass", "notes": "<light theme, grid, type, color, density>"},
    "title_branding": {"status": "pass", "notes": "<logo asset, light cover, hierarchy, clear space>"},
    "visual_quality": {"status": "pass", "notes": "<full-size inspection of every slide>"}
  },
  "findings": [],
  "reviewed_renders": [{"slide_number": 1, "sha256": "<64 hex>"}],
  "overall_status": "pass"
}
```

## Final deterministic checks

Run these checks after the final PPTX bytes and final review renders are written:

1. `check_pptx_content.py` scans slide OOXML directly for leftover placeholder patterns and a conservative Simplified-Chinese tripwire. Treat any tripwire hit as a blocking human review; it is intentionally not a complete language detector.
2. Run `.agents/skills/pptx-nhi-tw/scripts/office/validate.py`; use `--original` for a template-derived deck.
3. Render with `.agents/skills/pptx-nhi-tw/scripts/office/soffice.py --headless --convert-to pdf output/presentation.pptx`. The resulting final PNGs must be the same bytes reviewed in the passing `SlideReview v1` report.
4. Create `QAReport v1` with `office_validation` and `visual_review` both set to `pass` only after Office validation and `SlideReview v1` validation pass. Validate the report immediately. It verifies the final PPTX SHA-256, OOXML-derived slide count, and EvidenceMap SHA-256.

The deterministic scripts enforce artifact integrity and release gates; the agent's rendered-slide inspection supplies the semantic and aesthetic judgment. A valid report does not prove policy correctness beyond the source links, so high-stakes policy approval still belongs to the responsible human reviewer.
