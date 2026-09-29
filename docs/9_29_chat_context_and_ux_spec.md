# Spec: chat context management + chat UX polish

**For:** Codex. **Date:** 2026-09-29. **Builds on:**
`docs/9_29_chat_core_and_attachments_plan.md`. The chat core (sessions, the Agents SDK engine,
knowledge-base search, attachments) is already implemented. Read that plan first; its hard
rules apply here unchanged.

Two independent parts: **A. backend context management + reasoning events** and
**B. frontend chat UX**. They share one contract, the SSE events in section C.

---

## Where things are

| What | Where |
| --- | --- |
| Engine: history rebuild, tools, streaming | `backend/app/services/chat/engine.py` (`AgentsSdkChatEngine`, `_message_to_input_item`, `_build_instructions`) |
| Chat routes and SSE forwarding | `backend/app/api/routes/chat.py` (`POST /chat/sessions/{id}/messages`) |
| Tables | `backend/app/models/chat.py` (`ChatSession`, `ChatMessage`, `ChatAttachment`) |
| Attachment storage and processing | `backend/app/services/chat/attachments.py` |
| Model catalog | `backend/app/services/chat/models.py` |
| Migrations | `backend/alembic/versions/` (chat tables: `f9d62e31a528_add_chat_core_tables.py`) |
| Frontend chat | `frontend/components/chat/*`, `frontend/lib/hooks/useChatSession.ts`, `frontend/lib/hooks/useChatEngine.tsx`, SSE parsing in `frontend/lib/api.ts` (`parseChatEventBlock`, `sendChatMessage`) |

Current behaviour worth knowing:

- Every turn replays **all** stored messages (final texts only; tool calls are not replayed).
- PDFs are sent as `input_file` parts, images as `input_image` parts, **on every replay of
  the message they were attached to, forever**. DOCX/TXT/MD are read through the
  `read_attachment` tool.
- The session's attachment list is written into the agent **instructions**, at the very start of
  the prompt.
- Each assistant message stores the provider's token `usage`.

---

## A. Backend

### A1. Attachment expiry

**Problem:** a PDF or image is re-sent with its message on every later turn. A long PDF in a
long chat costs its full size every turn.

**Behaviour:**

- A PDF or image is sent inline with the user message it was attached to, and while that
  message is among the **last 3 user messages** (the turn it was sent plus 2 follow-ups).
  Make `3` a named constant.
- After that, the message is replayed with a text placeholder instead of the file:
  `（附件：公文.pdf，已於先前訊息提供；如需再次查看請使用 open_attachment）`.
- New tool: `open_attachment(name: str)`, for PDFs and images of the current session. It
  returns the file to the model for the current turn.
  - **Preferred:** return the file as a structured tool output (`ToolOutputFileContent` with
    inline `file_data` for PDFs, `ToolOutputImage` with a data URL for images; see
    `agents/tool.py`).
  - **Verify first**, in the installed SDK source (`agents/models/chatcmpl_converter.py`),
    that such outputs survive the LiteLLM path (Gemini, Anthropic) as well as the OpenAI
    path. The converter comment suggests tool-output files may only be supported as `file_id`.
  - **If a provider path cannot carry them,** use this fallback instead: when the user's new
    message mentions an attachment's display name, re-inline that attachment for this turn,
    and do not register `open_attachment`.
  - Report which approach you used and why.
- DOCX/TXT/MD are unchanged: they were never inline; `read_attachment` already covers them.
- Tool labels: `tool_started` 「重新開啟附件：公文.pdf」, `tool_finished` 「已開啟」.

### A2. Cache-friendly prompt order

**Problem:** OpenAI and Gemini cache an unchanged *prefix* of the prompt automatically
(Anthropic uses explicit cache markers). Our instructions contain the attachment list, so
every new attachment changes the first part of the prompt and invalidates the whole cache.

**Behaviour:**

- The agent instructions are **static**: no per-session or per-turn content.
- Per-turn context (the current attachment list with each file's kind and status, and the
  compaction summary if any, see A3) goes into the input **after** the replayed history. Put
  it as an extra `input_text` part on the *new* user message, not in the instructions and not
  in the stored message content.
- Replayed history must be byte-for-byte stable between turns. Same order, same placeholder
  text, no timestamps.
- Anthropic: if the SDK exposes a prompt-cache breakpoint on input parts (see
  `_copy_prompt_cache_breakpoint` in the converter), set one on the last replayed history
  item. If that is not cleanly possible through LiteLLM, skip it and say so.

### A3. Compaction (summarizing older turns)

**Problem:** nothing stops a conversation from growing past the model's context window.

**Behaviour:**

- Add `context_window` (tokens) to each model option in `app/services/chat/models.py`. If a
  value isn't known for certain, use a conservative 128 000. Also add a
  `CHAT_COMPACTION_THRESHOLD` setting (default `0.6`).
