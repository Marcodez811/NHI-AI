# Visual QA

`work/evidence.json` is the authoritative factual source, and
`work/sources.json` is the authoritative mapping to the knowledge-base document
names the user recognizes. Keep each displayed claim traceable to its evidence
blocks while drafting and revising.

After a complete draft, render it to `work/rendered/preview/` and inspect every
slide at full size. Check narrative progression, factual consistency, light
theme, official-logo treatment, clipping, overlap, contrast, label legibility,
alignment, template residue, text density, and audience-facing citations.
Compare representative generated slides with the chosen reference-family preview
from `reference-style.md`: headline scale, main graphic density, color roles,
and header/footer geometry should visibly relate to that family. Do not treat
reference facts or decorative assets as authorized presentation content.
Factual slides must use the numbered footer format required by `SKILL.md`, with
an allowlisted knowledge-base `display_name` and every available evidence-backed
section, page, or line locator. Confirm the last slide is titled exactly
`參考資料`, contains each cited source once, and uses the same source numbers as
the footers. Never show EvidenceStore names, internal paths, staged filenames,
hashes, or block IDs as citations or generation explanations; legitimate source
discussion of those technologies is allowed. For retained blocks without
derived citations, use provenance only to select the corresponding entry in
`work/sources.json`, then show its `display_name` and any reliable provenance
locator, falling back to the allowlisted name alone. Confirm every chart has a
descriptive title, readable labels, visible values where the chart design calls
for them, and units on its axis. Confirm the delivered PPTX contains no speaker
notes. Correct blocking issues in the PPTX, replace stale previews, and repeat
the inspection.

The author owns only previews. Do not create final PNGs or any validation,
snapshot, review-report, or QA-report artifact. The backend independently
renders the validated PPTX into `work/rendered/final/`, runs structural checks,
and passes those final images to semantic review.
