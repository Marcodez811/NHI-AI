# Spec: config.yaml and the settings page

**Date:** 2026-10-06.

**Hard rules** (unchanged): Traditional Chinese UI, no internal ids shown to users, API keys never
leave the backend.

**Decisions taken:**
- One global `backend/config.yaml`.
- The settings page saves **overrides to the database**; it never writes the file.
- Settings are split into **user** and **dev**. Dev settings are visible only when
  `ENABLE_DEV_SETTINGS=true`.
- The frontend reads its limits from the API.
- Settings take 外觀's place in the sidebar footer, and appearance becomes a section of the
  settings page.
- Dead and deprecated settings are deleted.

## Why

Today configuration is spread over:
- about 65 env vars;
- constants hard-coded in Python (`attachments.py`, `models/chat.py`);
- two hand-maintained model lists (`services/chat/models.py`, `WorkflowModelSettings.tsx`);
- upload limits duplicated in `frontend/lib/api/chat.ts`.

The goal is one reviewed baseline file, `.env` reserved for secrets and deployment, and a settings
page for changing behaviour without a rebuild.

## Resolution order

For every setting, the first source that has a value wins:

```
database override (settings page)  >  .env  >  config.yaml  >  code default
```

- `.env` keeps its current variable names, so existing deployments keep working. `.env.example`
  only documents secrets and deployment values; YAML-backed values can still be overridden by env
  as an undocumented escape hatch.
- Per-workflow stage models keep their existing table (`agent_stage_settings`) and page (the
  「模型設定」 tab on each workflow). Their "default" source now means `config.yaml`.

---

## Phase 1: config.yaml (no UI, behaviour-preserving)

### What stays in `.env`

| Group | Variables |
|---|---|
| Secrets | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` |
| Connections | `REDIS_URL`, `DATABASE_URL` |
| Paths | `DOCUMENTS_ROOT`, `AGENT_JOBS_ROOT`, `AGENT_OUTPUT_ROOT` |
| Workers and queues | `*_WORKER_PROCESSES`, `*_MAX_ASYNC_TASKS`, `*_QUEUE_NAME`, `TASKIQ_SCHEDULE_PREFIX` |
| Switches | `ENABLE_AGENT_DEV_ROUTES`, `ENABLE_DEV_SETTINGS` (new, default `false`) |
| Seed | `OPENAI_VECTOR_STORE_ID` |

### `backend/config.yaml`

Nested and commented. It holds values only; metadata lives in code (Phase 2). Default values are
today's code defaults and `.env.example` values.

```yaml
# Global product configuration. Secrets and deployment values live in .env.
models:                       # the model catalog; order = picker order
  - id: litellm/anthropic/claude-fable-5-1
    label: Claude Fable 5.1
    provider: anthropic
    context_window: 128000    # conservative until verified per model
    use: [chat, agents]       # chat | agents | codex
  # … all 10 models from services/chat/models.py; OpenAI ones use plain ids,
  # use: [chat, agents, codex]
chat:
  default_model: gpt-6-luna
  timeout_seconds: 180
  stream_heartbeat_seconds: 10
  compaction_threshold: 0.8   # current code default
agents:
  default_model: gpt-6-luna
  default_reasoning_effort: high
  stages:                     # runner / model / reasoning_effort per stage
    extraction: {runner: …, model: null, reasoning_effort: null}
    planner:    {runner: agents, model: litellm/gemini/gemini-3.8-flash, enabled: true}
    author:     {runner: …, model: gpt-6-luna, reasoning_effort: high}
    reviewer:   {runner: …, model: gpt-6.1-sol, reasoning_effort: high}
  limits:
    timeout_minutes: 60
    heartbeat_seconds: 10
    max_author_attempts: 5
    review_stagnation_limit: 2
    planner_max_evidence_chars: …
    awaiting_outline_ttl_seconds: …
    awaiting_outline_sweep_interval_seconds: …
    event_retention_seconds: 86400
    keep_workspace_on_failure: true
    require_process_isolation: …
