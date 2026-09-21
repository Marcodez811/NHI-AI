# Remove speaker notes; rework the citation model — 2026-09-20

Implementation plan. Self-contained for agents without session context; read
[`9_19_implementation_plan.md`](./9_19_implementation_plan.md) for the surrounding
migration and the ground rules, which apply here unchanged.

All decisions are settled. WI-1 is the natural first commit.

---

## What changes and why

Generated decks currently write **speaker notes** — the PowerPoint `notesSlide` part the
presenter sees in Presenter View and the audience never does. They carry three things:

1. expanded citation detail (source name, section path, page/line locator),
2. chart values, units and calculation notes,
3. outline node ids, added recently purely so deterministic validation could confirm the
   author followed the approved outline.

**The product should not ship speaker notes at all.** Item 3 was never a product
requirement and goes regardless. Items 1 and 2 carry real information, so removing the
notes field means deciding where that information goes instead — which is what most of
this plan is about.

The replacement model, decided with the product owner:

- **Each slide's footer** names its source and carries a numbered marker.
- **A references slide at the end** resolves those numbers to full citations.
- **Charts must be titled.** Chart values, units and calculation notes are dropped; an
  untitled chart is not defensible in a scrutiny setting, so the title carries the
  burden instead.

This is the conventional model for a sourced report, and it puts the audit trail in
front of the audience rather than in a field only the presenter sees.

---

## Decisions already made

| Decision | Rationale |
| --- | --- |
| **No abstraction leakage into the deck** | The slides contain only what the user knows: knowledge-base document names. No internal ids, derived titles, or pipeline artifacts. Governing principle — see WI-0. |
| Generated decks carry no speaker notes | Product decision. Not a presenter-notes product. |
| References slide is **excluded** from the requested slide count | It is deck furniture. Asking for 10 slides yields 10 content slides plus a references slide. |
| Footer shows **source document name + numbered marker** | Readable on its own, and traceable to the references slide. |
| Chart values/units/calculation notes are **dropped** | Values are visible in the chart; units belong on the axis. |
| Charts **must carry a name/title** | An untitled chart is not scientific. This is new and enforceable. |
| Outline node ids leave speaker notes | They were a validation convenience, never a product requirement. |

### A flagged inconsistency

The **cover slide is currently counted** inside `slides_count`: validation asserts the
requested title appears on slide 1 (`validation.py:1351`) and the plain slide-count check
(`validation.py:1307`) counts every slide in the file. So the deck already contains one
piece of "furniture" that *is* counted, while the new references slide will not be.

**Recommendation: leave the cover counted.** Excluding it too would change the length of
every deck the system has ever produced, for a consistency argument nobody asked for.
Document the asymmetry rather than fixing it. If the product owner would rather both be
excluded, that is a one-line change to the expected count plus a prompt update — but it
is a behaviour change for existing users and should be an explicit choice.

---

## Current behaviour, with evidence

Read these before changing anything.

| Location | What it does |
| --- | --- |
| `backend/app/services/slides/runtime.py:97-101` | Author prompt: footer gets `citation.display_text`; source name, section path and locator go to speaker notes. Forbids "a pipeline explanation to the cover or an **unsupported** bibliography slide". |
| `backend/app/services/slides/runtime.py:110-111` | Author prompt: chart values, units and calculation notes go to speaker notes. |
| `backend/app/services/slides/runtime.py:56` | Author prompt: node id as a standalone speaker-notes line (added for the outline cross-check). |
| `backend/.agents/skills/pptx-nhi-tw/SKILL.md:22` | The concrete instruction to call `slide.addNotes()` with expanded source, locator, claim and chart values. **This is the real notes-writing mandate**, not `runtime.py`. |
| `backend/app/services/slides/validation.py:470-482` | `build_deck_snapshot` extracts `notesSlide` parts into `snapshot["notes"]`. |
| `backend/app/services/slides/validation.py:1118-1163` | `_outline_cross_check`: matches node ids as standalone notes lines; emits `outline_node_missing` and `outline_slide_count_mismatch`. |
| `backend/app/services/slides/validation.py:1307-1308` | Plain `slide_count` check against `expected_slide_count`. |
| `backend/app/services/slides/adapter.py:437,443` | Reviewer prompt: "expanded references in slide notes"; "Speaker notes are author claims that must still match frozen evidence." |
| `backend/.agents/skills/pptx-nhi-tw/scripts/clean.py:212-226` | Already removes unreferenced notes slides. |
| `backend/.agents/skills/pptx-nhi-tw/scripts/add_slide.py:5,64` | Already strips the `notesSlide` reference when copying a slide. |

