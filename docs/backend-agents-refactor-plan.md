# Backend conversations and agent workflows refactor

Status: proposed implementation plan, 2026-09-18. Based on the current working tree, including ongoing news workflow changes. No runtime changes are included.

## Product contract

Treat a saved conversation as the entry point for answering questions, developing documents, and planning presentations. Use OpenAI Agents SDK for model/tool execution; the application owns durable records, workflow transitions, user approval, and artifact publication.

| PM requirement | Proposed behavior | Acceptance condition |
| --- | --- | --- |
| 對話紀錄、存對話框 | Named, persistent conversation threads with ordered messages, citations, tasks, and downloads | Reopen after browser/server restart and continue with prior context |
| 根據知識庫及對話產生文本 | Chat can create a document task using selected sources and the conversation brief | Download reflects the requested revisions and cites its factual sources |
| 輿情／立院等不同格式 | Versioned output templates independent of retrieval scope | Same authorized sources can produce different requested formats |
| 知識庫改為各科 | 科別 catalog and document associations | Upload, browse, search, and source selection use 科別 |
| 簡報大綱互動及確認 | Generate, revise, validate, explicitly confirm, then author | An unapproved or stale outline cannot start authoring |

Working assumptions: 科別 organizes/filter sources; it does not automatically imply access restrictions. Initial text downloads are DOCX and TXT. These assumptions await product input. Templates, official 科別 list, cross-section document ownership, conversation ownership/identity, and retention requirements need confirmation before the corresponding implementation ships. Interpret 存對話框 as saved conversation threads.

## Evidence from the current backend

- `backend/app/models/chat.py`: `ChatRequest` accepts question, mode, and document IDs; there is no conversation ID. `QaMode` explicitly matches retrieval metadata.
- `backend/app/api/routes/chat.py`: `_resolve_document_scope` checks category equality, readiness, retrieval eligibility, and pending category transitions. Preserve its server-controlled source allowlist behavior while changing the taxonomy.
- `backend/app/services/chat/responder.py`: `build_request` sends only the current question to Responses API. Mode selects instructions and search filtering. Answers without citations receive an insufficient-evidence response.
- `backend/app/models/documents.py`: category and pending-category transitions also affect ingestion jobs and public document contracts. This is more than a UI label rename.
- `backend/app/models/slides.py`: durable slide jobs already record input briefs, status, leases, and artifacts; their request contract lacks an outline revision or approval record.
- `backend/app/services/slides/runtime.py`: the author consumes frozen `work/evidence.json` and extracted assets. Extend this evidence contract to the planning stage.
- `backend/app/services/slides/adapter.py` and `backend/app/services/agentic/service.py`: extraction, authoring, deterministic validation, and independent semantic review already exist.
- `backend/app/tasks/agents.py`: the worker builds a runner registry containing only `codex`. `AgentRunner` provides a migration seam.
- `backend/pyproject.toml` declares `openai-agents>=0.22.2`; `services/agentic/test_new_agent.py` is an SDK experiment, not the production integration.

The existing graph was used for discovery; its August snapshot predates current changes. Current source files are authoritative for these findings.

## Target responsibilities

1. **Conversation service:** thread CRUD, messages, ownership, context selection, and ordered turn execution.
2. **Knowledge service:** document lifecycle, 科別 associations, authorized source resolution, retrieval, and evidence snapshots.
3. **Workflow service:** document and presentation tasks, immutable revisions, approval transitions, queue dispatch, retry/cancellation, and publication.
4. **Agents SDK runtime:** agent definitions, typed tools, structured outputs, streamed execution, and bounded specialist calls.
5. **Artifact service:** validated files, version metadata, authorized downloads, and cleanup.

Keep FastAPI, PostgreSQL/SQLModel, Taskiq/Redis, and existing validation/rendering capabilities. Store authoritative state in PostgreSQL; Redis carries queued work and transient progress.

Use one conversational agent with bounded tools for source search, document drafting, and outline planning. Split agents where instructions, tools, or review independence differ. Do not create one agent per 科別 or per output format. Apply versioned 科別 guidance and template configuration to reusable agents.

Use an explicit application pipeline for presentation author/reviewer stages. Agents may propose actions; application services enforce prerequisites. The chat agent cannot set approval fields or bypass workflow state checks.

## Persistence and context

Proposed records:

