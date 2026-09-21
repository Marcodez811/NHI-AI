# Remaining implementation plan — 2026-09-19

Handoff document. Written for coding agents who were **not** present for the work it
describes, so it carries its own context. Read "Context" and "Ground rules" before
touching anything; each work item below is self-contained after that.

## 2026-09-21 citation/no-notes implementation handoff

`9_20_citation_and_notes_plan.md` WI-0 through WI-8 is implemented on
`agents-sdk-migration` as separate work-item commits: `cd51c833`
(WI-1 and WI-6 atomically: no author-written notes, exact
`work/outline_mapping.json` sidecar and structural outline checks), `8edf1423`
(WI-0: knowledge-base `display_name` allowlist via `work/sources.json`),
`a7ebd30b` (WI-2: final evidence-derived `參考資料` slide), `9e327a7c` (WI-3:
numbered footers tied to references), `8548fe4f` (WI-4: native chart titles),
`0d5d0cd4` (WI-5: exact N content slides plus one references slide, no notes
parts, and legacy verifier parity), and `1a512d6f` (WI-7: visible citations are
reviewable author claims). `49d12553` hardens WI-2/WI-3 against malformed
reference entries, title spoofing, and internal-artifact leaks; `10e981dd`
completes the WI-8 skill-documentation sweep.

Important correction to the plan: PptxGenJS emits referenced empty notes parts
even without `slide.addNotes()`. The WI-5 commit therefore extends the existing
`clean.py` with safe, atomic PPTX cleanup and instructs the author to run it
after generation. No path grant was widened. The plan also omitted the legacy
`artifacts.verify_output` count gate; WI-5 migrated it to N+1 and no-notes
enforcement. Final backend result: **346 passed / 3 skipped** (requested
baseline: 307 passed / 3 skipped), a net gain of 39 passing tests. Most came
from this work's structural coverage; other pre-existing uncommitted tests
were also present during the run. This is not a live-model acceptance run. Visible text
alone cannot prove that a page/line locator is truthful, so WI-7 asks the
semantic reviewer to compare citations against frozen evidence. Two different
knowledge-base documents may also share the same `display_name`; the visible
citations cannot distinguish those identities without a product decision on
unique labels or an additional source-identity contract. No reviewer path grant
was widened to paper over that ambiguity.

Companion documents:

- [`agents-sdk-migration-plan.md`](./agents-sdk-migration-plan.md) — the migration's
  file-level plan and a per-stage record of what actually landed. **Authoritative for
  everything already done.**
- [`backend-agents-refactor-plan.md`](./backend-agents-refactor-plan.md) — the broader
  product architecture (conversations, 科別 taxonomy, output templates). Authoritative
  for product contracts. Anything here that touches durable records or approval should be
  checked against it.

---

## Context

### What the system does

`NHI-AI` generates source-grounded PowerPoint decks (and NHI news articles) from uploaded
documents, for Taiwan's National Health Insurance Administration. Slide text is
Traditional Chinese, so CJK font handling is a real constraint, not an afterthought.

The backend is FastAPI + SQLModel (PostgreSQL, SQLite in tests) + TaskIQ/Redis workers.
The frontend is Next.js + React.

### The slides pipeline

`POST /slides/jobs` → `SlideJobService` writes a durable `slide_jobs` row → TaskIQ
`agents.run` (`backend/app/tasks/agents.py`) → `execute_workflow` → a state machine in
`backend/app/services/agentic/service.py`:

```
PREPARING → EXTRACTING → [PLANNING → AWAITING_OUTLINE] → DRAFTING → VALIDATING
          → REVIEWING → REVISING* → PUBLISHING
```

The bracketed phases are new and **off by default** (`agent_planner_enabled=False`).

Agents communicate through a **filesystem workspace**, not through message passing:
`input/`, `work/evidence.json` (the frozen EvidenceStore), `work/extracted/`,
`work/outline.json`, `work/rendered/final/`, `output/presentation.pptx`. Each stage gets
a narrow path grant — this is the core security property and must never be widened
casually. See `stage_hidden_paths` / `stage_read_only_paths` / `stage_writable_paths` in
`backend/app/services/slides/adapter.py`.