uploads:
  max_upload_bytes: 262144000           # knowledge-base documents
  chat:
    max_attachments: 10
    max_message_chars: 20000
    max_document_bytes: 26214400
    max_pdf_bytes: 20971520
    max_pdf_pages: 100
    max_image_bytes: 10485760
    max_image_edge: 1568
cleanup:
  lease_seconds: 300
  retry_base_seconds: 60
  retry_max_seconds: 3600
  reconcile_interval_seconds: 900
  reconcile_batch_size: 100
knowledge_base:
  vector_store_name: NHI-AI Knowledge Base
  bootstrap_timeout_seconds: 10
```

Fill every `…` with the current default from `app/config.py`. Do not invent values.

### Loading

- Use pydantic-settings' YAML support (`YamlConfigSettingsSource`; add `pyyaml` as a direct
  dependency) inside `settings_customise_sources`, with priority **init > env > yaml > defaults**.
- An explicit mapping table in `app/config.py` maps nested YAML paths to the existing flat `Settings`
  fields, e.g. `agents.stages.author.model` → `agent_author_model`. Env names do not change.
- The file path defaults to `backend/config.yaml` and can be overridden with `CONFIG_FILE`.
- Validation errors fail startup with the YAML path in the message.
- `models` is parsed into a validated list. Ids must be unique. `provider` must be one of
  `openai | anthropic | gemini`. Labels are required. `use` must be non-empty.

### Consumers

- `services/chat/models.py`:
  - builds the catalog from `models` where `use` contains `chat`;
  - `context_window` comes from the YAML;
  - delete `_DEFAULT_MODEL_IDS` and `_LABELS`.
- New `GET /models?use=agents|codex|chat` returns `{id, label, provider, available}`.
  `WorkflowModelSettings.tsx` uses it for suggestions; delete its hard-coded `OPENAI_MODELS`. Codex
  takes plain OpenAI ids, the Agents runner takes LiteLLM ids: keep today's conversion
  (`litellm/openai/<id>` for OpenAI models under the Agents runner).
- Chat limits:
  - `attachments.py` and `models/chat.py` read `uploads.chat.*` instead of constants.
  - New `GET /config/client` returns `{chat: {max_attachments, max_message_chars,
    max_document_bytes, max_pdf_bytes, max_image_bytes}, max_upload_bytes}`.
  - The frontend replaces the `CHAT_MAX_*` constants in `lib/api/chat.ts` with a small cached hook.
    Until the fetch resolves, fall back to the current numbers so the composer never blocks.

### Delete

- `openai_chat_model` / `OPENAI_CHAT_MODEL` (nothing reads it).
- The `OPENAI_MODEL` alias.
- The `SLIDES_*` deprecated aliases.
- `chat_model_options` / `CHAT_MODEL_OPTIONS`.
- Update `.env.example` to the "stays in `.env`" table only. Each line gets a one-line comment.

### Navigation: settings replace 外觀 in the sidebar footer (frontend only)

Settings move to where 外觀 is now, like ChatGPT and Claude, and appearance becomes a settings
section.

- `components/app-shell/app-shell.tsx`:
  - Remove 設定 from the main nav list.
  - The footer block (currently `<ThemeMenu />` + 「外觀」) becomes a link to `/settings`: a
    `Settings` (gear) icon + 「設定」, icon-only when the sidebar is collapsed.
  - Show the active state on `/settings` like the other nav items.
- Mobile header: replace `<ThemeMenu />` with an icon-only gear link to `/settings`
  (`aria-label="設定"`). Check that the mobile layout still reaches settings.
- `/settings` 「一般」 tab: the first section is 「外觀」, a three-option segmented control
  (淺色 / 深色 / 跟隨系統) using the same `next-themes` calls `ThemeMenu` uses today.
  - This is a per-browser preference (stored by next-themes in localStorage), **not** a YAML or
    database setting.
  - Below it, keep the links to each workflow's 「模型設定」 tab until Phase 2 fills the page.
- Delete `components/app-shell/theme-menu.tsx` if nothing else uses it.
- Tests:
  - The sidebar footer links to `/settings`, and 設定 is no longer in the main nav.
  - The appearance control switches the theme.

### Tests (Phase 1)

- The YAML loads, and env still overrides a YAML value.
- A bad model entry fails with a clear error.
- The catalog order and labels come from the YAML.
- `GET /models?use=…` filters correctly.
- `GET /config/client` reflects the YAML values.
- Chat attachment validation uses the YAML limits (set a small limit in a test config).
- Frontend: the workflow settings suggestions come from the API, and the composer uses the API
  limits.

---

## Phase 2: settings overrides and the settings page

### Backend

- **Registry:** `app/services/app_settings.py` holds one entry per editable setting:
  - `key` (YAML path, e.g. `chat.timeout_seconds`), `type` (`int | float | bool | enum | model`);
  - `level` (`user | dev`), `label_zh`, `description_zh`, `min`/`max`/`choices`, `unit_zh`;
  - `restart_required` (bool).
- **Table:** `app_setting_overrides(key PK, value JSON, updated_at)`, created by an Alembic
  migration after the current head.
- **Resolver:** `effective(key)` returns `(value, source)`, where source is
  `database | env | config | default`. Call sites of editable settings read through it at use time
  (not at import), so overrides apply live in every container.
- **API:**
  - `GET /app-settings`:
    - returns `{dev_enabled, groups: [{id, label_zh, settings: [{key, label_zh, description_zh, type, constraints, value, source, default_value, restart_required}]}]}`;
    - lists `dev` settings only when `ENABLE_DEV_SETTINGS=true`.
  - `PUT /app-settings/{key}` takes `{value}`. It validates against the registry, returns 422 with a
    Chinese message, and returns 403 for `dev` keys when dev settings are disabled.
  - `DELETE /app-settings/{key}` removes the override (重設為預設).
- **Model visibility:** user setting `chat.hidden_models` (list of ids). Hidden models disappear from
  the chat picker but stay in the catalog.

### Which settings, at which level

| Level | Settings |
|---|---|
| **user** | `chat.default_model`, `chat.hidden_models` |
| **dev** | `chat.timeout_seconds`, `chat.compaction_threshold`, `agents.limits.*`, `agents.stages.planner.enabled`, `uploads.*`, `cleanup.*` |

Per-workflow stage models and effort stay on the workflow pages (user level).

### Frontend (`/settings`)

- Tabs: 「一般」, plus 「開發者」 only when `dev_enabled`. 「一般」 keeps the Phase 1 「外觀」 section
  first, then the user settings.
- Each setting is a row with:
  - the Chinese label and description;
  - a control by type: number with unit, `Switch`, `Select`, or the model picker for `model`;
  - a source badge: 自訂 / .env / 設定檔 / 預設;
  - 「重設為預設」 when the source is 自訂 (confirm with `ConfirmDialog`);
  - 「需重新啟動」 when `restart_required`.
- Save on change, with an inline error on 422.
- Keep the existing links to each workflow's 「模型設定」 tab under 「一般」.
- Use the existing `components/ui` kit; it must work at 375px.

### Tests (Phase 2)

- The migration creates the table.
- The resolver order is database > env > config > default.
- PUT validation: type, range, 403 for dev keys when disabled.
- DELETE resets.
- A live override changes behaviour without a restart (e.g. `uploads.chat.max_attachments`).
- Frontend: the dev tab is hidden when disabled; source badges render; reset confirms first.

## Verify

```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd backend && uv run --group dev pytest tests/ -q          # exit code 0
cd ../frontend && npm test && npm run typecheck && npm run build
```