| Record | Essential data |
| --- | --- |
| Department | Stable ID, name, active flag, optional versioned work guidance |
| DocumentDepartment | Document ID and department ID; supports shared sources without duplicating files |
| Conversation | ID, owner/workspace, title, selected scope, timestamps, archive status |
| Message | Conversation ID, sequence, role, content, citations, run ID, completion status |
| AgentSessionItem | Conversation/session ID, sequence, SDK input/output item, schema version |
| WorkflowTask | Conversation ID, kind, status, current revision, active run, immutable brief |
| AgentRun | Task/turn ID, attempt, state, model/config versions, usage, lease, sanitized error |
| OutlineRevision | Task ID, revision, structured outline, evidence snapshot, validation result |
| Approval | Outline revision/hash, approving actor, timestamp, source snapshot hash |
| Artifact | Task/run/revision IDs, storage key, MIME type, filename, hash, provenance |
| OutputTemplate | Stable format key, version, required sections, rendering rules |

Use a PostgreSQL-backed SDK session adapter, verifying its Python interface against the pinned SDK during implementation. Store SDK tool-call/results separately from display messages, but commit them consistently through the turn service. Use one context continuation strategy; do not combine session replay with provider conversation chaining. Keep the full display history while compacting model context with provenance retained.

Accept a client request ID, persist the user message, and create a durable run before execution. Serialize runs within a conversation with a lease/version check; duplicate requests return the existing run. Commit the final message and run outcome before emitting the terminal event. A dropped stream can reconnect to run status/history; long tasks continue in workers. Partial text must remain marked incomplete after failure.

Ownership checks apply to conversation, task, approval, source, and download lookups. The authenticated actor must come from server context. Establish the deployment's identity integration before exposing per-user history; do not invent an authentication scheme from the current inspection.

## Knowledge taxonomy migration

Separate `department_ids` and explicit `document_ids` from `output_template_id`. 輿情、立院、備參 become output purposes/templates, with no requirement to store those labels on new knowledge documents.

1. Add the department catalog and associations without deleting category data.
2. Obtain an explicit mapping for existing documents. Old purpose labels cannot reliably determine 科別. Keep unmapped records visible for assignment, excluded from department-scoped search until assigned.
3. Update upload/update/list contracts, ingestion payloads, retrieval metadata/filter construction, source pickers, and chat empty states together.
4. Reconcile retrieval metadata with resumable jobs. Use a metadata version/readiness marker, preserving the existing principle that in-transition documents are ineligible. Database-resolved document IDs remain the authority for each search.
5. Switch new clients to the new contract; remove mandatory category writes. Retain old data only for the migration/rollback window, then retire old fields after old jobs and clients are drained.

Source authorization is checked at execution and download time. A scope change invalidates incompatible cached context; facts from a previously selected scope must not silently support a new answer. Evidence snapshots preserve source versions for reproducibility, but never override a later permission revocation.

## Chat and downloadable documents

Flow: persist message → load conversation context and current source scope → agent retrieves evidence and answers or creates a task → persist response/task reference → stream completion.

Document tasks capture the conversation brief, source snapshot, and template version. Generate structured sections, validate factual citations and required sections, then render to DOCX/TXT through application code. Return an artifact card/download, not a fabricated URL or raw filesystem path. User edits create a new document revision; downloads remain tied to their revision.

Define 輿情／立院／備參 template schemas from PM-approved examples; do not invent official formatting requirements. A template can specify section order, tone, headings, and citation placement. 科別 guidance can specialize terminology and work context independently.

Preserve evidence requirements for factual answers. Clarification questions and editing acknowledgments can have no citations; they should not be replaced by the current blanket insufficient-evidence fallback. Unsupported factual claims must still be withheld or explicitly flagged.

## Presentation planning and approval

Flow: selected sources → frozen evidence → outline revision → user discussion/edits → new revision → validation → explicit confirmation → author → deterministic validation → independent review/correction → published PPTX.

An outline contains title, audience, purpose, slide sequence, per-slide objective/key points, evidence references, and unresolved questions. Store both the structured outline and a readable preview. Chat revisions and direct edits use the same revision API and optimistic concurrency check.

Validation checks schema, slide-count constraints, evidence-reference validity, unresolved blockers, and factual support. An LLM review can assist with support assessment; deterministic checks cannot establish semantic truth alone. Persist the validation result for the exact revision.

`POST /presentation-plans/{id}/confirm` carries the expected revision and idempotency key. In one database transaction, verify ownership, current revision, valid evidence, and successful validation; record approval and create a unique author job plus outbox entry. An outbox dispatcher delivers to Taskiq; worker claiming remains idempotent. This handles a process crash between database commit and queue publication.

The worker loads the approved revision and snapshot by ID/hash. It never consumes a mutable “latest outline.” Reject missing approval, mismatched hashes, stale revisions, or revoked sources. Confirmation retries return the same job; stale confirmation returns 409.

Edits after confirmation create a new unapproved revision. The previous job retains its approved snapshot; the user can cancel it or approve a replacement. Review repairs must preserve the approved content and ordering. A material change returns to planning for a new user confirmation.

