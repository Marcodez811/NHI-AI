# NHI-AI backend

The backend is a FastAPI application with two Taskiq background pipelines:

- document ingestion into an OpenAI vector store for source-grounded Q&A;
- evidence-first PowerPoint generation from previously uploaded documents.

PostgreSQL stores document, folder, and ingestion metadata. Uploaded source files and generated PowerPoint files live on a shared filesystem volume. Redis Streams carries asynchronous tasks and stores short-lived task progress/results. News ingestion and news generation are intentionally outside the current scope.

The repository-level setup guide is in [../README.md](../README.md). This document describes the backend package as it exists now.

## Architecture at a glance

```mermaid
flowchart LR
    UI[Next.js frontend] -->|HTTP /api/v1| API[FastAPI API]
    API -->|catalog metadata| DB[(PostgreSQL)]
    API -->|source uploads and downloads| VOL[(Shared slides-data volume)]
    API -->|enqueue and poll| REDIS[(Redis Streams + result backend)]
    REDIS --> DW[documents-worker]
    REDIS --> TW[tasks-worker]
    DW --> DB
    DW --> VOL
    DW -->|document upload/index| VS[OpenAI vector store]
    TW --> VOL
    API -->|Responses API + file_search| VS
    TW -->|isolated agent turns| CODEX[OpenAI Codex SDK]
    CODEX -->|PPTX + evidence + QA artifacts| VOL
```

### Runtime responsibilities

| Component                   | Current responsibility                                                                                                                                              |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FastAPI process             | Validates requests, manages document/folder records, saves uploads, enqueues jobs, reports job state, serves downloads, and handles grounded chat requests.         |
| PostgreSQL                  | Stores `Document`, `Folder`, and `IngestionJob` rows. It stores metadata and opaque OpenAI IDs, not document binary content.                                        |
| Shared `slides-data` volume | Stores original documents, temporary agent workspaces, and published `.pptx` files. API and both workers mount the same volume.                                  |
| Redis                       | Provides separate `documents` and `tasks` Taskiq Redis Streams; task progress/results use `tasks:result` and expire after one hour. |
| Taskiq workers              | `documents-worker` handles `documents.ingest` (1 process/4 async tasks); `tasks-worker` handles the generic `agents.run` entrypoint (2 processes/2 async tasks). |
| OpenAI vector store         | Holds indexed copies of uploaded documents used by `file_search`. Every indexed source is tagged with its application document ID and non-news metadata.            |
| Codex slide runtime         | The registered `slides` adapter runs generation/correction in workspace-write mode, semantic review read-only, and publishes only after deterministic and semantic gates pass. |

## Data ownership

The system deliberately keeps different data in different stores:

```text
PostgreSQL
  document identity, filename, checksum, category, status, storage key,
  folder relationship, retrieval flags, and opaque provider IDs

/data/documents/<document UUID>/<safe filename>
  the original uploaded file bytes

Redis
  serialized Taskiq payloads, sanitized progress, and terminal task results

/data/jobs/<slide job UUID>/
  temporary slide inputs, skills, audit files, renders, and QA artifacts

/data/output/<slide job UUID>.pptx
  the published presentation
```

Only UUIDs cross the task queue boundary. Absolute source paths are resolved by workers from the shared volume and absolute artifact paths are never returned to the client.

## Complete backend file tree

The tree below includes the backend's source, tests, configuration, bundled agent skills, and runtime assets. Generated or sensitive paths are intentionally excluded: `.env`, `.venv/`, `node_modules/`, `graphify-out/`, `.pytest_cache/`, and `__pycache__/`.