**Note on the "unsupported bibliography" prohibition.** The word *unsupported* is
load-bearing: the rule bans a fabricated reference list, not a references slide as such. A
references slide built from the frozen evidence store's `citation` objects is supported by
construction. This plan refines that rule; it does not overturn a safety constraint.

**Note on the skill scripts.** `clean.py` and `add_slide.py` already treat notes as
optional and removable. No script *mandates* notes — the mandate is prose in `SKILL.md`.
So this change is mostly prompt and validation work, not toolkit surgery.

---

## Work items

---

### WI-1 — Stop writing speaker notes

**Size:** small. **Depends on:** nothing.

1. `runtime.py`: remove the node-id instruction (`:56`), the "source name, section path,
   and locator in speaker notes" clause (`:100`), and the "put chart values, units, and
   calculation notes in speaker notes" sentence (`:110-111`).
2. `SKILL.md:22`: remove the `slide.addNotes()` mandate and its expanded-source content.
3. Replace both with an explicit prohibition: the delivered deck must contain **no
   speaker notes**. State it plainly — a model that has seen a lot of deck-authoring
   material will add notes by default unless told not to.

**Acceptance.** A generated deck has no `ppt/notesSlides/` parts. Existing render tests
still pass.

---

### WI-0 — No abstraction leakage: citations use knowledge-base names only

**Size:** medium. **Depends on:** nothing. **Governs WI-2 and WI-3.**

**Principle.** The delivered deck must contain only things the user recognises. Users know
the document names in their knowledge base. No internal identifier, derived title, or
pipeline artifact may appear in slide text, footers, or the references slide.

This is stricter than the existing rule at `runtime.py:97`, which forbids evidence IDs,
hashes, `EvidenceStore` and JSON filenames. Those are obvious internals. The real risk is
names that *look* legitimate:

| Leak | Where it comes from |
| --- | --- |
| A title parsed from the document's own cover page | `citation.source_name` is a free-form string the extraction agent produces (`evidence_store_v1.json:61-66`). Nothing ties it to the knowledge-base record. |
| `report__2.pdf` | `stage_uploads` appends `__N` on filename collision (`artifacts.py:251`); `provenance.source` carries the synthetic name. |
| A sha256 | The evidence store's `document_id` and block `id` are sha256 digests, not the app's document UUIDs — so the author currently has **no** path back to the knowledge-base name even if told to use it. |

**Task.**

1. In `prepare_input` (`adapter.py:191`), resolve the job's `document_ids` to their
   knowledge-base display names and write an authoritative list to the workspace — e.g.
   `work/sources.json`, mapping each staged `input/` filename to the name the user sees.
   The names must come from the documents table, not from the staged filename, so
   collision suffixes never reach the deck.
2. Grant that file read-only to the author and to the planner
   (`stage_read_only_paths`). Note the planner also needs it: outline `key_points`
   referencing a source should name it the way the user will.
3. Author prompt: cite **only** names from `work/sources.json`. Where `citation.source_name`
   disagrees with the knowledge-base name, the knowledge-base name wins. Locator detail
   (`PDF 第 12 頁`) still comes from the evidence block.
4. Deterministic check in `validation.py`: every source name appearing in a footer or
   references entry must match an entry in `work/sources.json`. Anything else is a
   blocking finding.

**Acceptance.** A job over `2025報告.pdf` produces citations saying `2025報告.pdf`, even
when the document's own title page says something else. A deck citing a name outside the
allowlist fails validation.

**Why this matters more than the prompt rule:** it converts "do not leak abstractions"
from an instruction the author may quietly disregard into a fact the backend verifies
against a list it controls.

---

### WI-2 — References slide

**Size:** medium. **Depends on:** WI-1.

Add a final references slide, built from the frozen evidence store's `citation` objects.

