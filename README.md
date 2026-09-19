# NHI-AI

NHI-AI is an internal, document-grounded AI workspace for the National Health Insurance Administration. The current product supports category-scoped Q&A, document upload/cataloguing/indexing, and PPTX generation from selected documents. The frontend is a modular Next.js workspace built from shadcn/Base UI primitives, and PPTX generation is the first registered workflow on a provider-neutral agent runtime backed by Codex.

News ingestion, news retrieval, news UI, and news generation are intentionally out of scope.

## Architecture

| Component          | Responsibility                                                                   | Docker endpoint         |
| ------------------ | -------------------------------------------------------------------------------- | ----------------------- |
| `frontend`         | Next.js chat, knowledge-base, slides, and gated agent-telemetry UI              | `http://localhost:3000` |
| `backend`          | FastAPI API, retrieval bootstrap, document catalog, grounded chat, and job APIs   | `http://localhost:8000` |
| `documents-worker` | Taskiq consumer for document ingestion, deletion, and provider cleanup           | none                    |
| `tasks-worker`     | Taskiq consumer for the generic `agents.run` entrypoint                          | none                    |
| `scheduler`        | Taskiq scheduler for delayed cleanup retries and reconciliation                    | none                    |
| `postgres`         | Document, ingestion, slide-job, and primary retrieval-index metadata               | `localhost:5432`        |
| `redis`            | Queues, live job progress, delayed schedules, and short-lived telemetry            | `localhost:6379`        |
| OpenAI             | Managed vector store, `file_search`, Responses API, and Codex execution           | external                |

PostgreSQL is the source of truth for catalog metadata, slide-job lifecycle state, and the application's primary vector-store ID. Uploaded files and generated PPTX artifacts are stored in the shared `slides-data` volume so the API and workers resolve the same UUID-scoped paths. Redis holds queue transport, live-progress overlays, delayed cleanup schedules, and optional expiring development telemetry; it is not the durable catalog.

### Request/data flow

1. The browser calls the Next.js app; `/api/v1/*` is rewritten to FastAPI.
2. A document upload writes metadata to PostgreSQL and one secure UUID directory to shared storage, then queues a Taskiq ingestion job in Redis.
3. `documents-worker` adopts or creates the application's news-free OpenAI vector store through the database-backed retrieval registry, uploads the source, and records opaque provider IDs in PostgreSQL. Re-index cleanup is a separate idempotent task with scheduled backoff and reconciliation.
4. Chat uses an explicit scope (`legislative_qa`, `public_opinion`, or `bei_can`) and OpenAI `file_search`; responses include citations or the insufficient-evidence fallback.
5. Agent jobs are first recorded durably in PostgreSQL, then enter the `tasks` stream as typed `AgentTaskPayload` values. `tasks-worker` resolves an explicitly registered workflow; today that workflow is `slides`, which resolves selected document IDs, generates and validates a PPTX, and publishes it under the shared output volume.

Redis uses separate streams: `documents` carries `documents.ingest`,
`documents.delete`, and `documents.cleanup`, while `tasks` carries `agents.run`.
Task results/progress use the `tasks:result` namespace and expire after one
hour; slide status and download availability do not depend on that retention.
Worker process and concurrency limits are configured by `DOCUMENTS_WORKER_*`
and `TASKS_WORKER_*` settings.

Uploads currently accept PDF, DOCX, Markdown (`.md`/`.markdown`), and TXT sources, with a 250 MiB per-file limit.

### Agent workflows

`agents.run` is the only Taskiq entrypoint for Codex-backed work. Its payload contains a job ID, an allowlisted workflow name, and typed workflow input; it never selects a Python module, shell command, skill path, or filesystem path from client data. The workflow registry resolves the name to an adapter, and that adapter declares the skills it needs from `backend/.agents/skills`.

