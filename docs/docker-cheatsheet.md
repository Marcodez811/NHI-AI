# Docker cheat sheet

Everything runs from the repo root (`~/NHI-AI`). Services: `postgres`, `redis`,
`migrate` (one-off), `backend`, `tasks-worker`, `documents-worker`, `scheduler`,
`frontend`.

`backend`, `migrate`, `tasks-worker`, `documents-worker` and `scheduler` all run the
**same image** (`nhi-ai-backend:local`), so building `backend` rebuilds all of them.

---

## Start and stop

| I want to… | Run |
| --- | --- |
| start everything (normal) | `docker compose up -d` |
| start with frontend hot reload | `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --watch` |
| see what's running | `docker compose ps` |
| stop everything, **keep data** | `docker compose down` |
| stop just the job worker | `docker compose stop tasks-worker` |

> ⚠️ **Never** `docker compose down -v` unless you mean it: `-v` **deletes the Postgres
> database and every uploaded document**.

`--watch` only live-syncs the **frontend**. Backend changes always need a rebuild.

---

## After changing things

| I changed… | Run |
| --- | --- |
| backend code (`backend/…`) | `docker compose build backend && docker compose up -d` |
| frontend code, not using `--watch` | `docker compose build frontend && docker compose up -d frontend` |
| both | `docker compose build backend frontend && docker compose up -d` |
| `backend/.env` | `docker compose up -d --force-recreate backend tasks-worker documents-worker scheduler` |
| backend dependencies (`pyproject.toml`) | same as backend code: rebuild |

> ⚠️ `docker compose restart` does **not** re-read `.env`. It restarts the same container
> with its old settings. After editing `.env`, use `up -d --force-recreate`.

Migrations run automatically: the `migrate` service runs `alembic upgrade head` on every
`up`.

---

## Watching a job

| I want to… | Run |
| --- | --- |
| follow the job worker live | `docker compose logs -f tasks-worker` |
| only the last hour | `docker compose logs --since 1h tasks-worker` |
| find one job in the logs | `docker compose logs tasks-worker 2>&1 \| grep <job-id>` |
| backend API logs | `docker compose logs -f backend` |

A job's files live in `/data/jobs/<job-id>/` inside the containers. Failed jobs keep
theirs; **successful jobs are cleaned up**.

| What | Where (inside the container) |
| --- | --- |
| validator findings | `/data/jobs/<job-id>/work/intermediate/content_check.json` → `validator_findings` |
| reviewer findings | `/data/jobs/<job-id>/work/intermediate/semantic_review_history.json` |
| per-attempt audits and token usage | `/data/jobs/<job-id>/work/agents/<node>/attempt-N.json` |
| the generated deck | `/data/jobs/<job-id>/output/presentation.pptx` |

Read a file: `docker compose exec tasks-worker cat <path>`

Copy one out: `docker compose cp tasks-worker:<path> ./somewhere.pptx`

Read the volume **while the stack is stopped**, without starting anything:

```
docker compose run --rm --no-deps -T --entrypoint sh tasks-worker -c 'ls /data/jobs'
```

---

## Model settings (`backend/.env`)

Planner on Gemini, the current setup:

```
AGENT_PLANNER_ENABLED=true
AGENT_PLANNER_RUNNER=agents
AGENT_PLANNER_MODEL=litellm/gemini/gemini-3.8-flash
GEMINI_API_KEY=...
```

Back to OpenAI/Codex for the planner: set `AGENT_PLANNER_RUNNER=codex`, or delete that
line.

Then recreate the backend containers (see "After changing things").

Test the planner **without** running the expensive author: start a job, wait for the
outline, and **don't approve it**. It parks and nothing else runs.

---

## Tests (no Docker needed)

```
cd backend  && uv run --group dev pytest tests/ -q
cd frontend && npm test && npm run typecheck && npm run build
```

`npm run build` matters: it catches production-build failures that tests and typecheck
miss.

---

## Housekeeping

| Message or situation | Fix |
| --- | --- |
| `Found orphan containers (nhi-ai-worker-1)` | `docker compose up -d --remove-orphans` |
| a job stuck at 執行中 for days | its worker died; it won't recover by itself |
| disk filling up with images | `docker image prune` (removes unused images only) |
