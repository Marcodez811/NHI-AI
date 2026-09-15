# Visual QA

`work/evidence.json` is the authoritative factual source. Keep each displayed
claim traceable to its evidence blocks while drafting and revising.

After a complete draft, render it to `work/rendered/preview/` and inspect every
slide at full size. Check narrative progression, factual consistency, light
theme, official-logo treatment, clipping, overlap, contrast, label legibility,
alignment, template residue, text density, and audience-facing citations.
Compare representative generated slides with the chosen reference-family preview
from `reference-style.md`: headline scale, main graphic density, color roles,
and header/footer geometry should visibly relate to that family. Do not treat
reference facts or decorative assets as authorized presentation content.
Factual slides must cite recognizable filenames with the available section,
page, or line locator. Never show EvidenceStore names, paths, hashes, or block
IDs as citations or generation explanations; legitimate source discussion of
those technologies is allowed. For retained blocks without derived citations,
use the source basename and available provenance locator, falling back to the
filename alone. Confirm image charts have readable labels and that their
speaker notes preserve values, units, missing-value treatment, and calculation
notes. Correct blocking issues in the PPTX, replace stale previews, and repeat
the inspection.

The author owns only previews. Do not create final PNGs or any validation,
snapshot, review-report, or QA-report artifact. The backend independently
renders the validated PPTX into `work/rendered/final/`, runs structural checks,
and passes those final images to semantic review.
