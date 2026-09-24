# Backend-written citations — implementation brief

Task brief for a coding agent. The ground rules in
[`9_19_implementation_plan.md`](./9_19_implementation_plan.md) apply unchanged. Work on a
branch `backend-citations` from `main`, commit per logical piece, and do not merge or
push. **No live model runs**: the user runs the live test.

---

## Why

Today the author (Codex, writing PptxGenJS) types every citation itself: each content
footer `[N] 資料來源：<name>，<locator>` and the whole final `參考資料` slide. The
validator then checks whether it typed them correctly, and on failure the entire deck is
regenerated. Most live-run failures so far were exactly this: malformed reference entries,
closing captions, entries split across paragraphs, combined multi-source footers, and
document titles invented instead of knowledge-base names.

Citation text is mechanical: it follows directly from *which evidence supports which
slide*. So split the work:

- **The author decides which evidence supports each slide.** That is a judgement, and it
  stays with the model; the semantic reviewer still checks it.
- **Backend code writes every citation.** Numbering, source names, locators, footers and
  the references slide become correct by construction: deterministic code, not a model
  that usually gets it right. That is also the credibility argument: a policy deck's
  citations come from code, not from model behaviour.

The author keeps full control of layout and styling. Nothing about how slides look
changes.

---

## Current behaviour to read first

| Where | What |
| --- | --- |
| `backend/app/services/slides/runtime.py` | author prompt: footer format, `參考資料` rules, one-paragraph-per-entry, no combined footers |
| `backend/.agents/skills/pptx-nhi-tw/SKILL.md` step 3 | the same rules for the author; both must stay consistent |
| `backend/app/services/slides/adapter.py:639` | `post_author_completion_check`, which already runs `clean_candidate_deck` (line 652) after every author attempt |
| `backend/app/services/slides/adapter.py` (~line 358) | the author's single-file writable grant for `work/outline_mapping.json`; the pattern to copy |
| `backend/app/services/slides/source_manifest.py` | `load_source_manifest` / `SlideSource`: staged filename → knowledge-base `display_name` (`work/sources.json`) |
| `work/evidence.json` | blocks with `id` and `citation` (`source_name`, `section_path`, `locator_label`, `display_text`); schema in `backend/.agents/skills/source-document-extraction/schemas/evidence_store_v1.json` |
| `backend/app/services/slides/validation.py` ~1256–1560 | today's citation checks: `_citation_source_name_findings`, `_references_slide_findings`, `_combines_citation_sources`, numbering, duplicates, mismatches |
| `build_review_prompt` in `adapter.py` | the reviewer prompt's citation guidance |

---

## Design (decided)

### 1. The author declares, and never types citations

After building the deck, the author writes `work/slide_citations.json`:

```json
{ "slides": { "3": ["<evidence block id>"], "6": ["<id>", "<id>"] } }
```

- Keys are 1-based **content** slide numbers; values are evidence block ids from
  `work/evidence.json`.
- A slide with factual claims, figures or charts lists at least one id. The cover and
  section dividers are omitted.
- Grant the author a **single-file** writable grant for `work/slide_citations.json`, like
  `outline_mapping.json`. Never widen a grant.

### 2. Placeholders: the author owns *where*, code owns *what*

- On every cited content slide, the author places one text box whose entire text is
  exactly `{{CITATION}}`, positioned and styled as the footer should appear.
- The author still builds the final slide titled exactly `參考資料`, with one text box
  whose entire text is exactly `{{REFERENCES}}`, styled as the entries should appear.
- The author never types a source name, a `[N]` marker or a locator anywhere.

### 3. The backend writes the citations

Run after `clean_candidate_deck` in the post-author step:

1. **Source identity** is the staged source (the `sources.json` entry), **not** the
   `display_name`: two knowledge-base documents can share a display name.
2. **Numbering:** one number per source, in first-appearance order (slide order, then id
   order within a slide).
