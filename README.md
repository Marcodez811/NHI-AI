# NHI-AI

NHI-AI is an internal, document-grounded AI workspace for the National Health Insurance Administration. The current product supports category-scoped Q&A, document upload/cataloguing/indexing, and PPTX generation from selected indexed documents. PPTX generation is the first registered workflow on a general Codex-based agent runtime, rather than a special-purpose queue pipeline.

News ingestion, news retrieval, news UI, and news generation are intentionally out of scope.

## Architecture

| Component  | Responsibility                                               | Docker endpoint         |
| ---------- | ------------------------------------------------------------ | ----------------------- |
| `frontend` | Next.js chat, knowledge-base, and slides UI                  | `http://localhost:3000` |
| `backend`  | FastAPI API, document catalog, chat adapter, and agent-job API | `http://localhost:8000` |
| `documents-worker` | Taskiq consumer for document ingestion                     | none                    |
| `tasks-worker`     | Taskiq consumer for the generic `agents.run` entrypoint    | none                    |
| `postgres` | Document, folder, and ingestion metadata                     | `localhost:5432`        |
| `redis`    | Queue, progress, and short-lived job results                 | `localhost:6379`        |
| OpenAI     | Managed vector store, `file_search`, and response generation | external                |

PostgreSQL is the source of truth. Uploaded files and generated PPTX artifacts are stored in the shared `slides-data` volume so the API and worker resolve the same UUID-scoped paths. Redis is transport/progress state only.

### Request/data flow

1. The browser calls the Next.js app; `/api/v1/*` is rewritten to FastAPI.
2. A document upload writes metadata to PostgreSQL and one secure UUID directory to shared storage, then queues a Taskiq ingestion job in Redis.
3. `documents-worker` uploads the source to the configured news-free OpenAI vector store and records opaque provider IDs in PostgreSQL.
4. Chat uses an explicit scope (`legislative_qa`, `public_opinion`, or `bei_can`) and OpenAI `file_search`; responses include citations or the insufficient-evidence fallback.
5. Agent jobs enter the `tasks` stream as typed `AgentTaskPayload` values. `tasks-worker` resolves an explicitly registered workflow; today that workflow is `slides`, which resolves selected document IDs, generates and validates a PPTX, and publishes it under the shared output volume.

Redis uses separate streams: `documents` carries `documents.ingest`, while `tasks`
carries `agents.run`. Terminal task results/progress use the `tasks:result` namespace
and expire after one hour. Worker process and concurrency limits are configured by
`DOCUMENTS_WORKER_*` and `TASKS_WORKER_*` settings.

Uploads currently accept PDF, DOCX, Markdown (`.md`/`.markdown`), and TXT sources, with a 250 MiB per-file limit.

### Agent workflows

`agents.run` is the only Taskiq entrypoint for Codex-backed work. Its payload contains a job ID, an allowlisted workflow name, and typed workflow input; it never selects a Python module, shell command, skill path, or filesystem path from client data. The workflow registry resolves the name to an adapter, and that adapter declares the skills it needs from `backend/.agents/skills`.

Each job receives an isolated workspace and one ephemeral Codex thread. Write-capable generation and correction turns are followed by deterministic validation; a valid candidate then receives a read-only semantic review. Blocking findings trigger a correction turn on the same thread, bounded by `AGENT_MAX_REVIEW_ROUNDS` (default `3`). Publication happens only after both gates pass. This framework is generic, although `slides` is currently the only registered workflow.

## API surface

Application routes are prefixed with `/api/v1`.

| Route                       | Purpose                                                  |
| --------------------------- | -------------------------------------------------------- |
| `GET /health/live`          | Process liveness; does not require dependencies          |
| `GET /health`               | PostgreSQL and Redis readiness                           |
| `GET /api/v1/qa-modes`      | Supported retrieval scopes                               |
| `POST /api/v1/chat`         | Non-streaming grounded answer                            |
| `POST /api/v1/chat/stream`  | SSE answer stream with citations                         |
| `/api/v1/documents`         | Upload, list, update, download, delete, ingestion status |
| `/api/v1/documents/folders` | Folder CRUD                                              |
| `/api/v1/slides/jobs`       | Queue, poll, and download slide jobs                     |

Every retrieval request is filtered by scope and `is_news_source=false`. The configured vector store must contain only approved, non-news sources.

## Configuration

```bash
cp backend/.env.example backend/.env
```

Set at least these values in `backend/.env`:

```dotenv
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=replace-me
OPENAI_MODEL=gpt-5.6-luna
OPENAI_CHAT_MODEL=gpt-5.6-luna
OPENAI_VECTOR_STORE_ID=vs_news_free_index
DATABASE_URL=postgresql+psycopg://nhi_ai:nhi_ai_local@localhost:5432/nhi_ai
```

`OPENAI_VECTOR_STORE_ID` must identify a dedicated news-free index. Never commit `backend/.env` or provider keys. The default database is SQLite (`sqlite:///./nhi_ai.db`) for a lightweight local run; Compose overrides it with PostgreSQL. The current bootstrap uses SQLModel `create_all`, so add versioned migrations before changing a long-lived production schema.

### Queue and worker tuning

The defaults intentionally isolate ingestion from longer-running agent work:

| Workload | Redis stream / task | Default worker processes | Default async-task limit per process | Settings |
| --- | --- | ---: | ---: | --- |
| Document ingestion | `documents` / `documents.ingest` | 1 | 4 | `DOCUMENTS_WORKER_PROCESSES`, `DOCUMENTS_WORKER_MAX_ASYNC_TASKS` |
| Agent workflows | `tasks` / `agents.run` | 2 | 2 | `TASKS_WORKER_PROCESSES`, `TASKS_WORKER_MAX_ASYNC_TASKS` |

Queue names are configured with `DOCUMENTS_QUEUE_NAME` and `TASKS_QUEUE_NAME`. Worker settings are read at startup, so restart the affected worker after tuning them; there is no dynamic autoscaling.

## Run with Docker Compose

Configure secrets and start the complete stack:

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and set OPENAI_API_KEY and OPENAI_VECTOR_STORE_ID.
docker compose up --build
```

Open:

- UI: <http://localhost:3000>
- Swagger/OpenAPI: <http://localhost:8000/docs>
- Readiness: <http://localhost:8000/health>

The backend waits for healthy PostgreSQL and Redis. Keep both workers running for uploads and agent jobs to finish.

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
uv run python -c "from app.db import init_db; init_db()"
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

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
  app/services/agentic/    generic workflow registry, runner, review loop, skill staging
  app/services/slides/     registered PPTX workflow adapter and validation assets
  app/tasks/               `documents.ingest` and generic `agents.run` consumers
  app/worker.py            settings-driven documents/tasks worker launcher
  .agents/skills/          versioned, allowlisted workflow skills
  scripts/                 Corpus migration utilities
  tests/                   Backend tests
frontend/
  app/page.tsx             Chat, knowledge-base, slide workspace
  lib/api.ts               Typed /api/v1 client and SSE parser
docker-compose.yml          PostgreSQL, Redis, backend, split workers, frontend
IMPLEMENTATION_PLAN.md      Architecture comparison and rollout decisions
```

## Verification

From the repository root:

```bash
cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
cd ../frontend && pnpm typecheck
cd .. && docker compose config --quiet
```

The backend suite covers document storage/repositories, grounded chat/citations/streaming, generic agent workflow contracts and review-loop behavior, slide jobs, worker separation, and shared-volume resolution. Authentication, authorization, remote vector-store cleanup, retry/dead-letter handling, and versioned database migrations remain follow-up work.