Each job receives an isolated workspace. The coordinator runs server-defined `author`, deterministic `validator`, and read-only `reviewer` nodes. Every author attempt and reviewer activation gets an independent ephemeral provider session, so reviewers cannot mutate author state and revisions do not inherit hidden conversational context. Blocking deterministic or semantic findings create a fresh author attempt, bounded by `AGENT_MAX_REVIEW_ROUNDS` (default `3`). Publication happens only after both gates pass. This framework is generic, although `slides` is currently the only registered workflow.

## API surface

Application routes are prefixed with `/api/v1`.

| Route                              | Purpose                                                       |
| ---------------------------------- | ------------------------------------------------------------- |
| `GET /health/live`                 | Process liveness; does not require dependencies               |
| `GET /health`                      | PostgreSQL and Redis readiness                                |
| `GET /api/v1/retrieval/status`     | Vector-store bootstrap state and ready-document count         |
| `GET /api/v1/qa-modes`             | Supported retrieval scopes                                    |
| `POST /api/v1/chat`                | Non-streaming grounded answer                                 |
| `POST /api/v1/chat/stream`         | SSE answer stream with citations                              |
| `/api/v1/documents`                | Upload, list, update, download, async delete, ingestion status |
| `/api/v1/documents/folders`        | Folder CRUD                                                   |
| `/api/v1/slides/jobs`              | Queue, poll, and download slide jobs                          |
| `/api/v1/dev/agent-runs`           | Gated sanitized run snapshots and event timelines             |

Every retrieval request is filtered by scope and `is_news_source=false`. On first startup, the backend creates an empty news-free OpenAI vector store when `OPENAI_VECTOR_STORE_ID` is omitted, then persists the ID in PostgreSQL for the API and document workers to share. An explicitly configured ID is adopted and validated on first startup. After that, the persisted ID is authoritative; a different environment value produces a warning instead of silently switching corpora. The supplied API key must have access to the seeded store, and the store must contain only approved, non-news sources.

## Configuration

```bash
cp backend/.env.example backend/.env
```

Set at least these values in `backend/.env`:

```dotenv
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=replace-me
AGENT_DEFAULT_MODEL=gpt-5.6-luna
AGENT_AUTHOR_MODEL=gpt-5.6-luna
AGENT_REVIEWER_MODEL=gpt-5.6-sol
AGENT_DEFAULT_REASONING_EFFORT=high
AGENT_AUTHOR_REASONING_EFFORT=high
AGENT_REVIEWER_REASONING_EFFORT=high
OPENAI_CHAT_MODEL=gpt-5.6-luna
# Optional: seed an existing dedicated news-free store on first startup.
# OPENAI_VECTOR_STORE_ID=vs_news_free_index
OPENAI_VECTOR_STORE_NAME=NHI-AI Knowledge Base
OPENAI_VECTOR_STORE_BOOTSTRAP_TIMEOUT_SECONDS=10
DATABASE_URL=postgresql+psycopg://nhi_ai:nhi_ai_local@localhost:5432/nhi_ai
```

When supplied, `OPENAI_VECTOR_STORE_ID` must identify a dedicated news-free index in the OpenAI project visible to `OPENAI_API_KEY`; it is a first-run seed and the persisted database record becomes the runtime source of truth. Bootstrap failure does not fail process liveness: inspect `/api/v1/retrieval/status`, fix the credential/store issue, and let a later worker attempt retry provisioning. Never commit `backend/.env` or provider keys. The default database is SQLite (`sqlite:///./nhi_ai.db`) for a lightweight local run; Compose overrides it with PostgreSQL. The current bootstrap uses SQLModel `create_all`, so add versioned migrations before changing a long-lived production schema.

### Queue and worker tuning

The defaults intentionally isolate ingestion from longer-running agent work:

| Workload | Redis stream / task | Default worker processes | Default async-task limit per process | Settings |
| --- | --- | ---: | ---: | --- |
| Document ingestion and cleanup | `documents` / `documents.ingest`, `documents.cleanup` | 1 | 4 | `DOCUMENTS_WORKER_PROCESSES`, `DOCUMENTS_WORKER_MAX_ASYNC_TASKS` |
| Agent workflows | `tasks` / `agents.run` | 2 | 2 | `TASKS_WORKER_PROCESSES`, `TASKS_WORKER_MAX_ASYNC_TASKS` |

Queue names are configured with `DOCUMENTS_QUEUE_NAME` and `TASKS_QUEUE_NAME`. The scheduler delivers delayed cleanup retries and a periodic reconciliation pass. Worker settings are read at startup, so restart the affected worker after tuning them; there is no dynamic autoscaling.

## Run with Docker Compose

Configure secrets and start the complete stack:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
# Edit backend/.env and set OPENAI_API_KEY. An existing OPENAI_VECTOR_STORE_ID
# may be supplied as an optional first-run seed.
docker compose up --build
```

Open:

- UI: <http://localhost:3000>
- Swagger/OpenAPI: <http://localhost:8000/docs>
- Readiness: <http://localhost:8000/health>

The backend waits for healthy PostgreSQL and Redis. Keep both workers and the scheduler running for uploads, cleanup recovery, and agent jobs to finish.

### Edit the frontend with Docker

To run the full stack while developing the frontend, use the development Compose override from the repository root:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --watch
```

Open <http://localhost:3000>. Keep this command running while editing files in `frontend/`; Compose syncs saved source changes into the container and Next.js refreshes the page. Changes to frontend dependencies or the Dockerfile rebuild only the frontend development image. The backend and workers run as containers, as in the regular Compose setup. Stop with Ctrl+C. This development override uses a separate `nhi-ai-frontend:dev` image; the regular `docker compose up` command still uses the production frontend image.

The first run may build images that are missing. It requires the same `.env` and `backend/.env` setup described above. Docker Compose Watch is available in recent Docker Compose versions.

### Developer telemetry console

The unauthenticated agent telemetry endpoints and `/dev/agents` console are
disabled by default. For trusted local development only, set
`ENABLE_AGENT_DEV_ROUTES=true` in the root `.env` used by Compose, then restart
the stack. Compose binds the frontend and backend to `127.0.0.1` by default;
do not change `HOST_BIND_ADDRESS` to a public interface for this console.
Telemetry is sanitized before it reaches Redis, expires after
`AGENT_EVENT_RETENTION_SECONDS` (default one day), and never controls workflow
success or failure.

To inspect a remote development stack, keep its ports loopback-bound and use an
SSH tunnel instead:

```bash
ssh -L 3000:127.0.0.1:3000 -L 8000:127.0.0.1:8000 user@development-host
```

Then open <http://localhost:3000/dev/agents>. The console intentionally
contains only sanitized telemetry; prompts, provider responses, and secrets are
not exposed.

Useful operations:

```bash
docker compose ps
docker compose logs -f backend documents-worker tasks-worker
docker compose down
```

`docker compose down -v` also deletes the PostgreSQL and shared-file volumes. Use it only when intentionally discarding local data.

## Run locally

### Prerequisites