### What the migration is

The original execution layer was bespoke: `CodexRunner` wrapping `openai_codex`, with
bwrap process isolation and hand-rolled skill staging — roughly 3,800 lines under
`backend/app/services/agentic/`.

`openai-agents` (the OpenAI Agents SDK) provides equivalents natively: `agents.sandbox`
with `SandboxAgent`, `Filesystem`/`Shell`/`Skills` capabilities, `SandboxPathGrant`, plus
structured outputs, guardrails and sessions. The migration replaces the execution layer
while keeping the adapter/registry/phase/telemetry boundary intact.

The seam is the `AgentRunner` protocol (`agentic/contracts.py`). `RunnerRegistry` holds
both `"codex"` and `"agents"`; each node picks its runner by setting. **Every runner
setting currently defaults to `"codex"`, so none of this is live yet.**

### What already landed (all uncommitted, in the working tree)

| Stage | What |
| --- | --- |
| 0 | `agentic/sdk_runner.py` — `AgentsSdkRunner`, registry wiring, runner settings |
| 1 | extraction node selectable onto the SDK runner |
| 2 | reviewer node + `output_type=ReviewOutcome`; deleted `review_output_schema`, `parse_review`, `_parse_review` |
| 5a | `OutlineNode`/`SlideOutline`, `slide_outlines` table + migration `783d53229171`, `outline_repository.py` |
| 5b | planner node, worker pause/resume, three outline endpoints, `planner.py` |
| — | outline-to-deck validation cross-check; abandoned-outline TTL sweep |
| 5c | outline review UI, parked polling, revision-bound approval and stale-revision recovery |
| — | transactional approval outbox; exact speaker-note node matching; baseline test fixes |

Per-stage detail, including design changes forced by the real SDK API, is in
`agents-sdk-migration-plan.md`. **Read the "outcome" sections there before extending any
of it** — several assumptions in the original plan turned out wrong and are corrected
there, not here.

---

## Current state

### Working tree

Nothing is committed. `git status` shows:

```
 M backend/app/api/routes/slides.py          M backend/app/services/slides/adapter.py
 M backend/app/config.py                     M backend/app/services/slides/evidence.py
 M backend/app/main.py                       M backend/app/services/slides/repository.py
 M backend/app/models/slides.py              M backend/app/services/slides/runtime.py
 M backend/app/scheduler.py                  M backend/app/services/slides/validation.py
 M backend/app/services/agentic/contracts.py M backend/app/tasks/agents.py
 M backend/app/services/agentic/runner.py    D backend/app/services/agentic/test_new_agent.py
 M backend/app/services/agentic/service.py
?? backend/alembic/versions/783d53229171_add_slide_outlines.py
?? backend/app/services/agentic/sdk_runner.py
?? backend/app/services/slides/outline_repository.py
?? backend/app/services/slides/planner.py
?? backend/tests/... (6 new test modules)
```

### Test baseline

```
cd backend && uv run --group dev pytest tests/ -q
→ 306 passed, 3 skipped
```

WI-6 resolved the two former baseline failures, but **its second fix was wrong and has
been reverted**. The worker test now correctly supplies a `SecretStr`.