```text
backend/
├── .env.example
├── .gitignore
├── .python-version
├── Dockerfile
├── README.md
├── package.json
├── package-lock.json
├── pyproject.toml
├── uv.lock
├── .agents/
│   └── skills/                         # backend-global allowlisted skills
│       ├── source-document-extraction/
│       └── pptx-nhi-tw/
├── app/
│   ├── __init__.py
│   ├── main.py                         # FastAPI assembly, lifecycle, DI, health routes
│   ├── config.py                       # Environment-backed settings
│   ├── db.py                           # SQLModel engine, tables, request sessions
│   ├── broker.py                       # Separate document/task brokers and result backend
│   ├── api/
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── chat.py                 # Grounded chat and SSE endpoints
│   │       ├── documents.py            # Document/folder CRUD and ingestion status
│   │       └── slides.py               # Slide create, poll, and download endpoints
│   ├── models/
│   │   ├── __init__.py
│   │   ├── chat.py                     # Chat request/response contracts
│   │   ├── documents.py                # SQLModel tables and document API contracts
│   │   ├── slides.py                   # Public and queue-level slide contracts
│   │   └── virtual_fs.py               # Virtual-filesystem contracts
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── documents.py                # documents.ingest Taskiq task
│   │   ├── slides.py                   # deprecated in-process compatibility helper
│   │   └── agents.py                   # stable agents.run worker import boundary
│   └── services/
│       ├── agentic/
│       │   ├── contracts.py             # generic task/turn/adapter contracts
│       │   ├── registry.py              # explicit workflow allowlist
│       │   ├── runner.py                # Codex turns, progress, heartbeat, audits
│       │   ├── service.py               # lifecycle and bounded review loop
│       │   └── staging.py               # declared-skill staging
│       ├── __init__.py
│       ├── chat/
│       │   ├── __init__.py
│       │   ├── citations.py            # Provider citation normalization
│       │   ├── responder.py            # OpenAI Responses API adapter
│       │   └── retrieval.py            # file_search tool and metadata filters
│       ├── documents/
│       │   ├── __init__.py
│       │   ├── repository.py           # Repository protocol, SQLModel and test implementations
│       │   └── storage.py              # Secure shared-volume upload storage
│       ├── virtual_fs/
│       │   ├── __init__.py
│       │   └── local.py                 # UUID-to-source-path worker resolver
│       └── slides/
│           ├── __init__.py
│           ├── agent.py                 # Top-level slide orchestration facade
│           ├── artifacts.py             # Workspaces, staging, preflight, validation, publish
│           ├── contracts.py             # JobError and progress callback contracts
│           ├── runtime.py               # Legacy prompt/runtime compatibility helpers
│           ├── adapter.py                # Registered slides WorkflowAdapter
│           └── fonts/
│           │   ├── NotoSansTC-Black.ttf
│           │   ├── NotoSansTC-Bold.ttf
│           │   ├── NotoSansTC-ExtraBold.ttf
│           │   ├── NotoSansTC-ExtraLight.ttf
│           │   ├── NotoSansTC-Light.ttf
│           │   ├── NotoSansTC-Medium.ttf
│           │   ├── NotoSansTC-Regular.ttf
│           │   ├── NotoSansTC-SemiBold.ttf
│           │   ├── NotoSansTC-Thin.ttf
│           │   ├── NotoSansTC-VariableFont_wght.ttf
│           │   ├── NotoSerifTC-Black.ttf
│           │   ├── NotoSerifTC-Bold.ttf
│           │   ├── NotoSerifTC-ExtraBold.ttf
│           │   ├── NotoSerifTC-ExtraLight.ttf
│           │   ├── NotoSerifTC-Light.ttf
│           │   ├── NotoSerifTC-Medium.ttf
│           │   ├── NotoSerifTC-Regular.ttf
│           │   ├── NotoSerifTC-SemiBold.ttf
│           │   └── NotoSerifTC-VariableFont_wght.ttf
│           └── (fonts and slide-specific helpers)
├── scripts/
│   └── migrate_nhiqa.py                # Legacy NHI-QA metadata migration utility
└── tests/
    ├── api/
    │   └── test_slides.py
    ├── infrastructure/
    │   └── test_workers.py
    ├── services/
    │   ├── agentic/
    │   │   └── test_framework.py
    │   ├── chat/
    │   │   ├── test_chat.py
    │   │   └── test_stream.py
    │   ├── documents/
    │   │   ├── test_repository.py
    │   │   └── test_storage.py
    │   ├── slides/
    │   │   └── test_main.py
    │   └── virtual_fs/
    │       └── test_local.py
    └── tasks/
        └── test_slides.py
```

## How the backend starts

