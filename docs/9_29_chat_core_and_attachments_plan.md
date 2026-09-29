# Plan: chat core + attachments (harness phases 1–2)

**Date:** 2026-09-29. **Background:** `docs/9_26_chat_harness_options.md` (why the Agents SDK,
why our own skill layer later). **Replaces** the current single-question chat.

## Goal

A real agentic chat, like web ChatGPT/Claude:

- **Saved conversations** with a list in the sidebar; the agent remembers earlier turns.
- **An agent loop** (OpenAI Agents SDK) that can search the knowledge base and read the
  user's attachments, and shows those steps while it streams.
- **A model picker in the chat box**, working with OpenAI, Gemini and Anthropic.
- **Attachments:** documents (PDF, DOCX, TXT, MD) and images, dropped into the chat box.
  They belong to that chat only and are separate from the knowledge base.

Out of scope here (phase 3+): skills, the document side panel, approval cards, .docx export,
「加入知識庫」 promotion, login.

---

## Decisions already made

1. **The old chat is removed**, not kept alongside: `POST /chat`, `POST /chat/stream`,
   `GET /qa-modes`, `app/services/chat/{responder,citations,retrieval}.py`,
   `app/models/chat.py` and their frontend and tests. Keep anything other features import
   (for example, move `SSE_RESPONSE_HEADERS` and `_stream_with_heartbeat` from
   `app/api/routes/chat.py` to a shared module, because the slides outline stream uses them).
2. **Attachments are not knowledge-base documents.** They get their own table, tied to one chat
   session, and are never indexed into the vector store.
3. **Messages live in our own Postgres tables.** Each turn, the conversation is rebuilt from
   them and passed to the agent (final user and assistant texts; earlier tool calls are not
   replayed).
4. **Single user** (prototype): no ownership columns yet. Add `TODO(auth)` where ownership
   will go.

## Hard rules (same as the rest of the app)

- **No internal identifiers shown to users:** no document ids, vector-store file ids, storage
  keys or hashes in streamed text, tool labels, sources or error messages. Users see
  **document display names** only.
- **API keys never leave the backend.** The model list reports availability only.
- **Tools are our Python functions.** No shell, no filesystem access for the model, no sandbox.
- **User-visible strings are Traditional Chinese.**

---

## Backend

### Tables (SQLModel + one Alembic migration after the current head)

`chat_sessions`
: `id` (uuid PK), `title` (str, default 「新對話」), `model` (str: the last model used),
  `created_at`, `updated_at`.

`chat_messages`
: `id` (uuid PK), `session_id` (FK, index, cascade delete), `role` (`user` | `assistant`),
  `content` (text), `attachment_ids` (JSON list, user messages), `sources` (JSON list of
  `{"name": str, "snippet": str}`, assistant messages), `status` (`complete` | `interrupted` |
  `error`), `model` (str, assistant messages), `usage` (JSON, nullable), `created_at`.

`chat_attachments`
: `id` (uuid PK), `session_id` (FK, index, cascade delete), `display_name`, `mime_type`,
  `kind` (`document` | `image`), `size_bytes`, `storage_key`, `status`
  (`ready` | `failed`), `error` (user-safe Chinese message, nullable), `text_chars`
  (int, nullable), `created_at`.

### Attachment handling (`app/services/chat/attachments.py`)

- Storage: under the existing documents storage root, `chat_attachments/<session_id>/<uuid>`.
  Reuse `LocalDocumentStorage` (`app/services/documents/storage.py`) patterns: atomic
  writes, no user-controlled path parts.
- **Limits:** documents 25 MB, images 10 MB, at most 10 attachments per message. Allowed:
  `.pdf .docx .txt .md` and `image/png`, `image/jpeg`, `image/webp`. Check the extension
  *and* the content (magic bytes / Pillow open). Reject anything else with a clear message.
- **Documents:** extract text **synchronously during upload** (in `asyncio.to_thread`). Reuse
  the deterministic extractor in
  `backend/.agents/skills/source-document-extraction/scripts/source_extraction.py` for DOCX
  and PDF (text layer). Save the text next to the file (`<storage_key>.txt`). If a PDF has no
  text layer, mark it `ready` with `text_chars = 0` and a note shown in the UI:
  「此 PDF 沒有文字層，無法讀取內容。」 (OCR is phase 3+.)
- **Images:** re-encode with Pillow (strips metadata and neutralizes odd files). Downscale so
  the long edge is ≤ 1568 px. Store as PNG or JPEG.
- Deleting a session deletes its attachment files.

### Model list (`app/services/chat/models.py`)

- Options, each with a display label and provider:
  - `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna` (OpenAI; sent as plain names)
  - `litellm/gemini/gemini-3.8-flash` (Gemini)
  - `litellm/anthropic/claude-sonnet-5` (Anthropic)
- An env setting `CHAT_MODEL_OPTIONS` (comma-separated) may replace the list. A new
  `CHAT_DEFAULT_MODEL` setting falls back to `agent_default_model`.
- An option is **available** only if its provider's key is configured. The server rejects an
  unavailable or unknown model with 422.

### Chat engine (`app/services/chat/engine.py`)

- A small interface, `ChatEngine.run_turn(history, user_message, attachments, model) ->
  AsyncIterator[ChatEvent]`, and one implementation, `AgentsSdkChatEngine`. Routes and storage
  depend only on the interface, so the harness can be swapped later.
- Build the model exactly the way `app/services/agentic/sdk_runner.py` does for the planner
  (LiteLLM for `litellm/...` names, explicit provider keys, tracing disabled, usage included).
  Reuse its helper; don't duplicate it.
