# Spec: my files, knowledge-base attachments, artifacts

**Date:** 2026-09-29. **Builds on:** `docs/9_29_chat_core_and_attachments_plan.md` (hard rules apply
unchanged: no internal ids shown to users, API keys never leave the backend, Traditional
Chinese UI, tools are our Python functions only).

## Goal (ChatGPT's ＋ menu and Library, for our app)

1. **Uploads belong to the user, not to one conversation.** A conversation *references*
   files. Deleting a conversation keeps its files; files are deleted in 「我的檔案」.
2. **The ＋ menu in the composer** is a dropdown:
   - 上傳檔案 — 從電腦上傳
   - 從知識庫加入 — 瀏覽並搜尋知識庫文件
   - 從我的檔案加入 — 先前上傳的檔案
   - 使用技能 — disabled, 「即將推出」
3. **After an upload** (picked files, not pasted images), a modal asks 「要將這些檔案加入知識庫嗎？」
   with **「只用於此對話」 as the default**. Choosing 「同時加入知識庫」 asks for a category (and
   optional folder), warns 「加入後所有人都能搜尋到，並需數分鐘建立索引。」, and creates a
   knowledge-base **copy** through the normal ingestion pipeline. The file is usable in the
   chat immediately either way.
4. **Knowledge-base documents can be attached to a conversation.** The model then reads the
   whole document, not just search snippets.
5. **Generated outputs are artifacts:** every generated deck (and future reports) gets a
   record the user can find, download and delete in 「我的檔案」.
6. **「我的檔案」** in the sidebar, route `/files`, with tabs 「上傳的檔案」 and 「產出的文件」.

---

## Backend

### Data model (one Alembic migration after the current head)

- **`user_files`** replaces `chat_attachments` (migrate existing rows and keep their stored
  files working). Columns: `id`, `display_name`, `mime_type`, `kind` (`document` | `image`),
  `size_bytes`, `storage_key`, `status`, `error`, `text_chars`, `origin_session_id`
  (nullable, **SET NULL** when the conversation is deleted), `promoted_document_id`
  (nullable), `created_at`. `TODO(auth)`: an owner column comes with login.
  - Storage: new files go under `user_files/<file_id>`. Existing files keep their current
    `storage_key` (paths under `chat_attachments/<session_id>/`); resolve paths from the
    storage root plus `storage_key`, never from the session. Paths are never user-controlled.
- **`chat_session_files`** (`session_id`, `file_id`, `created_at`): which files a
  conversation uses. Deleting a conversation deletes its links, **not** the files.
- **`chat_session_documents`** (`session_id`, `document_id`, `created_at`): knowledge-base
  documents attached to a conversation.
- **`artifacts`**: `id`, `kind` (`slide_deck` | `news_draft` | `report`), `title`, `mime_type`,
  `size_bytes`, `storage_key`, `source_workflow` (`slides` | `news` | `chat`),
  `source_job_id` (nullable), `session_id` (nullable, SET NULL), `created_at`.
  - Slides: when a job publishes (`SlidesWorkflowAdapter.publish`), create an artifact
    for the published deck, titled with the job's title. Backfill existing completed slide
    jobs whose file still exists. The existing job download URL keeps working.
  - News: if news jobs produce a downloadable file, do the same; if they only return text,
    skip and say so in the report.
  - Chat/skill reports come later (the 立院QA skill will create `report` artifacts through
    the same model).

### Engine (`app/services/chat/engine.py`)

- Session attachments = `chat_session_files` + `chat_session_documents`.
- Attached **knowledge-base documents** behave exactly like user files of the same type:
  - PDFs are sent as `input_file`, with the same 3-user-turn expiry and mention-to-reopen;
  - DOCX/TXT/MD are read through `read_attachment` (extract text on first use and cache it
    beside the stored document, reusing the attachment extraction code).
  - In the turn context (the fenced 〈系統提供的對話背景〉 block), mark them 「知識庫文件」.
    Only READY, retrieval-enabled documents can be attached.
- Everything else (search tool, compaction, reasoning) is unchanged.

### API

