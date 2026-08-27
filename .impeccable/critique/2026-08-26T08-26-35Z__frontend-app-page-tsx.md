---
target: frontend/app/page.tsx
total_score: 16
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
timestamp: 2026-08-26T08-26-35Z
slug: frontend-app-page-tsx
---
## Design Health Score

The reviewed surface is the desktop Slides view in its zero-document state. All ten Operate-surface heuristics apply.

| # | Heuristic | Score | Key issue |
|---|---|---:|---|
| 1 | Visibility of system status | 2/4 | Counts and warnings exist, but “可用 0 份” does not explain the blocked state or the next action. |
| 2 | Match system / real world | 2/4 | Source → settings is understandable, but “符合簡報來源格式” exposes implementation language rather than user language. |
| 3 | User control and freedom | 2/4 | Navigation is clear, yet an empty Slides state forces a context switch to Knowledge Base. |
| 4 | Consistency and standards | 1/4 | Traditional Chinese is mixed with English labels, CTA text, and the “Creation studio” eyebrow. |
| 5 | Error prevention | 2/4 | Generation is correctly disabled without a valid source, but the remedy is visually weak and easy to miss. |
| 6 | Recognition rather than recall | 2/4 | Users see counts and selected files, but must infer what makes a source eligible. |
| 7 | Flexibility and efficiency | 1/4 | There is no inline source search, upload, or filtering in the Slides flow. |
| 8 | Aesthetic and minimalist design | 1/4 | A large empty panel, pale surfaces, thin borders, and repeated rounded cards read as a generic template. |
| 9 | Error recovery | 2/4 | Retry paths exist in code, but the initial blocked state offers little recovery guidance. |
| 10 | Help and documentation | 1/4 | The empty state gives almost no instruction beyond “select from Knowledge Base.” |
| **Total** |  | **16/40** | **Needs substantial refinement** |

## Design Specificity Verdict

**LLM assessment:** Yes, it looks cooked—specifically undercooked rather than catastrophically broken. The page reads like a generic admin scaffold awaiting a real creation flow, not a finished creation workspace. “健保署 AI,” Traditional Chinese copy, and the restrained government blue provide product signals, but the composition could be shipped unchanged by an unrelated dashboard product: generic rounded cards, thin gray borders, pale blue-gray surfaces, an all-caps eyebrow, and a standard disabled primary button. “Powered By FlySheet” is doing more branding work than the interface itself.

**Deterministic scan:** The Impeccable detector returned no findings for `frontend/app/page.tsx`, `frontend/components/workspace/WorkspaceViews.tsx`, or the combined app/workspace scope (including `type,layout`). That is useful evidence that the problem is not a simple static anti-pattern; it is primarily hierarchy, empty-state, copy, and product-specificity judgment. No false positives were reported.

**Visual overlays:** No overlay is available. Browser automation is not exposed in this session, so a live page, detector injection, and console overlay could not be used; the supplied screenshot and source inspection are the visual evidence.

## Overall Impression

The sidebar and two-column mental model are clear, but the main experience has the emotional arc of “I arrived at a form and found a dead end.” The single biggest opportunity is to make source acquisition the intentional first step: give it a compact, useful empty state with an obvious action, then let the settings become secondary until the page is ready to generate.

## What’s Working

- The sidebar makes the three destinations immediately understandable, and the active section has a strong dark-blue anchor.
- The source/configuration split is conceptually correct for a policy-document workflow.
- The implementation already has useful readiness, failure, retry, and ingestion states; the issue is how those states are surfaced, not a lack of state modeling.

## Priority Issues

### [P1] The empty source state is a dead end

**Why it matters:** In the screenshot, the source card occupies most of the page height because the grid stretches to match the settings card. With zero documents, the user gets a huge blank area, a dashed placeholder, and a low-affordance text link. A first-time policy staff member can reasonably assume the feature is broken.