- Python 3.13 and [uv](https://docs.astral.sh/uv/);
- Node.js 22+, Corepack, and pnpm;
- PostgreSQL and Redis, either installed locally or started with `docker compose up -d postgres redis`.

When dependencies run in Docker but the application runs on the host, use `localhost` in `backend/.env`.

### Backend

Terminal 1:

```bash
cd backend
uv sync
npm ci --omit=dev --no-audit --no-fund
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API lifespan initializes the current SQLModel schema and performs a
best-effort retrieval-index validation. A document worker can also initialize
the schema and retry vector-store provisioning during a cold start.

The Node dependencies in `backend/package.json` support the current PPTX workflow. The task worker also needs the Codex SDK and the slide workflow's rendering, font, and OCR dependencies; the Docker image provides them.

### Worker

Terminal 2, from `backend/`:

```bash
uv run python -m app.worker documents
# In a second terminal:
uv run python -m app.worker tasks
```

The API and both workers must share the same `DATABASE_URL`, `REDIS_URL`,
`DOCUMENTS_ROOT`, `AGENT_JOBS_ROOT`, and `AGENT_OUTPUT_ROOT`. The old `SLIDES_*`
filesystem/runtime names remain accepted as deprecated aliases when canonical names
are absent.

### Frontend

Terminal 3:

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm typecheck
pnpm dev
```

The Next.js rewrite sends `/api/v1/*` to `BACKEND_URL` or `BACKEND_INTERNAL_URL` (in that order); local development defaults to `http://localhost:8000`. Docker supplies `http://backend:8000` at both build time and runtime because Next rewrites are compiled during `next build`. To set it explicitly for local development:

```bash
BACKEND_INTERNAL_URL=http://localhost:8000 pnpm dev
```

Copy `frontend/.env.example` to `frontend/.env.local` to configure the same
values for local development. Keep `ENABLE_AGENT_DEV_ROUTES=false` unless both
the frontend and backend are running on a trusted development network.

## Import the predecessor corpus

The migration is dry-run by default and excludes the two news-tagged predecessor PDFs. The current predecessor manifest yields 20 eligible sources (14 PDFs and 6 Markdown files); these become queued records and the document worker performs provider indexing afterward.

Preview the import:

```bash
cd backend
uv run python scripts/migrate_nhiqa.py /path/to/NHI-QA
```

For Docker, mount the predecessor repository and write into the Compose database/shared volume:

```bash
docker compose run --rm \
  -v /path/to/NHI-QA:/migration-source:ro \
  backend uv run python scripts/migrate_nhiqa.py /migration-source \
    --database-url postgresql+psycopg://nhi_ai:nhi_ai_local@postgres:5432/nhi_ai \
    --storage-root /data/documents --apply
```

After applying, monitor the worker until imported documents move from `queued`/`indexing` to `ready`.

## Repository layout

```text
backend/
  app/api/routes/          FastAPI route modules
  app/models/              SQLModel and API contracts
  app/services/chat/       file_search, response, citation adapters
  app/services/documents/  repository and secure file storage
  app/services/retrieval/  durable vector-store provisioning and validation
  app/services/agentic/    coordinator, runner allowlist, telemetry, review loop, skill staging
  app/services/slides/     registered PPTX workflow adapter and validation assets
  app/tasks/               document ingest/delete and generic `agents.run` consumers
  app/worker.py            settings-driven documents/tasks worker launcher
  .agents/skills/          versioned, allowlisted workflow skills
  scripts/                 Corpus migration utilities
  tests/                   Backend tests
frontend/
  app/(workspace)/         Shared shell with /chat, /knowledge, and /slides routes
  components/ui/           shadcn/base-nova primitives
  components/workspace/    Modular workflow views and session provider
  components/dev/          Sanitized agent telemetry dashboard
  lib/api/                 Domain API facades (documents, chat, slides, agents)
  lib/api.ts               Backward-compatible typed client barrel and SSE parser
docker-compose.yml          PostgreSQL, Redis, backend, split workers, frontend
```

## Verification

From the repository root:

```bash
cd backend && PYTHONDONTWRITEBYTECODE=1 uv run pytest -q
cd ../frontend && pnpm typecheck && pnpm test && pnpm build
cd .. && docker compose config --quiet
```

The backend suite covers retrieval bootstrap, document storage/repositories and provider cleanup recovery, grounded chat/citations/streaming, agent contracts/telemetry/review behavior, durable slide jobs, scheduler wiring, worker separation, and shared-volume resolution. The frontend suite covers the API facades, retrieval empty/error states, workspace hooks, slide-job activity, and the developer telemetry console. Authentication, authorization, artifact retention, and dead-letter operations remain follow-up work.