1. Author prompt (`runtime.py` and `SKILL.md`): require a references slide as the **last**
   slide, listing each cited source once, numbered, with the detail formerly in notes —
   source name, section path, page/line locator.
2. Numbering must match the footer markers from WI-3. One number per **source**, not per
   claim; a source cited on five slides keeps one number.
3. The existing rules still apply: never expose evidence block IDs, hashes,
   `EvidenceStore`, `work/evidence.json`, or XML paths; never invent a page, section,
   publisher, date or office. For a retained block without `citation`, derive a
   conservative reference from `provenance.source`'s basename plus any available locator,
   and use the filename alone when no reliable locator exists.
4. Update the "unsupported bibliography slide" clause so it forbids a *fabricated*
   reference list while requiring this evidence-derived one.

**Acceptance.** A deck drawing on three sources ends with a references slide listing
exactly those three, numbered, each traceable to the evidence store.

---

### WI-3 — Footer format

**Size:** small. **Depends on:** WI-2 (numbering must agree).

Footer becomes **source document name + numbered marker**, e.g.
`[1] 資料來源：年度報告.pdf`. Today it is `citation.display_text` alone; the existing
example in `SKILL.md:22` is `資料來源：年度報告.pdf，PDF 第 12 頁`.

Decide and document whether the per-slide locator (`PDF 第 12 頁`) stays in the footer or
moves entirely to the references slide. **Recommendation: keep it in the footer.** A
reader challenging a number on screen should not have to flip to the end, and it is the
current behaviour.

Unchanged: cite slides carrying factual claims, figures or charts; do not add citations to
a cover or section divider with no factual claim.

---

### WI-4 — Charts must be titled

**Size:** small. **Depends on:** WI-1.

1. Author prompt: every chart carries a descriptive title naming what it shows. Units
   belong on the axis label.
2. Drop the chart values/units/calculation-notes requirement entirely (removed in WI-1).
3. Consider a deterministic check that every chart part has a non-empty title.
   `_chart_snapshot` (`validation.py:401`) already parses chart XML, so the data is
   likely available — **verify before promising it**, and if the title is not in the
   snapshot, say so rather than inventing a check that cannot run.

**Acceptance.** A chart without a title produces a validation finding, or — if the
snapshot genuinely cannot see titles — the prompt requirement lands and the check is
explicitly documented as not enforceable.

---

### WI-5 — Validation: slide count and notes absence

**Size:** medium. **Depends on:** WI-1, WI-2.

1. The references slide is excluded from the requested count, so the plain check at
   `validation.py:1307` must expect `expected_slide_count + 1` when a references slide is
   present. Do not loosen the check to a range — that would forfeit one of the strongest
   deterministic guarantees in the pipeline.
2. Same adjustment for `outline_slide_count_mismatch` (`:1163`) and for
   `SlideOutline.total_slides`, whose meaning must stay "content slides".
3. Add a check that the deck contains **no** speaker notes, so a regression is caught
   deterministically rather than noticed in a delivered file.
4. Keep `build_deck_snapshot`'s notes extraction (`:470-482`). It now serves the
   absence check. Do not delete it.

**Acceptance.** A 10-slide request yields 10 content slides plus references and passes. A
deck with any notes part fails. `test_validation.py` covers both.

---

### WI-6 — Outline conformance via a sidecar mapping

**Size:** medium. **Depends on:** WI-1, WI-5 (content-slide count must be settled first).

`_outline_cross_check` (`validation.py:1118-1163`) currently proves the author followed
the approved outline by matching node ids in speaker notes. Replace that with an author-
written `work/outline_mapping.json` and structural verification against the deck.

This check is the only thing standing between "the user approved an outline" and "the deck
actually follows it". Without it a drifting author silently ignores the approved plan,
which removes much of the point of the planning phase — so it is replaced, not dropped.

Full rationale, the verification rules, and the implementation notes are in
**"Resolved: conformance via a sidecar mapping artifact"** below. Read that section
before starting.

---

### WI-7 — Reviewer prompt

**Size:** small. **Depends on:** WI-1.

`adapter.py:437` tells the reviewer to expect "expanded references in slide notes";
`:443` says "Speaker notes are author claims that must still match frozen evidence." Both
are now false.