| Method and path | Purpose |
| --- | --- |
| `POST /chat/sessions/{id}/attachments` | Unchanged contract: upload → creates a `user_files` row **and** links it to the session |
| `POST /chat/sessions/{id}/files` | Body `{file_ids}`: link existing user files to the session |
| `DELETE /chat/sessions/{id}/files/{file_id}` | Unlink (the file is kept) |
| `POST /chat/sessions/{id}/documents` | Body `{document_ids}`: attach READY, retrieval-enabled knowledge-base documents |
| `DELETE /chat/sessions/{id}/documents/{document_id}` | Detach |
| `GET /chat/sessions/{id}` | `attachments` now lists linked files **and** documents, each with `source`: `upload` / `knowledge_base` |
| `GET /files` | User files, newest first, with `origin_session_title` (nullable) and `in_knowledge_base` (bool) |
| `GET /files/{id}/content` | Download or preview |
| `DELETE /files/{id}` | Delete the file and its stored bytes; its links go too. Old messages keep their text. |
| `POST /files/{id}/promote` | Body `{category, folder_id?}`: copy into the knowledge base through the **same** code path as `POST /documents` (reuse its helper; don't duplicate ingestion). Only knowledge-base-supported types (PDF, DOCX, TXT, MD); images → 422. Sets `promoted_document_id`. Repeat → 409 「已加入知識庫」. |
| `GET /artifacts` | Newest first: `id`, `kind`, `title`, `mime_type`, `size_bytes`, `source_workflow`, `created_at` |
| `GET /artifacts/{id}/download` | The file, with a readable filename |
| `DELETE /artifacts/{id}` | Delete the record and its stored copy (not the original job output) |

- Keep the existing attachment content and delete routes working, or redirect them to the new
  model, so the current UI doesn't break mid-migration.
- Deleting a conversation no longer deletes files (change `delete_session` accordingly).

### Tests (backend, fakes only)

- The migration moves existing attachments into `user_files` plus links, and their files
  still resolve.
- Deleting a conversation keeps its files; deleting a file removes its links and bytes.
- Linking a file into a second conversation makes it visible to the model there.
- Promote: creates a document through the normal ingestion path; 409 on repeat; 422 for
  images.
- An attached knowledge-base PDF is inlined with expiry; an attached DOCX is readable through
  `read_attachment`; a non-ready document is rejected.
- Slide publish creates an artifact; the backfill works; artifact download returns the file;
  no ids in any user-visible text.

---

## Frontend (uses `lib/api/*` modules; add `lib/api/files.ts` and `lib/api/artifacts.ts`)

- **Composer ＋** becomes a `DropdownMenu` (same component family as the model picker) with the
  4 items above; lucide icons Paperclip, Library, FolderOpen, Zap. 使用技能 is disabled
  with 「即將推出」.
- **「從知識庫加入」 dialog:**
  - a search box plus a list of READY knowledge-base documents (display name, category);
  - multi-select, 「加入對話」;
  - reuse existing document list API calls.
- **「從我的檔案加入」 dialog:** the user's files (name, type, origin conversation), multi-select,
  「加入對話」.
- **Upload modal:** after picking files (not pasted images), one modal per batch:
  - radio buttons, defaulting to 「只用於此對話」;
  - 「同時加入知識庫」 reveals category (required) and folder (optional) selects, plus the
    warning line;
  - 「確定」 calls promote for each file.

  Uploading proceeds regardless of the choice.
- **Attachment chips** show a small 「知識庫」 badge for attached knowledge-base documents; removing
  a chip unlinks (never deletes).
- **Sidebar:** add 「我的檔案」 to the nav (after 知識庫管理).
- **`/files` page, two tabs:**
  - 「上傳的檔案」: a table/list with name, type, size, date, 來源對話 (link), and a
    「已加入知識庫」 badge. Actions: 下載, 加入知識庫 (same modal fields), 刪除 (confirm).
  - 「產出的文件」: title, type (簡報/新聞稿/報告), date, source (link to the job page for
    slides). Actions: 下載, 刪除 (confirm).
  - Empty states in Chinese.
- 375px width works, and nothing shows ids.

### Tests (frontend)

- The ＋ menu has 4 items and 使用技能 is disabled.
- The upload modal defaults to 「只用於此對話」, and promote is called only when the user chooses
  it (with the category).
- Pasted images skip the modal.
- Attach-from-knowledge-base and attach-from-my-files call the link APIs.
- A 知識庫 chip badge renders; removing a chip unlinks.
- `/files` renders both tabs, and delete confirms first.

## Verify

```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
cd backend && uv run --group dev pytest tests/ -q   # check the exit code, not only the count
cd ../frontend && npm test && npm run typecheck && npm run build   # exit codes must be 0
```