Waiting for human input is a persisted task state, not a running worker. Prefer ending the planning SDK turn and starting the author run after confirmation. SDK interrupted-run state is available for individual tool approvals, but is not needed to keep an outline discussion alive across days.

## API and frontend boundary

Suggested versioned endpoints (names remain implementation choices):

- `/conversations`: create/list; `/{id}` retrieve/rename/archive; `/{id}/messages` list and submit turns.
- `/runs/{id}`: status; `/events` reconnectable events; `/cancel` cancellation request.
- `/departments` and document department associations: catalog and source organization.
- `/output-templates`: discover available document formats and versions.
- `/conversations/{id}/document-tasks`: create a downloadable-document task.
- `/conversations/{id}/presentation-plans`: create a planning task.
- `/presentation-plans/{id}/revisions`: inspect/edit; `/confirm`: approve an exact revision.
- `/artifacts/{id}/download`: authorized artifact retrieval.

Expose application events such as message delta/completed, task status, outline revised, confirmation required, artifact ready, and failure. Map SDK events internally. Durable event sequence IDs or persisted snapshots support reconnect; Redis telemetry alone cannot be the recovery record.

Frontend work is necessary: conversation list/history, department source selection, independent output format selection, document downloads, editable outline preview, and a confirmation button bound to the displayed revision. Existing direct slide-generation entry points must be gated or retired at cutover so they cannot bypass approval.

## SDK migration and implementation sequence

1. **Contracts and persistence:** define schemas and ownership; add migrations, conversation APIs, run/event records, idempotency, and session storage. Exit: restart-safe conversation history and ordered concurrent submissions.
2. **科別 migration:** add mapping/reconciliation tools and new retrieval contracts. Exit: department search works through ingestion and reindexing, without category coupling.
3. **SDK chat and text tasks:** add an `OpenAIAgentsRunner` implementation and register it server-side; introduce constrained tools, typed output templates, artifact rendering, and reconnectable events. Exit: grounded follow-up QA and revision-correct downloads.
4. **Presentation planning:** reuse extraction to freeze evidence before planning, add outline revisions and approval/outbox dispatch, and update the author input contract. Exit: no author activation before exact-revision confirmation.
5. **Author/reviewer migration:** move remaining agent stages to SDK execution, retaining validators and bounded review attempts. Start behind a per-workflow switch. Exit: quality and isolation parity on representative documents and decks.
6. **Cutover and cleanup:** switch UI/contracts, retire legacy bypasses and category fields after migration, and remove obsolete runner paths only when no active jobs need them.

The Agents SDK does not automatically reproduce the Codex runtime's shell, staged skills, rendering tools, or filesystem isolation. Implement explicit tools or a constrained execution environment for those capabilities and verify parity. A temporary Codex-backed author adapter can support phase 4; phase 5 completes the intended SDK migration. Pin and test the actual SDK version instead of relying on an unbounded lower dependency constraint.

Keep orchestration ownership clear: application code schedules durable stages and enforces policy; SDK runs perform individual agent stages. Avoid overlapping retry loops by distinguishing model/tool retries, bounded author corrections, and queue redelivery. Tool side effects and publication use durable idempotency keys.

## Verification and rollout gates

- Persisted history and relevant context survive browser refresh, API restart, and worker replacement.
- Duplicate requests, two simultaneous turns, stream disconnects, failed tools, and expired worker leases do not duplicate messages or artifacts.
- Search cannot escape resolved source IDs; department transitions, shared sources, unmapped documents, empty scopes, and revoked access behave deterministically.
- The same evidence can produce different templates without changing knowledge classification.
- Outline revision N cannot be confirmed after N+1 exists; repeated confirmation queues one job; a queue outage after approval recovers via the outbox.
- Author output follows the approved outline and frozen evidence; unsupported claims and material deviations block publication or require renewed confirmation.
- DOCX/TXT content, citations, filenames, and download authorization are verified; PPTX rendering/content checks and existing independent review still pass.
- Evaluate representative Traditional Chinese QA, follow-ups, conflicting/insufficient sources, template tasks, outline revisions, and decks against the current baseline. Record quality, latency, token usage, and cost before cutover.

Use per-workflow rollout switches. Drain existing jobs under their original runner/config versions. Rollback changes runtime selection without deleting newly stored conversations, approvals, or artifacts. No production data migration or provider execution was performed during this planning task.

## Official documentation checked

- [Agents SDK overview](https://developers.openai.com/api/docs/guides/agents/sdk)
- [Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents): runs, streaming, and alternative conversation continuation strategies.
- [Results and state](https://developers.openai.com/api/docs/guides/agents/results): Python result/history surfaces and resumable interruption state.
- [Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration): specialist ownership versus agents used as bounded tools.

These sources establish SDK capabilities. The database schemas, application approval transaction, migration stages, and endpoint designs above are proposed architecture for this repository.