`app.main` constructs the application and installs production dependency overrides:

1. The lifespan handler calls `SQLModel.metadata.create_all()` through `init_db()`.
2. The API process starts the Taskiq broker client. A Taskiq worker manages its own broker lifecycle.
3. Document and chat routes receive a request-scoped `SQLModelDocumentRepository` backed by PostgreSQL or the configured SQLite database.
4. The chat service receives the configured OpenAI model, vector-store ID, and a mapper from OpenAI file IDs back to local document UUIDs.
5. Chat, document, and slide routers are mounted under `/api/v1`.
6. Shutdown closes Redis and both API-owned producer broker connections, including
   partial-startup cleanup if the second broker cannot start.

`GET /health/live` proves that the process is alive. `GET /health` also checks Redis and the database and returns `503` if either dependency is unavailable.

The current schema bootstrap uses `create_all`, not a versioned migration framework. Existing-table migrations therefore require an explicit migration step or script.

## Current feature flows

### Document ingestion

1. `POST /api/v1/documents` validates the extension/MIME type and streams the upload to a temporary file in the shared document volume.
2. Storage enforces the size limit, computes a SHA-256 checksum, sanitizes the filename, and atomically moves the file to `<documents-root>/<document-id>/<filename>`.
3. The API creates the `Document` and `IngestionJob` rows and enqueues `documents.ingest` with opaque IDs and a category.
4. The worker resolves the local source, uploads it to OpenAI Files, and attaches it to the configured vector store with `document_id`, `qa_set`, `content_type`, and `is_news_source=false` attributes.
5. PostgreSQL is updated to `ready` with the opaque provider IDs. Failures mark both rows as `failed` with a safe error.

Supported upload extensions are currently `.pdf`, `.docx`, `.md`, `.markdown`, and `.txt`. Valid categories are `legislative_qa`, `public_opinion`, and `bei_can`; there is no news category.

### Source-grounded chat

1. `POST /api/v1/chat` or `/chat/stream` validates every explicitly selected document against PostgreSQL.
2. Selected documents must match the requested category, be `ready`, and have retrieval enabled.
3. `ResponseService` calls the OpenAI Responses API with a `file_search` tool constrained to the configured vector store, category, optional document IDs, and `is_news_source=false`.
4. Provider citations are normalized and remote file IDs are mapped back to local document UUIDs.
5. If the final answer has no source citation, the backend replaces it with `無法回答，因為無相關資料`. The streaming route buffers answer deltas until final citations are known, preventing ungrounded text from being emitted.

## Slides pipeline workflow

The slides API is asynchronous because extraction, generation, rendering, revision, and validation can take several minutes.

```mermaid
flowchart TD
    A[POST /api/v1/slides/jobs] --> B[Validate request]
    B --> C[Create UUID and queued progress in Redis]
    C --> D[Enqueue agents.run with UUIDs only]
    D --> E[Worker marks job running]
    E --> F[Resolve UUIDs from shared document volume]
    F --> G[Create isolated job workspace]
    G --> H[Preflight dependencies and CJK fonts]
    H --> I[Stage sources and required skills]
    I --> J[Build prompt and locked-down environment]
    J --> K[Run one streamed Codex SDK turn]
    K --> L[Extract sources and build evidence map]
    L --> M[Generate editable PPTX]
    M --> N[Render, review, revise, and produce QA reports]
    N --> O{All release validators pass?}
    O -->|No| P[Return sanitized failed result]
    O -->|Yes| Q[Publish job-id.pptx]
    Q --> R[Delete successful workspace]
    R --> S[Expose download URL]
```

### 1. API acceptance and queueing

`POST /api/v1/slides/jobs` accepts:

```json
{
    "title": "健保政策簡報",
    "document_ids": ["00000000-0000-0000-0000-000000000000"],
    "slides_count": 10,
    "guidance": "Emphasize the policy timeline and evidence.",
    "tone": "formal"
}
```

The API validates a nonblank title, 1–20 unique document UUIDs, an exact requested length of 5–25 slides, and a `formal` or `casual` tone. It creates a job UUID, writes `queued` progress, and enqueues an `AgentTaskPayload` with `workflow="slides"` and the typed slide input using that same UUID as the Taskiq task ID. The response is immediate:

```json
{
    "job_id": "<uuid>",
    "status": "queued"
}
```

The payload contains document UUIDs, never filesystem paths, provider credentials, or file bytes.

### 2. Worker-side source resolution

The generic `agents.run` task resolves the registered `slides` adapter. Its preparation hook changes the job to `running` and uses `SharedVolumeDocumentResolver` to resolve each ID as:

```text
<DOCUMENTS_ROOT>/<document UUID>/<exactly one regular source file>
```

The resolver rejects missing or multiple files, symlinks, path escapes, non-regular files, and unsupported extensions. Its accepted set is `.pdf`, `.docx`, `.txt`, `.md`, `.rtf`, `.csv`, and `.xlsx`. This does not exactly match the upload API: uploads accept `.markdown`, but the slide resolver does not; the resolver accepts `.rtf`, `.csv`, and `.xlsx`, but the upload API does not. Consequently, an uploaded `.markdown` document can be indexed for chat but currently fails slide source resolution.

### 3. Isolated workspace and preflight

`generate_slides()` creates this job-local structure:

```text
<AGENT_JOBS_ROOT>/<job UUID>/
├── .agents/skills/
│   ├── source-document-extraction/
│   └── pptx-nhi-tw/
├── input/                             # Copies of resolved sources
├── template/                          # Optional presentation template
├── output/
│   └── presentation.pptx              # Required candidate deck
└── work/
    ├── prompt.md
    ├── codex_result.json               # Job-local SDK audit
    ├── final_message.txt
    ├── progress.jsonl
    ├── extracted/
    │   └── manifest.json               # Required for PDF/DOCX
    ├── images/
    ├── rendered/final/*.png            # One final render per slide
    └── intermediate/
        ├── evidence_map.json
        ├── content_check.json
        ├── review_report.json
        └── qa_report.json
```

Before the model runs, preflight verifies:

- Python packages required for OOXML, images, and PDF extraction;
- Node modules `pptxgenjs`, `sharp`, `react`, `react-dom`, and `react-icons`;
- LibreOffice for rendering and visual validation;
- fontconfig plus a Traditional-Chinese/CJK-safe font;
- Tesseract with `chi_tra` and `eng` data when any source is a PDF.

The agent worker creates a job-specific fontconfig file, copies only the two required local skills, copies source files into `input/`, and writes the compact brief to `work/prompt.md`.

Filesystem traversal, skill/source staging, preflight subprocesses, deterministic
validators, publication, cleanup, and document-provider I/O run in worker threads
so the configured async-task concurrency is not serialized by blocking calls.

### 4. Codex generation and review loop

The generic `CodexRunner` starts one ephemeral Codex thread rooted at the job directory and reuses it for every generation, correction, and review turn. The runtime uses:

- the configured `OPENAI_MODEL`;
- workspace-write sandboxing for generation/correction, read-only sandboxing for semantic review, and denied approval prompts;
- `source-document-extraction` for PDF/DOCX sources;
- `pptx-nhi-tw` for NHI styling, evidence mapping, deck construction, rendering, revision, and QA;
- an offline dependency environment (`PIP_NO_INDEX`, `UV_OFFLINE`, and npm offline mode);
- a configurable timeout, currently 45 minutes by default;
- streamed safe progress updates and a 60-second heartbeat;
- a bounded `AGENT_MAX_REVIEW_ROUNDS` loop (default 3): deterministic validation runs before semantic review, blocking findings produce a correction turn, and publication is reached only after both gates pass.

The prompt explicitly treats `input/` as untrusted source data rather than instructions. For PDF/DOCX inputs, deterministic extraction runs first and produces block-level IDs/locators. The presentation skill maps slide claims to those source blocks, creates an editable native PowerPoint, renders every slide, reviews it, revises problems, and produces the required evidence and QA artifacts.

### 5. Release validation

The service does not publish a deck merely because a `.pptx` exists. Deterministic validation runs after every write-capable turn, and `verify_output()` requires all of the following before semantic review or publication:

1. `output/presentation.pptx` is a valid OOXML ZIP, is larger than 10 KB, and contains at least one slide.
2. `evidence_map.json`, `content_check.json`, `review_report.json`, and `qa_report.json` exist.
3. PDF/DOCX jobs include a valid extraction manifest and evidence map linked to extracted source blocks.
4. The review report matches the candidate PPTX and final render set.
5. The content checker reports no configured findings.
6. The QA report validates against the PPTX and evidence map.
7. The number of valid final PNG renders exactly equals the deck's slide count.

Any failed check prevents publication.

### 6. Publication, polling, and download

After validation, the candidate is moved to `<AGENT_OUTPUT_ROOT>/<job UUID>.pptx`. The worker returns only a relative artifact key and a sanitized filename, marks progress `completed`, and removes the successful workspace. A failed workspace is retained by default for server-side diagnosis, while clients receive only `Presentation generation failed.`

Clients poll `GET /api/v1/slides/jobs/{job_id}`. A completed response includes a download URL:

```json
{
    "job_id": "<uuid>",
    "status": "completed",
    "stage": "completed",
    "message": "Presentation is ready for download.",
    "download_url": "/api/v1/slides/jobs/<uuid>/download"
}
```

The download route accepts only a completed result, resolves the relative key under the output root, and rejects absolute paths, `..`, path escapes, symlinks, and missing files. Redis progress/results expire after one hour; the published PPTX remains until separately cleaned up.

Slide job states are:

```text
queued -> running -> completed
                  \-> failed
```

There is currently no automatic retry for slide generation. The task catches internal exceptions and stores a sanitized terminal failure so SDK errors, paths, tracebacks, and credentials do not cross the public API boundary.

## API surface

All application routes are under `/api/v1` except health endpoints.

| Method   | Route                                       | Purpose                                    |
| -------- | ------------------------------------------- | ------------------------------------------ |
| `GET`    | `/health/live`                              | Process liveness.                          |
| `GET`    | `/health`                                   | Redis and database readiness.              |
| `GET`    | `/api/v1/qa-modes`                          | List supported Q&A categories.             |
| `POST`   | `/api/v1/chat`                              | Return one grounded answer.                |
| `POST`   | `/api/v1/chat/stream`                       | Stream a grounded answer as SSE.           |
| `POST`   | `/api/v1/documents`                         | Upload a document and enqueue ingestion.   |
| `GET`    | `/api/v1/documents`                         | Filter and list documents.                 |
| `POST`   | `/api/v1/documents/folders`                 | Create a folder.                           |
| `GET`    | `/api/v1/documents/folders`                 | List folders.                              |
| `PATCH`  | `/api/v1/documents/folders/{folder_id}`     | Rename a folder.                           |
| `DELETE` | `/api/v1/documents/folders/{folder_id}`     | Delete an empty folder.                    |
| `GET`    | `/api/v1/documents/{document_id}`           | Read document metadata.                    |
| `PATCH`  | `/api/v1/documents/{document_id}`           | Update document metadata.                  |
| `DELETE` | `/api/v1/documents/{document_id}`           | Mark deleting and remove the local source. |
| `GET`    | `/api/v1/documents/{document_id}/download`  | Download the original source.              |
| `GET`    | `/api/v1/documents/{document_id}/ingestion` | Read the latest ingestion job.             |
| `POST`   | `/api/v1/slides/jobs`                       | Queue a presentation job.                  |
| `GET`    | `/api/v1/slides/jobs/{job_id}`              | Poll progress or terminal status.          |
| `GET`    | `/api/v1/slides/jobs/{job_id}/download`     | Download a completed presentation.         |

FastAPI's interactive OpenAPI UI is available at `http://localhost:8000/docs` while the API is running.

## Configuration

Copy the example file before starting locally or through Compose:

```bash
cp backend/.env.example backend/.env
```