**Fix:** Make the empty state compact and intentional. Add a primary “上傳來源文件” action and a secondary “前往知識庫選取” action. Explain accepted formats and why a source is required. Avoid equal-height stretching; once a source exists, replace onboarding copy with a selected-file list.

**Suggested command:** `$impeccable onboard` (then `$impeccable layout`)

### [P1] The primary action is prominent but functionally opaque

**Why it matters:** The washed-out “Generate Slides” button still looks like the page’s focal action, while the reason it is disabled appears as a small amber line below it. Users must scan away from the control to understand how to unlock it.

**Fix:** Put readiness beside the source section or directly above the CTA. Use explicit copy such as “選取至少一份可用來源後即可生成,” or a short checklist: “來源 0/1 · 格式 0/1 · 標題已填寫.” Keep the button state-aware and make the next action obvious.

**Suggested command:** `$impeccable clarify`

### [P1] The language system feels accidental

**Why it matters:** “Creation studio,” “Presentation Title,” “Number of Slides,” “Tone,” “Formal,” “Casual,” “Guidance,” “Generate Slides,” and “Generate Again” sit beside Traditional Chinese headings. This makes the product feel unfinished and increases interpretation cost for the intended local audience.

**Fix:** Commit to Traditional Chinese throughout: “簡報工作區,” “簡報標題,” “投影片張數,” “語氣,” “正式 / 輕鬆,” “補充指引,” “生成簡報,” and “再次生成.” Keep English only if it is an explicit brand decision.

**Suggested command:** `$impeccable adapt`

### [P2] Too much empty chrome, too little intentional hierarchy

**Why it matters:** The desktop header is almost entirely blank; the eyebrow adds noise without context; both panels receive the same rounded-card treatment. Nothing tells the eye what matters first when generation is impossible.

**Fix:** Remove the blank header or use it for breadcrumb/context/status. Remove the eyebrow unless it names a real product concept. Reduce equal-weight containers and let the source-selection state drive the page composition.

**Suggested command:** `$impeccable distill` (then `$impeccable layout`)

### [P2] Status copy duplicates facts without answering “what now?”

**Why it matters:** “可用 0 份” and “0 份文件已選取 · 0 份符合簡報來源格式” repeat counts but do not explain the path forward. “符合簡報來源格式” is technical and ambiguous.

**Fix:** Consolidate into one plain-language block: “尚未選取來源文件。請上傳文件，或從知識庫選取 PDF、DOCX、Markdown 或 TXT。” Make the availability indicator a real status, not a chip that looks interactive.

**Suggested command:** `$impeccable clarify`

## Persona Red Flags

- **Jordan (first-timer / policy staff):** The empty source state has no inline upload CTA, the disabled button looks like a failure, and mixed English labels make the workflow feel unfinished. High abandonment risk before the first generation.
- **Alex (frequent analyst):** Selecting sources requires leaving Slides for Knowledge Base, then returning. There is no inline search/filter or quick source picker, so repeated generation costs unnecessary navigation and context switching.
- **Mei (review/compliance user):** “符合簡報來源格式” does not explain provenance, readiness, or why a document is excluded. The flow needs a clear eligibility explanation before she can trust the resulting deck.

## Minor Observations

- The green sidebar status dot can imply the system is healthy while the current task is blocked.
- “可用 0 份” looks like a filter chip but is not interactive.
- Secondary text is consistently pale, weakening scan hierarchy.
- The native number-input spinner feels visually disconnected from the otherwise custom controls.
- The tone buttons should expose selected state semantically with `aria-pressed`.
- The completed state still contains the English “Generate Again.”
- Source selection is rendered as a small text action instead of a meaningful step in the workflow.

## Questions to Consider

1. Is Slides intended to be a standalone creation workspace, or merely the final step after Knowledge Base selection?
2. When there are zero documents, should the first action be upload, or should the user be sent to Knowledge Base?
3. Is the mixed English/Traditional Chinese voice deliberate? If not, should the interface fully commit to Traditional Chinese?
4. Should the source panel be the dominant part of this page, given that generation cannot happen without a valid source?