- **Trigger:** before running a turn, if the previous assistant message's
  `usage.input_tokens` exceeds `threshold × context_window` of the model *being used for this
  turn*, compact first.
- **Compact:**
  - summarize every message except the **last 6** (a named constant) into a running summary,
    with one model call to the turn's model (no tools);
  - store it on the session: new columns `chat_sessions.summary` (text, nullable) and
    `chat_sessions.summary_through_message_id` (uuid, nullable); new Alembic migration;
  - when compacting again, summarize the *previous summary + the newly old messages*.
- **Summary prompt** (Traditional Chinese output) must keep:
  - the user's goal and constraints;
  - decisions made;
  - facts and numbers with the **document names** they came from;
  - attachment names and what they contained;
  - open questions.

  It must never include internal ids.
- **Replay after compaction:** static instructions → history *after*
  `summary_through_message_id` → new user message with the per-turn context part (A2) that
  now starts with 「先前對話摘要：…」.
- **Stream:** emit `compacting` before the summary call and `compacted` after (section C).
  Compaction failure must not fail the turn: log it, emit `compacted` with `ok: false`, and
  continue with the full history.
- `GET /chat/sessions/{id}` returns `compacted_through_message_id` (nullable) so the UI can
  place its marker. **Do not** return the summary text itself; it is internal context.

### A4. Reasoning events

**Problem:** the models think before answering, but the UI shows nothing until the first tool
step or word.

**Behaviour:**

- Forward **reasoning summaries** as `reasoning_delta` events.
  - OpenAI reasoning models: enable summaries through the model settings the SDK exposes
    (e.g. `reasoning={"summary": "auto"}`) when the model supports reasoning.
  - LiteLLM providers: the SDK's `chatcmpl_stream_handler.py` already turns
    `reasoning_content` into reasoning items and events. Forward whatever text arrives.
- Verify the exact stream event types in the installed SDK (`agents/stream_events.py`,
  `agents/items.py` `ReasoningItem`, raw `response.reasoning_summary_text.delta`). Do not
  guess.
- If a provider sends no reasoning, send nothing. The UI then just shows its generic 「思考中…」.
- Store the reasoning text on the assistant message (new nullable column
  `chat_messages.reasoning`, capped at 20 000 characters) and return it in
  `GET /chat/sessions/{id}`, so a reloaded conversation can show it collapsed.
- Reasoning is **never** replayed to the model in later turns.

### A5. Small fixes from the chat-core review

- `DELETE /chat/sessions/{id}/attachments/{attachment_id}` removes the DB row but leaves the file
  (and its `.txt` sidecar) on disk. Delete them too.
- `backend/app/api/routes/slides.py`'s outline-message stream double-wraps
  `_stream_with_heartbeat` in an extra generator. The chat route removed that pattern because
  it doesn't reliably run cleanup on client disconnect. Apply the same single-wrap pattern
  there; its behaviour and output must otherwise be unchanged.

---

## B. Frontend

### B1. Stick-to-bottom scrolling

- While a reply streams, the message list follows the bottom.
- If the user scrolls up (more than ~80 px from the bottom), stop following. Show a floating
  「↓ 最新訊息」 button; clicking it scrolls to the bottom (smooth) and resumes following.
- Opening a conversation scrolls to the bottom instantly. Sending a message always scrolls
  to the bottom.
- Prefer a small hook (`useStickToBottom`) over a new dependency. If you do add a library,
  justify it.

### B2. Thinking indicator and reasoning block

- From send until the first event of any kind: a 「思考中…」 line with a shimmer effect,
  replacing today's static 「正在處理問題…」.
- On `reasoning_delta`: a collapsible 「思考過程」 block above the answer.
  - Open while reasoning streams; the text is muted and smaller.
  - It auto-collapses when the first `text_delta` arrives, and its header then shows
    「思考了 N 秒」 (N measured on the client).
  - Collapsed by default for stored messages that have `reasoning`.
- The compaction events (section C) show 「整理先前對話中…」 with the same shimmer. After
  reload, a thin divider 「已摘要較早的對話」 appears after the message whose id equals
  `compacted_through_message_id`.

### B3. Tool steps

- While running: each step row as today, but the running step's label has a shimmer instead
  of plain text.
- Once the answer starts (first `text_delta`) or the turn ends, the steps collapse into one
  summary line, expandable on click, e.g. 「已搜尋知識庫 2 次・讀取 1 個附件」. Count by tool
  name; use the labels already sent by the backend for the expanded list.
- Tool names are internal: map them to Chinese in one place (`search_knowledge_base` → 搜尋知識庫,
  `read_attachment` → 讀取附件, `open_attachment` → 重新開啟附件). Unknown tools → 「使用工具」.

