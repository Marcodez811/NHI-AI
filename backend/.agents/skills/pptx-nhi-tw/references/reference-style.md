# Reference-driven NHI briefing design

## Authority and use

These examples define a visual target, not additional policy evidence. All facts,
numbers, quotations, charts, dates, speaker identities, and source citations in
the generated deck must still come from the job brief or frozen EvidenceStore.
Do not cite these PDFs merely because their layouts were used. Do not copy their
photographs, screenshots, diagrams containing policy claims, partner marks, or
dated event branding into a new deck. Use the bundled official NHI logo and
authorized evidence images; if imagery is unavailable, use a typographic cover.

An explicitly supplied PPTX template or requested reference takes precedence.
Otherwise use **teal policy briefing** as the default family. Choose a different
family when the requested audience or reference calls for it. Keep one family's
header/footer and palette throughout; borrow another family's diagram structure
without importing its competing chrome. Preserve the light theme and exactly one
official NHI logo on the first slide. English examples inform layout, not output
language: generated decks remain zh-TW unless requested otherwise.

## Reference catalog

All paths below are relative to the skill root. Page numbers are **PDF page
numbers**, not slide numbers printed on the page. Each preview contains six
selected pages labeled with their PDF page number. Open the chosen preview as an
image before drafting; consult a full-size PDF page only when more detail matters.

| Family / use | Source in `slides_reference/` | Preview | Pages to study |
|---|---|---|---|
| Teal policy briefing — default, program proposals and care delivery | `台美衛生福利政策研討會-大家醫計畫.pdf` (16 pages) | `slides_reference/previews/teal-policy.png` | 1 cover; 2 paired charts; 4 care continuum; 7 four challenges; 9 platform architecture; 16 closing |
| Navy institutional briefing — financing, broad policy review | `1140323-健康臺灣臺中論壇-健保署陳亮妤副署長20250319.pdf` (36 pages) | `slides_reference/previews/navy-forum.png` | 1 cover; 3 agenda; 8 revenue/expenditure comparison; 15 paired charts; 25 process plus evidence; 36 closing |
| Navy analytical lecture — evidence-heavy presentations | `1141007-國立空中大學演講-健保永續之挑戰(核定)-署長修Final 2.pdf` (40 pages) | `slides_reference/previews/navy-lecture.png` | 1 cover; 3 agenda; 5 aging comparison; 12 combination chart; 23 section transition; 40 closing |
| Light clinical section — chronic-care talks | `20251207_台灣基層糖尿病協會「114年冬季會暨學術研討會」幾頁.pdf` (7 pages) | `slides_reference/previews/clinical.png` | 1 section divider, not a complete-deck cover; 2 combination chart; 3 goals; 4–5 policy explanation; 7 next directions |
| Diagram structure reference — comparative care and digital services | `可參考的英文簡報.pdf` (11 pages) | `slides_reference/previews/english-diagrams.png` | 1 paired trends; 2 treemap; 3 life-course care; 6 challenges; 9 digital service; 11 two patient groups |

The source PDFs have varied fonts, density, and decorative elements. Transfer the
recurring strengths: large navy headlines, prominent evidence graphics, clearly
grouped policy relationships, selective emphasis, and compact sources. Do not
reproduce tiny labels, overfull paragraphs, 3D pies, stretched Chinese character
spacing, crowded logos, or decorative imagery without source authorization.

## Design recipe

The following are authoring defaults derived from the inspected examples, not an
official NHI brand specification or exact measurements of every PDF.

- **Canvas:** 16:9. For a new PPTXGenJS deck use `LAYOUT_WIDE` (13.333 × 7.5 in).
  Set a reusable master/grid before composing individual slides.
- **Color roles:** white `FFFFFF` surface, navy `002060` headline, charcoal
  `222222` body. Teal family: deep teal `1C5054`, cyan `00A9D5`, pale aqua
  `EAF5F4`. Navy family: navy with blue `0070C0` and restrained teal accents.
  Use amber `FFC000` for a selected stage/goal and red `C00000` for a small number
  of evidence-backed changes or risks; do not color entire paragraphs red.
- **Header:** reserve approximately the top 0.85–1.1 in for a strong headline.
  On content slides, an optional 0.38–0.48 in official seal at top left sits in a
  separate slot from the title. Start the title around x=1.05 in when using it,
  or x=0.55 in without it. A two-line title pushes content down; never overlap
  the logo or shrink all text to preserve a fixed content top.