| Variable                           | Purpose                                                             | Current default                               |
| ---------------------------------- | ------------------------------------------------------------------- | --------------------------------------------- |
| `REDIS_URL`                        | Task queue, progress, and result Redis connection.                  | Required                                      |
| `OPENAI_API_KEY`                   | OpenAI Files/vector store, Responses API, and Codex SDK credential. | Required                                      |
| `OPENAI_MODEL`                     | Slide-generation Codex model.                                       | `gpt-5.6-luna`                                |
| `OPENAI_CHAT_MODEL`                | Grounded chat model.                                                | `gpt-5.6-luna`                                |
| `OPENAI_VECTOR_STORE_ID`           | Shared non-news retrieval index.                                    | Optional setting, required for ingestion/chat |
| `DATABASE_URL`                     | SQLModel database connection.                                       | `sqlite:///./nhi_ai.db`                       |
| `DOCUMENTS_ROOT`                   | Original shared document storage.                                   | `/tmp/documents`                              |
| `AGENT_JOBS_ROOT`                  | Temporary agent job workspaces.                                     | `/tmp/agents/jobs`                             |
| `AGENT_OUTPUT_ROOT`                | Published agent artifacts (including PPTX).                         | `/tmp/agents/output`                           |
| `AGENT_TIMEOUT_MINUTES`            | Maximum Codex turn duration.                                        | `45`                                          |
| `AGENT_KEEP_WORKSPACE_ON_FAILURE`  | Retain failed workspaces for diagnosis.                             | `true`                                        |
| `AGENT_MAX_REVIEW_ROUNDS`          | Maximum same-thread semantic review/correction rounds.               | `3`                                           |
| `DOCUMENTS_QUEUE_NAME`             | Redis Stream for document ingestion.                                | `documents`                                   |
| `TASKS_QUEUE_NAME`                 | Redis Stream for agent jobs.                                         | `tasks`                                       |
| `DOCUMENTS_WORKER_PROCESSES`       | Document worker process count.                                      | `1`                                           |
| `DOCUMENTS_WORKER_MAX_ASYNC_TASKS` | Document async task limit per process.                              | `4`                                           |
| `TASKS_WORKER_PROCESSES`           | Agent worker process count.                                          | `2`                                           |
| `TASKS_WORKER_MAX_ASYNC_TASKS`     | Agent async task limit per process.                                  | `2`                                           |
| `SLIDES_*`                         | Deprecated aliases; canonical names win when both are set.          | See `.env.example`                            |
| `MAX_UPLOAD_BYTES`                 | Maximum document upload size.                                       | `262144000` (250 MiB)                         |

Worker process and async-task limits are read when each worker starts; change
the settings and restart the affected worker to apply them. There is no dynamic
autoscaling.

Do not commit `.env`; it contains the API key.

## Run with Docker Compose

From the repository root:

```bash
docker compose up --build
```

This starts PostgreSQL, Redis, FastAPI, separate document and agent Taskiq workers, and the frontend. Compose overrides the shared paths to `/data/jobs`, `/data/documents`, and `/data/output`, all backed by the `slides-data` volume shared by `backend` and both workers.

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health
docker compose logs -f backend documents-worker tasks-worker
```

## Run locally

The slide worker also requires Node.js, LibreOffice Impress, fontconfig/CJK fonts, and Tesseract with Traditional Chinese and English data. The Docker image installs these; for a native run they must already be available on the host.

From `backend/`:

```bash
cp .env.example .env
uv sync
npm ci
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

In another terminal:

```bash
uv run python -m app.worker documents
# In another terminal:
uv run python -m app.worker tasks
```

For host-native Redis, set `REDIS_URL=redis://localhost:6379/0`. Keep the API and worker on the same database and filesystem roots; otherwise the worker cannot resolve uploads or publish files that the API can serve.

## Tests

From `backend/`:

```bash
uv run pytest
```

The suite covers slide API contracts, safe job progress/results, worker resolution/failure handling, slide orchestration, chat grounding/streaming, document repository/storage behavior, and shared-volume path safety.

## Elaboration, Ideas going onward

The idea is that, instead of using the Codex SDK solely for slide generation, the Codex SDK should eventually act as the brain of the entire application, providing multiple services, similar to NotebookLM.

For instance, it should be able to provide slides-generation, news-generation, etc. as separate services.

Under the hood, these services would consist of multiple instances built around the Codex SDK, each using different skills and scripts to support the required functionality, generalizing the architecture.