### B4. Animations

- New messages, tool-step rows and source chips fade and slide in (150–200 ms, a few px of
  translate). Use `tw-animate-css` (already installed, see `app/globals.css`) or plain CSS.
- A blinking caret at the end of the streaming text while `pending`.
- Respect `prefers-reduced-motion`: no slide or shimmer, and instant scrolling.
- Keep it subtle. No bounces, no long durations.

---

## C. SSE contract (new events)

The existing events are unchanged: `message_start`, `text_delta`, `tool_started`,
`tool_finished`, `sources`, `done`, `error`.

| type | payload | when |
| --- | --- | --- |
| `reasoning_delta` | `text` | reasoning summary text, before or between tool steps |
| `compacting` | (none) | before the compaction model call |
| `compacted` | `ok: bool` | after it (also on failure; the turn continues) |

Order: `message_start` → (`compacting` → `compacted`)? → any interleaving of `reasoning_delta`,
`tool_started`/`tool_finished`, `text_delta` → `sources` → `done`, or `error` in place of
`sources`/`done`. The frontend parser must ignore unknown event types rather than fail.

`GET /chat/sessions/{id}` additions: `compacted_through_message_id` on the session and
`reasoning` on each message.

---

## Hard rules (unchanged from the chat-core plan)

- No internal identifiers in anything user-visible: streamed text, reasoning, labels,
  summaries, errors. Document display names only.
- API keys never leave the backend.
- Tools are our Python functions only. No shell, no filesystem access for the model.
- User-visible strings are Traditional Chinese.
- Don't change path grants, the slides/news workflows (except the A5 stream fix), or runner
  construction.

## Tests

Backend (fakes only, no network):

- An attachment is inline for 3 user turns, then a placeholder; the placeholder text is stable
  across turns.
- `open_attachment` (or the fallback) re-inlines the right file, only for the current session.
- The instructions string is identical across turns and sessions; the attachment list only
  appears in the new user message's extra part.
- Compaction triggers at the threshold and not below. Replay after compaction = summary +
  messages after the marker. A failed summary call still completes the turn. Repeated
  compaction folds in the previous summary.
- `reasoning_delta` is forwarded and stored (capped); it is not replayed.
- Deleting an attachment removes its files; the slides outline stream still behaves the same.
- The migration adds the new columns (`tests/infrastructure/test_migrations.py` pattern).

Frontend:

- The parser handles the three new events and ignores unknown ones.
- Stick-to-bottom: follows while at the bottom, stops when scrolled up, the button resumes.
- Reasoning block: opens on the first `reasoning_delta`, collapses on the first `text_delta`,
  collapsed for stored messages.
- Tool steps collapse into the summary line with correct counts.
- Reduced motion disables the animations.

Run all of these, and all must pass:

```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd backend && uv run --group dev pytest tests/ -q
cd ../frontend && npm test && npm run typecheck && npm run build
```

## When done

Report:

- files changed (one line each);
- test results;
- for A1: which mechanism you used (structured tool output or the fallback) and what you
  verified in the SDK source;
- for A4: which providers emit reasoning through our path, per the SDK source;
- anything you had to decide that this spec did not settle.

## Implementation notes

- Attachment reopening uses the name-mention fallback for every provider. The installed
  Agents SDK preserves inline `file_data` in structured tool outputs, but LiteLLM's
  Anthropic tool-result conversion accepts text and images and drops file parts. No
  `open_attachment` tool is registered; a named PDF/image is added to the new message only.
- Instructions are static. Attachment kind/status and any running summary are appended to
  the newest user input, never saved into its message content. Anthropic cache breakpoints
  are omitted: the SDK copies `prompt_cache_breakpoint`, whereas the installed LiteLLM
  Anthropic conversion consumes `cache_control`.
- Model context windows conservatively use 128,000 tokens. `CHAT_COMPACTION_THRESHOLD`
  defaults to `0.6` and must be between zero and one, exclusive. Compaction uses the
  specified strict `>` trigger and retains the last six stored messages. Failure replays
  full history for that turn and preserves the previous summary/marker.
- Reasoning summaries use `response.reasoning_summary_text.delta`. OpenAI reasoning
  families request automatic summaries; LiteLLM's `reasoning_content` produces the same
  summary event when supplied by Gemini/Anthropic. Separate raw reasoning-text events are
  not displayed as summaries. No provider response is assumed to contain reasoning.
- Apply Alembic revision `a6294c7e1d30` before using an existing chat database. Summary text
  remains internal; session responses expose only its message marker. Stored reasoning
  is capped at 20,000 characters and excluded from later model input.
- Chat follows the app shell's window scroll container without a new dependency.
  Reasoning duration is measured from its first delta to the first answer delta; the
  generic thinking indicator remains visible when a provider sends no reasoning.
