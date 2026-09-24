# Brief: model settings page

**For:** Codex. **Date:** 2026-09-24. **Scope:** backend + frontend, one feature.

## Goal

Choose each agent stage's model from a page in the app instead of editing `backend/.env`.
Today every change means editing `.env` and running
`docker compose up -d --force-recreate backend tasks-worker documents-worker scheduler`.
After this change, a saved setting applies to the **next job**, with no restart.

## What exists today (read these first)

- `backend/app/config.py`: `Settings` fields `agent_default_model`,
  `agent_{author,reviewer,extraction,planner}_model`,
  `agent_default_reasoning_effort`, `agent_{author,reviewer,extraction,planner}_reasoning_effort`,
  `agent_{extraction,author,reviewer,planner}_runner`, `agent_planner_enabled`.
- `backend/app/services/slides/adapter.py` (about lines 111-200): `SlidesWorkflowAdapter`
  properties such as `author_model`, `planner_runner` and `reviewer_reasoning_effort` each return
  `settings.agent_<stage>_<x> or settings.agent_default_<x>`.
  `backend/app/services/news/adapter.py` has the same pattern for news.
- `backend/app/services/agentic/service.py`: reads those properties with `getattr(adapter, ...)`
  when each stage runs (for example lines ~462, ~584, ~677-680, ~1206) and puts them on
  `AgentExecutionRequest.model` / `.reasoning_effort`. Runners take the model from the
  request, so **the runner registry in `app/tasks/agents.py` does not need to change.**
- `backend/app/services/slides/planner.py`: the outline chat runs in the **web** process and
  builds its runner from `slides_adapter.planner_runner` / `planner_model`.
- The dashboard at `frontend/app/dev/agents` already shows each stage's runner, model and
  reasoning effort.

## Design

### 1. Storage: one small table, `.env` stays the fallback

- New SQLModel table `agent_stage_settings`: one row per stage (`extraction`, `planner`,
  `author`, `reviewer`), with columns `stage` (PK), `runner`, `model`, `reasoning_effort`
  (all nullable) and `updated_at`. Also store `planner_enabled`, either in a one-row table
  or as a nullable column on the `planner` row (your choice; keep it simple).
- Alembic migration. Follow the existing files in `backend/alembic/versions/`.
- **Resolution order for each value:** database row → `settings.agent_<stage>_<x>` →
  `settings.agent_default_<x>`. A null column means "use `.env`". A fresh install with an
  empty table must behave exactly like today.

### 2. Per-job snapshot

- Resolve all stages **once, when a job starts** (in the worker, before extraction), and
  write the result to `work/model_settings.json` in the job workspace.
- Every stage of that job, and the outline chat (`planner.py`), reads the snapshot, not the
  live table. A change on the page never affects a job that is already running or parked at
  `awaiting_outline`.
- Suggested shape: a small resolver (for example `app/services/agentic/model_settings.py`)
  that returns a frozen `StageModelSettings(runner, model, reasoning_effort)` per stage. The
  adapter properties then read the snapshot. Keep the `settings.agent_*` fields: they are
  the fallback.
- Workspace grants are explicit allowlists, so no agent can read this file. It holds no
  secrets anyway.

### 3. API

- `GET /agent-settings` returns, for each stage:
  - the stored values;
  - the **effective** value and where it came from (`database` / `env` / `default`);
  - which runners that stage allows (see rules below).

  It also returns which providers have an API key configured, as booleans:
  `{"openai": true, "gemini": true, "anthropic": false}`.
- `PUT /agent-settings/{stage}`: set or clear (null) values. Validate and return 422 with a
  Traditional Chinese message on any rule violation.
- Put it next to the existing routes in `backend/app/api/routes/`.

### 4. Validation rules (enforce on the server; the UI mirrors them)

| Stage | Allowed runners | Why |
| --- | --- | --- |
| extraction | `codex` | Needs shell and file access in a confined sandbox. |
| planner | `codex`, `agents` | Runs as a plain agent with evidence inlined; this is how Gemini works today. |
| author | `codex` | The Agents SDK local sandbox does not confine the shell on Linux. **Must not change.** |
| reviewer | `codex` | Needs read grants on renders; the SDK path has no safe sandbox for that yet. |

- Model name: reuse the `_safe_model_name` rule in `backend/app/services/agentic/events.py`
  (slash-separated identifier segments). On the `agents` runner, a `litellm/<provider>/...`
  model requires that provider's key to be configured. On the `codex` runner, reject
  `litellm/` models.
- Reasoning effort: must be a value of `AgentReasoningEffort`
  (`backend/app/services/agentic/contracts.py`).

### 5. Frontend page

- New route `frontend/app/(workspace)/settings/` (or `.../models/`), linked from the workspace
  navigation. All UI text is in Traditional Chinese.
- One card per stage with: a runner select (only allowed runners), a model text input with a
  few suggestions, a reasoning-effort select, the effective value with its source ("來自 .env"),
  and a "恢復預設" button that clears the row. Add a planner on/off toggle.
- Show which providers have a key configured (✓ / ✗). **Never show or accept a key.**
- Note under the save button: 「變更只套用於之後建立的工作。」
- Follow the existing styling and components in `frontend/app/(workspace)/slides`.

## Hard constraints

- **API keys never go in the database, the API response, the page or the snapshot.** They
  stay in `backend/.env` only.
- The author and extraction stay on `codex`. Do not add a way around this.
- No user-visible internal identifiers (hashes, ids) on the page.
- Do not change how runners are constructed in `app/tasks/agents.py`.
- Do not change path grants.

## Out of scope for v1

- Per-job overrides from the job-creation form.
- Settings history or audit log.
- Auth (testing only for now). Once there are real users, this page must be admin-only:
  add a `TODO(auth)` comment on the route.
- The chat assistant's model (`openai_model`).

## Tests

- Resolver: database → env → default order; a null column falls through.
- Snapshot: written at job start; changing the table after that does not change the running
  job's requests; the outline chat uses the snapshot.
- API: author/extraction/reviewer reject `agents`; a `litellm/gemini/...` model is rejected
  when no Gemini key is set; `codex` rejects `litellm/` models; responses contain no key
  material.
- Frontend: the page renders the effective values; only allowed runners are offered.
- Run `cd backend && uv run --group dev pytest tests/ -q`, and
  `cd frontend && npm test && npm run typecheck && npm run build`.

## When done

Report the files changed, the test results, and anything you had to decide that this brief
did not settle.