Replace with the new model: references appear on the final references slide and in
footers, and both are author claims that must match frozen evidence. The reviewer should
treat an unsupported or fabricated reference entry as a blocking finding in the same way
it treats an unsupported slide claim.

---

### WI-8 — Skill documentation sweep

**Size:** small. **Depends on:** WI-1 through WI-4.

`backend/.agents/skills/pptx-nhi-tw/references/qa.md` and `generation-gotchas.md` both
mention speaker notes. Update them to match. Leave `clean.py`, `add_slide.py` and
`validators/pptx.py` alone — they already treat notes as optional, and `clean.py`'s
unreferenced-notes removal is now a useful backstop rather than something to delete.

---

## Resolved: conformance via a sidecar mapping artifact

**Decision: the author writes `work/outline_mapping.json`; nothing is marked in the deck.**

Guidance already works this way and needs no change: `work/outline.json` is granted
read-only to the author (`adapter.py:252`) and `build_prompt` instructs node-order
adherence and emphasis weighting. The node ids were never about guidance — they were
about verifying compliance afterwards.

An earlier draft of this plan recommended moving the ids into PPTX custom document
properties to "preserve the guarantee". That reasoning was wrong. **A node id in speaker
notes is an author attestation, not an independent fact** — the author writes it itself
and could place it on the wrong slide exactly as easily as it could fabricate a sidecar
entry. The in-deck marker bought deck pollution without buying trustworthiness, and
relocating it to document properties would have preserved the flaw somewhere less
visible.

What the check genuinely catches is **omission** — a section dropped, merged or split. A
sidecar artifact catches that equally, since the author must actively invent an entry for
a section it never wrote.

The sidecar is also strictly stronger, because it supports checks the notes approach could
not. Given a mapping of node id → slide range, validation can verify **against the deck
itself**:

- every approved node id appears exactly once;
- ranges are contiguous, non-overlapping, and in outline order;
- ranges fall within the deck's actual bounds;
- ranges collectively cover every content slide (references slide excluded);
- the total matches the deck's content-slide count.

Those are structural facts about the file, not author assertions. A drifting author cannot
satisfy them by lying in a single place.

**Implementation notes.**

- `work/outline_mapping.json` must be added to the author's `stage_writable_paths`
  (`adapter.py:262`), which currently grants only `output`, `work/rendered/preview`,
  `work/rendered/preview-pdf` and `work/images`.
- `_outline_cross_check` (`validation.py:1118-1163`) is rewritten to read the mapping
  instead of scanning `snapshot["notes"]`. Keep the `outline_node_missing` and
  `outline_slide_count_mismatch` finding codes; add codes for the structural violations
  above so the revision prompt can act on them specifically.
- A missing or malformed mapping file is itself a finding, not a silent pass.
- When no `work/outline.json` exists (planner disabled), none of this engages — unchanged
  from today.

---

## Sequencing

```
WI-0 (source-name allowlist) ───> governs WI-2 and WI-3; start it alongside WI-1

WI-1 (stop writing notes) ──┬──> WI-2 (references slide) ──> WI-3 (footer format)
                            ├──> WI-4 (chart titles)
                            ├──> WI-7 (reviewer prompt)
                            └──> WI-5 (validation) ──> WI-6 (outline conformance)
WI-8 (skill docs) ──────────────> last, after the behaviour settles
```

WI-1 is the natural first commit: it is a pure deletion, and every other item builds on
the vacuum it leaves.

---

## Hazards

1. **Do not loosen the slide-count check** to accommodate the references slide. Make the
   expectation exact and explicit.
2. **The real notes mandate is in `SKILL.md`**, not `runtime.py`. Changing only the Python
   prompt will leave the author still calling `slide.addNotes()`.
3. **Verify chart titles are in the snapshot** before promising a deterministic check.
4. **Citations are the product's credibility.** This deck is presented to the Legislative
   Yuan. Moving citation detail from notes to a references slide must not lose the
   locator — someone challenging a figure needs to find it.
5. **None of this is live-tested.** Every change here is to a prompt or a validator whose
   real behaviour only shows against a model. Structural tests prove the validator, not
   the author's compliance.
