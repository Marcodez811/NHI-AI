# Slides job page and job list — implementation brief

Task brief for a coding agent. Self-contained: file locations, current behaviour and the
traps are all below, so broad codebase exploration should not be needed.

Ground rules from [`9_19_implementation_plan.md`](./9_19_implementation_plan.md) apply
unchanged. Commit per logical piece on the current branch with descriptive messages.

---

## Context

`NHI-AI` generates source-grounded PowerPoint decks for Taiwan's National Health Insurance
Administration. A slides job runs extraction, then planning, then **parks** at
`awaiting_outline` until a human approves the generated outline, then authors and publishes
the deck. A parked job can wait up to **7 days** before a sweep expires it
(`agent_awaiting_outline_ttl_seconds`).

Today there is no durable way back to a job. That is the problem this task solves.

---

## Decisions already made

| Decision | Detail |
| --- | --- |
| **One page per job, for its whole lifecycle** | `/slides/:jobId` shows progress while extracting and planning, the outline review while parked, progress again while authoring, then the download. The URL never changes for a job. |
| **Starting a job navigates to its page** | After the create call returns a `job_id`, route to `/slides/:jobId`. |
| **`/slides` lists recent jobs** | The index page keeps the new-presentation form and adds a list of recent jobs with their status, each linking to its job page. |
| **The URL is the source of truth** | The job page reads its id from the route, not from browser storage. See "Single-slot storage" below. |

---

## Current behaviour — read before changing anything

### Single-slot storage is the root cause

`frontend/lib/hooks/useSlideJob.ts` wraps the generic `useAgentJob` with
`storageKey: "nhi-ai:active-slide-job-id"`. The workspace tracks **one** active slide job,
persisted in **`sessionStorage`** (see `frontend/lib/hooks/agent-job-state.ts`).
`sessionStorage` is per-tab and dies with the tab, so a user who closes the tab loses the
only pointer to a job that may be parked for days. Starting a second job overwrites the
first.

With the URL as the source of truth, the job page must not also depend on this key.
**Do not keep two authorities for "which job am I looking at".** Decide whether the key
still serves the index page (for example, resuming an in-flight form) and remove it if it
does not; state your decision in the report.

### The routing trap

`frontend/components/workspace/WorkspaceRoute.tsx`:

```ts
const routeToView = {
  "/chat": "chat",
  "/knowledge": "files",
  "/slides": "slides",
  "/news": "news",
  "/workflows": "workflows",
} as const;
...
const view = routeToView[pathname as keyof typeof routeToView] ?? "chat";
```

This is an **exact-match** lookup that falls back to `"chat"`. A new
`app/(workspace)/slides/[jobId]/page.tsx` rendering `<WorkspaceRoute />` would therefore
show **the chat view**. `/slides/:jobId` must resolve to the slides view. Fix the matching
deliberately; do not special-case one path by string concatenation.

Every page under `app/(workspace)/` currently renders `<WorkspaceRoute />`, and state lives
in a shared shell (`WorkspaceProvider.tsx`, `WorkspaceContent.tsx`). Follow that pattern
unless it genuinely cannot express a per-job page; if it cannot, explain why before
departing from it.

### Where the pieces are today

| File | Role |
| --- | --- |
| `frontend/app/(workspace)/slides/page.tsx` | renders `<WorkspaceRoute />` |
| `frontend/components/workspace/WorkspaceRoute.tsx` | pathname → view |
| `frontend/components/workspace/WorkspaceContent.tsx` | renders `SlidesView`, passes `workspace.slideJob.*` |
| `frontend/components/workspace/SlidesView.tsx` | the brief form, status, and the outline panel (`<OutlineReview>` at ~`:439`, shown when `status === "awaiting_input" && phase === "awaiting_outline"`) |
| `frontend/components/workspace/OutlineReview.tsx` | fetch, chat and approve for one outline; takes `jobId` and `onApproved` |
| `frontend/lib/hooks/useSlideJob.ts`, `useAgentJob.ts`, `agent-job-state.ts` | job polling; polling stops while parked and resumes after approval |
| `frontend/lib/api/slides.ts` | slides API barrel — import through it, not `lib/api` directly |

`OutlineReview` already works end to end and was verified against a live backend. **Reuse
it; do not rewrite it.**

---

## Part 1 — Backend: list recent jobs

There is no list endpoint today. `backend/app/api/routes/slides.py` has create, get-one,
download, and the three outline routes.

1. `SlideJobRepository` (`backend/app/services/slides/repository.py`) is a Protocol with an
   `InMemorySlideJobRepository` and a `SQLModelSlideJobRepository` subclass. Add a method
   returning recent jobs newest-first with a bounded `limit`, to the Protocol and both
   implementations.
2. Add `GET /slides/jobs` returning a list of job summaries — enough for a list row: id,
   title, status, phase, created/started/finished times. Reuse the existing response
   vocabulary in `backend/app/models/slides.py` where it fits; do not add fields the list
   does not display. Bound the limit server-side.
3. Tests mirroring `backend/tests/services/slides/test_repository.py` and the existing
   route tests.

The list returns every job; there is no per-user scoping, and none is wanted for now.

---

## Part 2 — Frontend

1. **Route.** Add `app/(workspace)/slides/[jobId]/page.tsx` and make `/slides/:jobId`
   resolve to the slides view (see the routing trap).
2. **Navigate on start.** When job creation succeeds, route to `/slides/:jobId`.
3. **Job page.** Reads the id from the route. Polls the job. Renders, by lifecycle:
   progress while extracting/planning → `OutlineReview` while parked → progress while
   authoring → download when complete → a clear failure state when failed.
   Unknown or deleted job ids render a not-found state, not a crash or an infinite spinner.
4. **Index page.** `/slides` keeps the new-presentation form and adds the job list. A job
   awaiting outline approval should be visually obvious in the list — it is the one state
   that needs the user.
5. **Refresh and deep link.** Reloading `/slides/:jobId`, or opening it in a new tab, must
   land on the correct state for that job.
6. Tests mirroring `frontend/tests/SlidesView.test.tsx`, `useSlideJob.test.tsx`,
   `WorkspaceRoute.test.tsx` and `OutlineReview.test.tsx`. Cover at least: the routing trap
   (a job URL resolves to the slides view, not chat), navigation on start, the job page in
   each lifecycle state, and the not-found state.

---

## Out of scope

- The developer dashboard at `/dev/agents` (known issues there: elapsed time keeps climbing
  on a parked run, and the run-level badge still says 執行中). Separate task.
- Redesigning the brief form (`SlideSettings.tsx`) or `OutlineReview`.
- Authentication or per-user job ownership.
- Any backend change beyond the list endpoint.

---

## Verification

```bash
cd backend && uv run --group dev pytest tests/ -q     # baseline 351 passed / 3 skipped
cd frontend && npm test && npm run typecheck          # baseline 72 passed / 17 files
```

Both counts should rise by your new tests with nothing failing. Explain any other drift.

Structural tests will not show whether the pages feel right. After the suites pass, start
the stack and walk one job through start → park → approve → download, including a refresh
while parked and reopening the job from the list.

---

## Report back

Under 300 words: the routing fix and why that approach; what happened to the
`sessionStorage` key; the list endpoint's shape; test output; whether you did the manual
walk-through and what it showed; anything in this brief that proved wrong.