`test_safe_filename_unicode_sanitized` was made to pass by changing
`documents/storage.py`'s `_UNSAFE_FILENAME` from `re.UNICODE` back to `re.ASCII`. That
reverted the intent of commit `5baedbc2` ("fixed unicode filenames issues in the
backend"), which had deliberately widened the pattern from `[^A-Za-z0-9._ -]` so that
Traditional Chinese filenames survive. Under the ASCII flag, `報告書.pdf` becomes
`___.pdf` — in a product where source documents are routinely named in Chinese.

**The stale test was the defect, not the implementation.** `re.UNICODE` is restored, and
the test is replaced by two that assert the real contract: unicode word characters are
preserved, and directory components plus shell punctuation are still stripped (the
hardening comes from `Path(...).name`, not from restricting the alphabet). That is one
extra test, hence 306.

General lesson for anyone picking up a failing legacy test: establish whether the test or
the implementation encodes the intended behaviour — `git log -S` on the relevant line is
usually decisive — before changing either.

### New settings (`backend/app/config.py`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `agent_extraction_runner` | `"codex"` | runner for the extraction node |
| `agent_author_runner` | `"codex"` | runner for the author node |
| `agent_reviewer_runner` | `"codex"` | runner for the reviewer node |
| `agent_planner_runner` | `"codex"` | runner for the planner node |
| `agent_planner_enabled` | `False` | master switch for the whole planning phase |
| `agent_planner_model` | `None` | falls back to the default model |
| `agent_planner_reasoning_effort` | `None` | falls back to the default effort |
| `agent_awaiting_outline_ttl_seconds` | `604800` (7d) | abandoned-outline expiry |
| `agent_awaiting_outline_sweep_interval_seconds` | `3600` | sweep cadence |

### Knowledge graph

`graphify-out/` holds a current graph (4313 nodes, 9975 edges, refreshed after the work
above). Query it before grepping for fan-out questions:

```
graphify query "<question>" --budget 1200
graphify update .          # free, AST-only, after code changes
```

---

## Ground rules

Apply to every work item.

1. **Do not commit.** Leave changes in the working tree.
2. **Default behaviour must not change.** Every new capability is off by default. A
   developer who changes no settings must see today's behaviour exactly.
3. **House style**, non-negotiable: `from __future__ import annotations`, full type
   hints, module and class docstrings that explain *why* rather than *what*, comments
   only where a reader would otherwise be puzzled. Read the file you are editing and copy
   its voice. `agentic/sdk_runner.py` and `services/slides/adapter.py` are good models.
4. **Names** are descriptive and unabbreviated, reusing existing vocabulary:
   job / revision / outline / node / grant / workspace / stage / audit / sweep / resume.
5. **No dead code, no TODOs, no speculative fields.** Do not write tests that assert
   something does not exist — they can only fail spuriously.
6. **Never widen a path grant** to make something work. If a stage needs a file it cannot
   see, that is a design question, not a permissions fix. Stop and say so.
7. **No network, no API key** in test runs. Verify structurally with fakes. If a task's
   real acceptance needs a live model, say so plainly rather than implying parity you did
   not demonstrate.
8. **Report honestly.** If you could not do part of a task, say which part and why. A
   partial result described accurately is far more useful than a confident overstatement.

---

## Work items

Ordered by recommended execution. Dependencies noted per item.

---

### WI-1 — Make outline-node matching exact

**Size:** small. **Depends on:** nothing. **Do this first.**

**Problem.** The Stage 5b validation cross-check decides an outline node is "represented"
in the deck by searching for its **heading text**, case-insensitively, as a substring of
any text shape on any slide (`_outline_cross_check` in
`backend/app/services/slides/validation.py`).

That was the only option available, because `runtime._outline_instructions`
(`backend/app/services/slides/runtime.py`) tells the author to honour node order and
emphasis but never asks it to emit a node identifier. The check is brittle in both
directions: a legitimately reworded heading fails, and a heading echoed in body text
passes falsely.

**Task.**

1. Extend `_outline_instructions` to require the author to write each node's `id` into
   the **speaker notes** of every slide belonging to that node. Speaker notes are the
   right carrier: invisible to the audience, already part of the deck snapshot, and
   already used for citation detail.
2. Tighten `_outline_cross_check` to match on that exact id, not on heading substrings.
3. Keep a fallback path only if you can justify it. A clean exact-match check that fails
   loudly is better than one that silently degrades to fuzzy matching.
4. Confirm `build_deck_snapshot` actually captures speaker-notes text. **If it does not,
   that is the real first task** — say so and extend it.

**Acceptance.** A deck whose notes carry every node id passes. A deck missing one node's
id produces exactly one `outline_node_missing` finding naming that id. A job with no
`work/outline.json` is completely unaffected. Existing `test_validation.py` cases still pass.

**Why first:** everything else in the planning feature assumes this check is trustworthy.

**Outcome (landed 2026-09-20).** The author prompt requires each node id as a standalone
speaker-notes line on every slide belonging to that node. The existing OOXML snapshot
already extracted note paragraphs, so validation now performs exact note-line matching
without a fuzzy fallback. Missing ids produce one `outline_node_missing` finding per
missing node; jobs without `work/outline.json` remain unchanged.

---

### WI-2 — Stage 5c: outline review frontend

**Size:** large (est. 350–450k agent tokens). **Depends on:** WI-1 ideally, backend only strictly.

The backend API is complete and exercisable with `curl` today. This item is the UI.

**Surface** (confirmed against the current tree):

| File | Role |
| --- | --- |
| `frontend/lib/api/slides.ts` | slides API client + types |
| `frontend/lib/hooks/agent-job-state.ts` | `phaseForJob()`, `appendPhase()`, sessionStorage phase history |
| `frontend/lib/hooks/useAgentJob.ts` | polling hook (`useAgentJob()` at :86) |
| `frontend/lib/hooks/useSlideJob.ts` | slides-specific job hook |
| `frontend/components/workspace/SlidesView.tsx` | the slides panel (`SlidesView()` at :37) |
| `frontend/components/workspace/SlideGenerationStatus.tsx` | in-progress display |
| `frontend/components/workspace/SlideSettings.tsx` | the pre-job brief form |
| `frontend/components/workspace/WorkspaceContent.tsx` | view routing |
| `frontend/tests/` | `SlidesView.test.tsx`, `useSlideJob.test.tsx`, `slideReadiness.test.ts`, `api.test.ts` |

**Backend endpoints to bind** (all under `/slides/jobs/{job_id}`):

- `GET /outline` → latest revision (outline body + revision number + approval state).
- `POST /outline/messages` → **SSE**. Streams the planner's reply, persists revision
  *n+1*. Event vocabulary matches `routes/chat.py` — reuse the existing client-side SSE
  parsing in `frontend/lib/api/chat.ts` rather than writing a second parser.
- `POST /outline/approve` → body `{ expected_revision: number }`. Returns the job.
  **409** means the revision went stale (a newer one exists) — the UI must refetch and
  tell the user their view was out of date, not silently retry.

Read the request/response models in `backend/app/models/slides.py` and the route
signatures in `backend/app/api/routes/slides.py`. Do not infer the wire shape from this
document.

**Task.**

1. Add `"planning"` and `"awaiting_outline"` to the `AgentJobPhase` union in
   `frontend/lib/api/slides.ts`, and teach `phaseForJob()` about
   `status === "awaiting_input"`.
2. `useAgentJob` / `useSlideJob`: on `awaiting_outline`, **stop polling** and surface the
   outline. A parked job can sit for days; polling it is waste.
3. New `frontend/components/workspace/OutlineReview.tsx`, shown between `SlideSettings`
   and `SlideGenerationStatus`. It needs: the node list with headings, intent, key points
   and an **emphasis control** (`light` / `normal` / `deep`) per node; the narrative; the
   revision number; a chat panel posting to `/outline/messages`; and an Approve button
   bound to the **displayed** revision.
4. Approve must send the revision the user actually looked at. On 409, refetch and
   explain — never auto-approve the newer revision on the user's behalf.
5. Tests mirroring the existing `frontend/tests/` patterns: phase transition into and out
   of `awaiting_outline`, polling stops while parked, approve sends the displayed
   revision, 409 surfaces rather than retrying.

**Acceptance.** `npm test` (or the project's configured runner — check `package.json`)
passes. A job can be taken from parked → chat revision → approve → authoring entirely
through the UI against a running backend.

**Note:** `analyzeSlideReadiness` (`slide-readiness.ts`) governs the *pre-job* form and
should need no change. If you find yourself editing it, re-read the task.

**Outcome (landed 2026-09-20).** Added the outline API/types, shared SSE transport,
planning phases, parked polling behavior, `OutlineReview`, post-approval polling resume,
and revision-bound stale-approval handling. `npm run typecheck` and all 66 frontend tests
pass. The production build remains unverified in this environment: Turbopack cannot bind
its internal port, and the webpack fallback fails in Next's TypeScript `--showConfig`
parser before application compilation.

---

### WI-3 — Live parity verification (HUMAN, not an agent task)

**Size:** small but requires an API key and spend. **Blocks:** WI-4, WI-5.

Stages 1 and 2 are **structurally complete but behaviourally unverified**. No agent has
run them against a real model. Before anything is flipped in a real environment, and
before the author node is touched:

1. Set `agent_extraction_runner=agents`, run a real slides job, and diff the resulting
   `work/evidence.json` against a `codex` run over the same corpus. Semantic equivalence
   is the bar, not byte equality.
2. Set `agent_reviewer_runner=agents` and confirm the reviewer returns a well-formed
   `ReviewOutcome` and that the publish/retry/reject policy behaves as before.
3. Separately, enable `agent_planner_enabled=True` on a test job and inspect the produced
   `SlideOutline` — field bounds in `models/slides.py` were chosen before any real
   planner output existed and may need adjusting.

Record the outcome in `agents-sdk-migration-plan.md`.

---

### WI-4 — Stage 3: migrate the author node

**Size:** large. **Depends on:** WI-3. **Highest risk in the project.**

The author is the hardest node because it does real work in a sandbox: python-pptx
authoring, chart PNG generation via a bundled script, LibreOffice rendering, CJK
fontconfig (`FONTCONFIG_FILE`, `PPTX_CJK_FONT` — see `runtime.build_job_environment`),
and offline package installation (`PIP_NO_INDEX`, `UV_OFFLINE`, `npm_config_offline`).

Read Stage 3 in `agents-sdk-migration-plan.md` first.

**Guidance.** Prefer the `docker` sandbox backend
(`agents/sandbox/sandboxes/docker.py`) over `unix_local`; the repo already ships
`docker-compose.yml` and it replaces bwrap cleanly. Verify against
`tests/services/slides/test_render_slides.py` and `test_chart_deck_integration.py`.

**Critical isolation note.** `AgentsSdkRunner` honours `restrict_workspace=True` by
pointing the sandbox manifest root at a virtual path with nothing mounted, so a path is
hidden by *never being granted*. This is an allowlist and is stronger than masking — but
it means **an adapter that omits `stage_isolation = True` silently loses stage isolation
on this runner.** Both current adapters set it. If you add one, set it.

**Acceptance.** A real deck renders correctly, with correct CJK fonts and charts, under
`agent_author_runner=agents`, and the existing render tests pass. If parity cannot be
reached, **stop and report** — the author staying on `"codex"` is a supported
configuration, not a failure.

---

### WI-5 — Stage 4: remove the Codex runtime

**Size:** large. **Depends on:** WI-4 succeeding. **Riskier than originally planned.**

Once the author runs on the SDK, delete: the legacy `elif build_review is not None:`
branch in `service.py`, `_uses_execution_request`, `_as_execution_result`, the
`modern_runner` detection, the bwrap helpers in `runner.py`, `CodexRunner`,
`CodexAgentRunner`, `run_codex` in `slides/runtime.py`, and `openai-codex` from
`backend/pyproject.toml`. `tests/services/agentic/test_process_isolation.py` is deleted
here, not migrated — it tests `build_bwrap_launch_args` directly.

**Read this before starting.** A knowledge-graph pass after the Stage 0–5b work shows
`CodexRunner` is now the **4th most connected node in the entire repository (57 edges)**,
and `_execute_workflow()` is still top-ten at 48. Stage 2 deliberately *added* typed-output
support to `CodexAgentRunner` (~44 lines) so that `parse_review` could be deleted cleanly
— useful then, more to unpick now. The Codex runtime is more entangled than when the
migration began, so **this deletion is a bigger change than `agents-sdk-migration-plan.md`
implies.** Budget accordingly and expect to work incrementally.

`ProgressReporter`, `safe_error` and `_turn_phase` currently live in `runner.py` and are
imported by `sdk_runner.py`. They must **move to a shared module**, not be deleted.

---

### WI-6 — Fix the two pre-existing test failures

**Size:** small. **Depends on:** nothing. Good filler work.

1. `tests/tasks/test_agents.py::test_worker_builds_allowlisted_codex_runner_with_default_model`
   — `CodexRunner.api_key` is a `SecretStr`; the test compares it against a plain `str`.
   Several call sites construct `CodexRunner(api_key="key")` the same way. The equivalent
   bug in `tests/services/agentic/test_framework.py` was already fixed by wrapping in
   `SecretStr(...)`; mirror that.
2. `tests/services/documents/test_storage_hardening.py::test_safe_filename_unicode_sanitized`
   — unrelated to the migration; investigate on its own terms. Note a recent commit
   (`5baedbc2`) touched Unicode filename handling, so check whether the test or the
   implementation is the one that is wrong before changing either.

If WI-6 lands, update the baseline in this document from "299 + 2" to the new numbers.

**Outcome (landed 2026-09-20).** The `SecretStr` failure was fixed correctly. The
`test_safe_filename_unicode_sanitized` failure was **not**: it was made to pass by
reverting `documents/storage.py` to `re.ASCII`, which undid commit `5baedbc2` and would
have turned every Traditional Chinese filename into underscores. That change has been
reverted and the stale test replaced — see "Test baseline" above for the full account.
The backend baseline is **306 passed / 3 skipped**, not 305.

---

### WI-7 — Close the approval crash window

**Size:** medium. **Depends on:** nothing, but lower priority than WI-1/WI-2.

`POST /outline/approve` spans three systems: the SQL approval commit, writing
`work/outline.json`, and enqueueing `agents.run`. These cannot be one atomic unit. The
SQL commit is the gate, so a crash between commit and enqueue leaves a job **approved but
never resumed** — invisible to the user, and no sweep recovers it.

The fix is a transactional outbox: record the intended enqueue in the same transaction as
the approval, and have a dispatcher deliver it to TaskIQ with at-least-once semantics
(worker claiming is already idempotent). `backend-agents-refactor-plan.md` already calls
for an outbox for exactly this reason — **implement the one it describes rather than
inventing a second mechanism.**

**Outcome (landed 2026-09-20).** Migration `f46c2a84e1d3` adds a unique approval outbox
row keyed by `(job_id, revision)`. Approval and enqueue intent commit together. Immediate
delivery and a 60-second recovery task materialize the exact approved outline, publish
`agents.run`, and only then mark the event delivered; duplicate publication is absorbed
by the existing durable worker claim. Queue outage and crash-before-delivery-mark cases
are covered by tests.

---

### WI-8 — Agent dashboard: reflect the current architecture

**Size:** small. **Depends on:** nothing. **Useful before manual testing, not after.**

`frontend/components/dev/agent/` renders the agent run dashboard (`RunList.tsx`,
`RunDetail.tsx`, `NodeCard.tsx`, `Timeline.tsx`, `format.ts`), fed by
`useAgentDevRuns.ts` and `backend/app/api/routes/dev_agents.py`. It predates the planner
node and the runner seam, and no longer describes what the system does.

The backend emits five node ids — `extraction`, `planning`, `author`, `validator`,
`reviewer` (`service.py`, `planner.py:108`). Confirmed gaps:

1. **`RunDetail.tsx:37`** — `const lifecycleOrder = ["author", "validator", "reviewer"]`.
   Missing `planning` (new) and `extraction` (pre-existing). Nodes outside this list do
   not take their proper position in the lifecycle view.
2. **`format.ts:54-60`** — `nodeLabel()` maps only author / review / valid to Chinese
   labels; `extraction` and `planning` fall through to the raw English `node_id`, so they
   render inconsistently beside 作者 Agent / 審查 Agent / 驗證器.
3. **Runner is never shown.** Lifecycle events already carry `runner` (`"codex"` or
   `"agents"`), plus `model` and `reasoning_effort`. Per-node runner selection is the
   central fact of the migration, and WI-3's parity testing is exactly the task of
   comparing one runner against another — the dashboard should surface which runner each
   node actually used.
4. **A parked run has no distinct state.** `awaiting_outline` renders as a raw phase
   string, so a run deliberately waiting on human approval looks the same as one that
   hung. It needs its own visual treatment and, ideally, a link into the outline review.

**Task.** Extend `lifecycleOrder` and `nodeLabel` to cover all five nodes; surface
`runner` (and `model` / `reasoning_effort`) on `NodeCard`; give `awaiting_outline` a
distinct, clearly-not-failed presentation. Mirror the existing test patterns in
`frontend/tests/RunDetail.test.tsx` and `agentAttempts.test.ts`.

**Acceptance.** A run with planning enabled shows all five nodes in lifecycle order with
consistent labels; each node states its runner; a parked run reads as waiting, not stalled.

**Outcome (landed 2026-09-20).** `RunDetail.tsx` now orders `extraction`, `planning`,
`author`, `validator`, and `reviewer`, while preserving unknown nodes afterward in their
original order. `format.ts` labels the new model-backed nodes `擷取 Agent` and
`規劃 Agent`. The `awaiting_outline` phase renders as a neutral waiting badge reading
`等待人工核准大綱`, distinct from blue running and red failed states. Contrary to the
task's initial finding, `NodeCard.tsx` already rendered runner, model, and reasoning
effort through `MetaValue` with `—` fallbacks, so it needed no production change;
regression coverage now locks that behavior down. Changes are confined to
`RunDetail.tsx`, `format.ts`, `RunDetail.test.tsx`, and `agentAttempts.test.ts`. The full
frontend suite passes with 72 tests across 17 files (six tests above the documented
baseline), and `npm run typecheck` passes.

---

## Sequencing

```
WI-1 (exact node matching) ──┐ complete
WI-6 (pre-existing tests) ───┼ complete
WI-7 (approval outbox) ──────┘ complete

WI-2 (frontend) ─────────────> complete (live browser/backend exercise still recommended)
WI-8 (agent dashboard) ──────> complete

WI-3 (live parity, HUMAN) ───> blocks everything below
        │
        └──> WI-4 (author node) ──> WI-5 (delete Codex runtime)
```

**Do not start WI-4 before WI-3.** Migrating the hardest node while the two easiest are
behaviourally unproven means a failure there is ambiguous — SDK parity problem, or
something already broken upstream?

---

## Decisions already made — do not relitigate

These were settled with reasoning; reopen only with new evidence.

| Decision | Reasoning |
| --- | --- |
| Planner reads `work/evidence.json` via a read-only sandbox grant | Evidence is already frozen, consolidated and local. `file_search` would add an indexing dependency and break the guarantee that planner and author see byte-identical evidence. |
| Outline chat uses a persisted `Session`, not serialised `RunState` | `RunState` exists for resuming mid-tool-call interruptions. An outline discussion open for days needs a session plus immutable revisions. |
| `(job_id, expected_revision)` is the approval retry key | A separate `idempotency_key` field was removed: it was required on the wire but decorative, implying a guarantee the tuple already provides via `SELECT ... FOR UPDATE`. |
| Planning is gated on `agent_planner_enabled`, not just an adapter hook | A human pause is product-visible in a way `extraction_skills` is not; it needs its own off switch. |
| Outline revisions are immutable and append-only | No update method exists on the repository. Revision *N* cannot be approved once *N+1* exists — that is the 409. |
| An expired parked job becomes `FAILED` | There is no third "abandoned" terminal state. Open to a product opinion, but do not invent a new status without one. |
| `scripts/reconcile.py` is **not** the lease-sweep guard | It reconciles vector-store attachments only. The guard is `SlideJobRepository.claim` refusing `AWAITING_INPUT` without `allow_resume_from_awaiting_input=True`. An earlier version of the plan got this wrong. |

---

## Hazards

1. **Path grants are a security boundary.** Widening one to make a stage work is almost
   always the wrong fix. The author must not see original source documents; the reviewer
   must not see review history.
2. **`restrict_workspace` is load-bearing** on the SDK runner — see WI-4.
3. **CJK fonts break silently.** A deck can render with tofu boxes and still pass
   structural validation. Inspect rendered PNGs, not just the XML.
4. **The evidence store is frozen for a reason.** Nothing after extraction may modify
   `work/evidence.json` or `work/extracted/`; author revisions must never cause a source
   document to be reinterpreted.
5. **`validation.py` is 1401 lines.** Read the region you need (`validate_candidate_deck`,
   `ValidationFinding`, `ValidationResult`, `ValidationStatus`), not the whole file.
6. **Test-count drift** is the cheapest regression signal available. 299 passed / 3
   skipped / 2 known failures. If your number differs, explain why before handing back.