3. **Footer**, replacing `{{CITATION}}`: one paragraph per source cited on that slide,
   `[N] 資料來源：<display_name>，<locators>`, where `<locators>` are the distinct
   evidence-backed locators of that slide's cited blocks from that source, joined with
   `、`. With no reliable locator, use `[N] 資料來源：<display_name>`. Never invent a
   locator. Reuse the locator wording the current author prompt specifies (section path,
   PDF page, text line).
4. **References**, replacing `{{REFERENCES}}`: one paragraph per source,
   `[N] <display_name>，<distinct locators cited anywhere in the deck>`.
5. **Preserve the placeholder's styling**: copy the placeholder run's paragraph and run
   properties onto each generated paragraph.
6. **Package safety, hard requirement.** The backend has `lxml` but no PPTX library. Edit
   slide XML with `lxml`, which preserves namespace prefixes, or add `python-pptx` if you
   judge it cleaner. **Never use `xml.etree.ElementTree` to write any package part**: it
   renames prefixes and made a deck unreadable to LibreOffice last week (see
   `PACKAGE_XML_REWRITE_SENSITIVE_PARTS` in `contracts.py`). Write the package
   atomically.

### 4. Author mistakes must be retryable, never fatal

Exceptions raised from `post_author_completion_check` end the job as "author output check
failed"; they are **not** retried. So the citation writer must **never raise for an author
mistake**. When its inputs are invalid (missing or malformed `slide_citations.json`, an
unknown evidence id, a slide number out of range, a cited slide without `{{CITATION}}`, a
missing `{{REFERENCES}}` box), leave the deck untouched and record the problems, e.g. in
`work/intermediate/citation_writer.json`. The validator then reports them as ordinary
`origin="candidate"` findings, so the author gets a retry with actionable feedback. Only a
genuine backend fault may raise.

### 5. Validation

- **Keep every existing citation check.** They now verify the backend's output: defence in
  depth that would catch a bug in the writer. **Acceptance criterion: the generated
  citations pass today's checks unchanged.**
- Add candidate findings for the recorded writer problems, and for any `{{CITATION}}` or
  `{{REFERENCES}}` text that survives into the delivered deck.

### 6. Prompts

- `runtime.py` and `SKILL.md`, which must agree: remove the footer format, the references
  entry format, the one-paragraph rule and the combined-footer rule. Add
  `slide_citations.json`, the two placeholders, and "never type citation text, source
  names or `[N]` markers". Keep the rule that the references slide contains nothing
  besides its title and the `{{REFERENCES}}` box.
- `build_review_prompt`: citations are now generated from the author's declared evidence
  ids. The reviewer's job is unchanged: judge whether the cited evidence supports each
  slide's claims, and never raise structural or format findings about citations.

---

## Out of scope

- Template-rendered slides (the model writes content as JSON and code draws every slide).
  Deliberately **deferred** until the product is usable and cost optimisation becomes the
  priority; see `architecture-observations.md`.
- The planner and outline, the frontend, and any runner changes.

---

## Tests (offline, fakes and fixtures only)

- Numbering by first appearance; one number per source across slides; two sources on one
  slide produce two footer paragraphs.
- Two sources sharing a `display_name` keep distinct numbers.
- Locators: distinct, joined with `、`; omitted when absent; never invented.
- Placeholder styling preserved; no placeholder text survives.
- Each author mistake in section 4 yields a candidate finding and **no exception**.
- **A deck written by the citation writer passes every existing citation check.**
- **The written package still loads in LibreOffice.** Use the project's existing renderer
  path, as `validation.py` does, and skip only if `soffice` is unavailable in the test
  environment.
- Prompt content: the new instructions are present in both `runtime.py` and `SKILL.md`.

## Verification

`cd backend && uv run --group dev pytest tests/ -q`. Baseline is **396 passed / 3
skipped**. Nothing may fail; explain any other drift.

## Report back

Under 250 words: where the writer runs, `lxml` or `python-pptx` and why, how author
mistakes become retryable findings, a sample generated footer and references entry, test
count, commit hashes, and what to watch on the first live run.