- **Typography:** use the job's detected CJK-safe font consistently (Noto Sans
  CJK TC is suitable); use Microsoft JhengHei only when installed. For the wide
  canvas aim for 30–36 pt bold content titles, 36–44 pt cover titles, 18–22 pt
  body, 14–16 pt diagram/chart labels, and 10–12 pt source footers. Use bold navy
  or selective color for emphasis. Reflow long Chinese headings naturally.
- **Body and footer:** keep side margins near 0.5–0.65 in and panel gaps near
  0.2–0.3 in. Give the primary chart/diagram most of the body area. Reserve the
  bottom 0.35–0.5 in for source text and a consistent bottom-right page number.
  A light fill behind a short takeaway can separate it from the chart below.
- **Teal family chrome:** a small pale-teal angular header accent and matching
  restrained edge detail echo the policy reference. Keep them out of text boxes.
  Navy-family decks can instead use a thin teal baseline. Do not combine the
  mountain footer, multicolor top stripe, pastel circles, and teal corner motif.
- **Density:** these are substantive policy briefings, not a sequence of sparse
  marketing cards. Prefer one dominant graphic with a short conclusion, or two
  directly comparable panels. When evidence is too dense, simplify labels, move
  detail into notes, or redistribute content within the requested slide count.
  Do not add filler diagrams, goals, or numbers to mimic reference density.

## Layouts to reuse

Choose the structure from the message; do not force every slide into one layout.

1. **Cover:** large left-aligned topic, smaller purpose/subtitle, then supplied
   office/date metadata. Teal-policy PDF p. 1 balances this against one right-side
   visual. Use a clean light cover with the official logo when no authorized
   visual is available. Do not copy the reference's speaker or meeting date.
2. **Agenda / divider:** a compact numbered sequence of actual sections, with a
   distinct color for the current section, as in navy-forum PDF p. 3. Clinical
   PDF p. 1 offers a large section number plus short section title. Use these
   only when they improve navigation within the requested slide count.
3. **Evidence / trend:** conclusion headline, brief pale-background takeaway,
   then a large labeled chart and readable source footer (navy-lecture PDF p. 12;
   clinical PDF p. 2). Two charts can compare related trends (teal-policy p. 2).
   Keep source units, axes, denominators, and uncertainty. Use editable charts
   when feasible; use the existing image-chart workflow for dual-axis/complex
   charts. Never copy numbers from the reference to fill a missing series.
4. **Care continuum:** a horizontal progression of stages with aligned service
   columns beneath, connecting arrows, and a deliberate shared band for services
   spanning stages (teal-policy p. 4; English p. 3). Only draw connections and
   coverage supported by the evidence. Keep shapes and text editable.
5. **Challenges / policy response:** three or four differentiated groups, each
   with a short colored heading and concise evidence. Teal-policy p. 7 and
   English p. 6 use a four-part arrangement with arrows. Preserve arrows only
   where a real relationship exists; do not invent a cycle for decoration.
6. **Program/platform architecture:** organize actors or data sources on the
   left, the central service in the middle, and recipients/outcomes on the right
   (teal-policy p. 9). Label flows and boundaries, keep a small number of color
   roles, and use evidence-authorized screenshots only when readable at size.
7. **Comparison / next directions:** two aligned columns for patient groups or
   before/after policy, or three clearly separated directions (English p. 11;
   clinical p. 7). Compare the same attributes; do not default to unrelated icon
   cards. End with a sourced takeaway or a simple zh-TW closing as appropriate.

## Render comparison

Compare a rendered cover, a data slide, and a diagram (when present) to the chosen
preview. Check the large headline, readable body scale, substantial main graphic,
selective highlights, consistent logo slot, and source/page footer. Similarity
means these visual relationships, not matching the reference's exact prose or
decoration. Inspect all generated pages for overlap and excessively small text.
The final deck must remain editable except for authorized source images and the
existing chart-image exceptions. Never flatten a reference PDF page into a slide.

If previews are absent, render only selected source PDF pages using the backend's
Poppler tools, for example:

```bash
mkdir -p work/reference-previews
pdftoppm -f 4 -l 4 -scale-to 1600 -png -singlefile \
  .agents/skills/pptx-nhi-tw/slides_reference/台美衛生福利政策研討會-大家醫計畫.pdf \
  work/reference-previews/care-continuum
```

If neither previews nor PDF rendering are available, use this recipe and report
the missing visual comparison in the author handoff, not on the slides. Do not
install packages or access external design sources during backend jobs.