- `Runner.run_streamed` with an `Agent` that has:
  - **Instructions** (Chinese): you are the 健保署 AI assistant; ground factual claims in
    knowledge-base search results or attachments; cite sources inline as 【文件名稱】; say
    so when the sources don't contain the answer; never reveal internal ids or paths. Also
    list this session's attachments by name, kind, and whether text is available.
  - **Tools:**
    - `search_knowledge_base(query: str, category: str | None = None, max_results: int = 8)`:
      calls `client.vector_stores.search` on the ready vector store (`RetrievalIndexRegistry`
      in `app/services/retrieval/registry.py`), always filtered to `is_news_source = false`,
      plus `qa_set = <category>` when a category (`legislative_qa` / `public_opinion` /
      `bei_can`) is given. **Post-filter** results against our `documents` table: keep only
      `READY` and `retrieval_enabled` documents, and replace file names with the document's
      `display_name`. Returns numbered results with name and snippet (no ids).
    - `read_attachment(name: str, start: int = 0, max_chars: int = 20000)`: returns a slice of
      an attachment's extracted text, with the total length so the model can page through it.
      Only attachments of the current session.
  - **Images:** image attachments go into the user message as image input parts on the turn
    they were attached, and are re-sent with that message in later turns. Check the SDK's
    input-item format for images and that it survives the LiteLLM path; cover it with a test.
- Collect every search result the model *used* (all results returned in the turn is fine for
  v1) into the assistant message's `sources`, deduplicated by name.

### Streaming events (SSE, `data: {"type": ..., ...}`)

Same transport as today: `SSE_RESPONSE_HEADERS` (`no-cache, no-transform`), 10 s
heartbeats.

| type | payload | when |
| --- | --- | --- |
| `message_start` | `message_id` | the assistant message is created |
| `text_delta` | `text` | streamed answer text |
| `tool_started` | `tool`, `label` (e.g. 「搜尋知識庫：健保藥費」, 「讀取附件：報告.pdf」) | a tool call begins |
| `tool_finished` | `tool`, `label` (e.g. 「找到 6 筆資料」) | a tool call ends |
| `sources` | `sources: [{name, snippet}]` | before `done` |
| `done` | `message_id`, `title` (the session title, which may have just been set) | the turn completes |
| `error` | `code`, `message` (Chinese, user-safe) | failure; the message is saved with `status = error` |

If the client disconnects, save the partial text with `status = interrupted`.

### API (`app/api/routes/chat.py`, rewritten)

| Method and path | Purpose |
| --- | --- |
| `GET /chat/models` | `[{id, label, provider, available}]` + `default` |
| `GET /chat/sessions` | list, newest `updated_at` first: `{id, title, updated_at}` |
| `POST /chat/sessions` | create (optional `model`) → session |
| `GET /chat/sessions/{id}` | session + messages + attachments |
| `PATCH /chat/sessions/{id}` | rename (`title`) |
| `DELETE /chat/sessions/{id}` | delete session, messages, attachment files |
| `POST /chat/sessions/{id}/attachments` | multipart, one file → attachment record (`status`, `error`) |
| `GET /chat/sessions/{id}/attachments/{attachment_id}/content` | the file (for image previews and opening a document) |
| `DELETE /chat/sessions/{id}/attachments/{attachment_id}` | remove an attachment not yet sent |
| `POST /chat/sessions/{id}/messages` | body `{content, attachment_ids, model}` → SSE stream |

- **Title:** set from the first user message (first 30 characters, whitespace collapsed). No
  extra model call.
- Attachment ids in a message must belong to the same session and be `ready`.
- Attachment records returned to the client carry `id` (the client needs it to send), but the
  UI never *displays* ids.

### Tests

Sessions CRUD; messages persist and history is rebuilt in order; the search tool post-filters
disabled or unknown documents and never returns ids; `read_attachment` is limited to the
session; upload validation (type, size, magic bytes, image re-encode); an unavailable model →
422; SSE event order with a fake engine; interrupted and error statuses; old chat routes gone.
Use fakes for the vector store and the model; no network.

---

## Frontend

- **Routes:** `/chat` (new conversation) and `/chat/[sessionId]`. The session is created on
  the first send, then the URL changes to `/chat/<id>`.
- **Sidebar (app shell):** under 「對話」, the recent conversations list (title, newest first)
  with rename and delete in a small menu. 「新對話」 goes to `/chat`.
- **Message list:**
  - user and assistant bubbles; assistant text rendered as Markdown (reuse
    `components/ai-elements/*` if suitable);
  - tool steps as small status rows while running (spinner → check);
  - sources as chips below the answer (document names);
  - interrupted and error messages marked.
- **Composer:**
  - an auto-growing textarea (Enter to send, Shift+Enter for a newline);
  - a 📎 attach button, drag-and-drop and paste for images;
  - attachment chips with upload progress / ready / failed, image thumbnails and a remove
    button;
  - the **model picker** (unavailable models disabled with 「未設定金鑰」);
  - Send becomes Stop while streaming (aborts the request).
- **Empty state:** a short greeting and 2–3 example prompts.
- Remove the old chat UI (`ChatView` scope/mode selection, `useChatRequest`, `chat-state`,
  the old API client functions and tests) once the new one replaces it.
- Tests: the stream parser handles every event type; the composer blocks send while uploads
  are pending; sessions list and navigation; model picker disables unavailable models.

---

## Build order

1. **Backend** (tables, attachments, engine, routes, tests) and **frontend** (against the
   contract above, with a mocked API in tests) can run in parallel.
2. Then: remove the old chat on both sides, run everything, and do a manual test:
   - `docker compose build backend frontend && docker compose up -d`;
   - ask a question that needs the knowledge base, on OpenAI, then Gemini;
   - attach a PDF and ask about it;
   - attach an image and ask about it;
   - reload the page: the conversation is still there.
