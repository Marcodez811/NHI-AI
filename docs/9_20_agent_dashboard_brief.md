# Agent dashboard refresh — implementation brief

Task brief for a coding agent. Self-contained: everything needed is below, including the
file locations and line numbers, so **no codebase exploration is required.**

This is WI-8 of [`9_19_implementation_plan.md`](./9_19_implementation_plan.md).

---

## Context

`NHI-AI` runs multi-agent workflows that generate source-grounded PowerPoint decks. A
developer-facing dashboard shows how those agent runs are progressing, backed by a Redis
telemetry store.

The dashboard was written when a workflow had three nodes: author → validator → reviewer.
The pipeline has since gained a **source extraction** node and, most recently, a
**planning** node that pauses the run for human outline approval. Nodes can also now run
on either of two agent runtimes (`"codex"` or the OpenAI Agents SDK, `"agents"`), chosen
per node by configuration.

The dashboard reflects none of this.

**This is a presentation-only task.** The backend already emits everything needed and the
client type already declares it — the fields are simply never rendered. Do not change any
backend file, API route, or TypeScript interface.

---

## The five node ids the backend emits

`extraction`, `planning`, `author`, `validator`, `reviewer`.

(Sources, for reference only — you do not need to open these:
`backend/app/services/agentic/service.py`, `backend/app/services/slides/planner.py:108`.)

---

## Files in scope

Derived from the dependency graph; this is the complete set.

| File | Role |
| --- | --- |
| `frontend/components/dev/agent/RunDetail.tsx` | lifecycle ordering, run-level metadata |
| `frontend/components/dev/agent/format.ts` | `nodeLabel()`, status/event label helpers |
| `frontend/components/dev/agent/NodeCard.tsx` | renders one node |
| `frontend/tests/RunDetail.test.tsx` | existing test patterns to mirror |
| `frontend/tests/agentAttempts.test.ts` | existing test patterns to mirror |

Do **not** modify `frontend/lib/api.ts`, `frontend/lib/api/agents.ts`,
`frontend/lib/hooks/useAgentDevRuns.ts`, `attempts.ts`, or anything under `backend/`.

---

## The data is already there

`AgentNodeSnapshot` (`frontend/lib/api.ts:216-233`) already declares:

```ts
node_id: string;
agent_role: string | null;
runner: string | null;            // "codex" | "agents"
model: string | null;
reasoning_effort: string | null;
status: AgentNodeStatus;
attempt: number | null;
duration_ms: number | null;
message: string | null;
// ...
```

The backend populates all of these (`AgentNodeSnapshot` in
`backend/app/services/agentic/events.py`). They are reaching the browser today and being
dropped on the floor. **No type change is needed.**

---

## Changes

### 1. Lifecycle ordering — `RunDetail.tsx:37`

```ts
const lifecycleOrder = ["author", "validator", "reviewer"];
```

Missing `extraction` (a pre-existing gap) and `planning` (new). Nodes outside this list
do not take their proper position in the lifecycle view.

Replace with the true pipeline order:

```ts
const lifecycleOrder = ["extraction", "planning", "author", "validator", "reviewer"];
```

Keep whatever fallback behaviour already handles an unrecognised `node_id` — a future
node must not vanish from the view.

### 2. Node labels — `format.ts:54-60`

```ts
export function nodeLabel(node: Pick<AgentNodeSnapshot, "node_id">): string {
    const key = node.node_id.toLowerCase();
    if (key.includes("author")) return "作者 Agent";
    if (key.includes("review")) return "審查 Agent";
    if (key.includes("valid")) return "驗證器";
    return node.node_id;
}
```

`extraction` and `planning` fall through to the raw English `node_id`, rendering
inconsistently beside the Chinese labels.

Add labels for both, matching the existing naming convention (`… Agent` for model-backed
nodes; the validator is a deterministic step and is labelled differently). Suggested:
`extraction` → `擷取 Agent`, `planning` → `規劃 Agent`. Confirm the wording reads
naturally alongside the existing three; the UI is Traditional Chinese.

### 3. Show the runner on each node — `NodeCard.tsx`

Each node card currently shows label, status, attempt and elapsed time. Add the
**runner**, and alongside it `model` and `reasoning_effort`.

This is the most valuable part of the task. Per-node runner selection is the central fact
of the current architecture migration, and the near-term engineering task is comparing one
runner's output against the other's. A dashboard that cannot say which runtime produced a
node's output is not useful for that.

Use the existing `MetaValue` component (`frontend/components/dev/agent/MetaValue.tsx`) so
presentation stays consistent with `RunDetail.tsx`'s metadata rows. Handle `null`
gracefully — the codebase's established placeholder for a missing value is `"—"`.

### 4. A parked run must not look stalled — `RunDetail.tsx:130`

```tsx
<MetaValue label="Phase" value={snapshot.phase || "—"} />
```

A run in phase `awaiting_outline` is **deliberately waiting for a human** to approve a
generated outline. Its worker has exited and released its lease; it may sit there for
days. Rendered as a bare phase string it is indistinguishable from a run that hung.

Give `awaiting_outline` a distinct, clearly-not-failed presentation — wording that says it
is waiting on human input, and visually distinct from both `running` and `failed`. Follow
the existing status-colour vocabulary in `StatusIcon.tsx` / `format.ts` rather than
introducing a new colour system.

The adjacent phase `planning` is an ordinary running phase and needs no special treatment
beyond its label.

---

## Ground rules

1. **Do not commit.** Leave changes in the working tree.
2. **Presentation only.** No backend files, no API routes, no TypeScript interface
   changes. If you believe a change outside `frontend/components/dev/agent/` is required,
   stop and explain why instead of making it.
3. **Match house style.** Read the file you are editing and copy its voice. Comments
   explain *why*, not *what*. The UI language is Traditional Chinese; developer-facing
   identifiers stay English.
4. **No dead code, no TODOs.** Do not write tests that assert something does not exist.
5. Mirror the existing test patterns in `frontend/tests/RunDetail.test.tsx` and
   `agentAttempts.test.ts`. Do not invent a new test harness.

---

## Verification

```bash
cd frontend && npm test
```

Current baseline: **66 passed, 17 files.** Your additions should raise the count; nothing
should break. If your number differs for any other reason, explain it.

---

## Acceptance

- A run with planning enabled shows all five nodes, in pipeline order, with consistent
  Traditional Chinese labels.
- Each node card states which runner executed it, plus its model and reasoning effort,
  degrading to `—` when absent.
- A run parked at `awaiting_outline` reads as waiting on a human, and is visually
  distinct from a running or failed run.
- An unrecognised future `node_id` still appears rather than disappearing.

---

## Report back

Keep it under 300 words: what you changed, the Chinese labels you chose and why, how a
parked run is now presented, test output, and anything in this brief that proved wrong or
underspecified.
