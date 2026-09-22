# Agents SDK migration and the slide planning phase

Status: execution plan, 2026-09-19. Scope is the `slides` and `news` agentic workflows in `backend/`.

This is the narrow, file-level companion to [`backend-agents-refactor-plan.md`](./backend-agents-refactor-plan.md).
That document owns the product contract, the 科別 taxonomy, conversations, and output
templates. This one owns two things only:

1. replacing the bespoke Codex execution layer with the OpenAI Agents SDK, and
2. inserting an interactive planning phase between extraction and authoring.

Where the two disagree, the broader plan wins on product contracts and this one wins on
file-level sequencing.

## Current state

`POST /slides/jobs` → `SlideJobService` (durable `slide_jobs` row) → TaskIQ `agents.run`
(`app/tasks/agents.py:118`) → `execute_workflow` → the hand-rolled state machine in
`app/services/agentic/service.py`:

```
PREPARING → EXTRACTING → DRAFTING → VALIDATING → REVIEWING → REVISING* → PUBLISHING
             (frozen        (author   (determin-   (semantic   (bounded loop,
              EvidenceStore)  agent)    istic)      reviewer)   stagnation-capped)
```

The framework under `app/services/agentic/` is roughly 3,800 lines:

| File | Lines | Responsibility |
| --- | --- | --- |
| `service.py` | 1308 | the state machine, plus a second legacy turn-callback path |
| `runner.py` | 728 | `CodexRunner` over `openai_codex.AsyncCodex`; threads, turns, streamed events, bwrap isolation, audit persistence |
| `contracts.py` | 713 | `WorkflowAdapter` protocol, review identity/stagnation policy, `AgentPhase` |
| `events.py` | 735 | Redis telemetry store |
| `registry.py`, `coordinator.py`, `staging.py` | 215 | workflow allowlist and skill staging |

The medium of exchange is a filesystem workspace (`input/`, `work/evidence.json`,
`work/extracted/`, `work/rendered/final/`, `output/presentation.pptx`). Isolation is
per-stage through `stage_hidden_paths` / `stage_read_only_paths` /
`stage_writable_paths` (`app/services/slides/adapter.py:212-262`) enforced with bwrap.
Any migration must preserve that isolation exactly.

## Why the migration is parity, not a downgrade

`openai-agents>=0.22.2` is already declared in `backend/pyproject.toml` and installed.
Verified against the installed package rather than from memory, it provides:

- `agents.sandbox` — `SandboxAgent`, capabilities `Filesystem` / `Shell` / `Skills`
  (with a `load_skill` tool) / `Memory` / `Compaction`, `SandboxPathGrant`,
  `SandboxWorkspaceScope`, and `unix_local` plus `docker` backends.
- `output_type` structured outputs, handoffs, input/output guardrails, tracing.
- `agents.memory` — `SQLiteSession`, `OpenAIConversationsSession`.
- `RunState` with `to_json()` / `from_json()` and `interruptions` / `ToolApprovalItem`.

The staged-skills, sandbox, and path-grant model built by hand on Codex + bwrap exists
natively. `app/services/agentic/test_new_agent.py` is an existing scratch sketch of the
same four-agent shape; it is not production wiring and is removed in Stage 0.

## Target architecture

Keep the adapter / registry / phase / telemetry boundary. Replace only the execution
layer. The `AgentRunner` protocol (`app/services/agentic/contracts.py:524`) is already
provider-neutral and `RunnerRegistry` already exists, so the seam is a drop-in:

```python
RunnerRegistry({"codex": CodexAgentRunner(...), "agents": AgentsSdkRunner(...)})
```

Each adapter selects a runner per node (`extraction_runner`, `author_runner`,
`reviewer_runner`), so nodes migrate one at a time and roll back by configuration.

Kept unchanged, because it is domain policy rather than framework:
`evaluate_review`, `normalize_review_outcome`, `review_finding_identity`, the stagnation
limit, the frozen-EvidenceStore contract, `app/services/slides/validation.py`, the
`AgentPhase` vocabulary, and the Redis telemetry and TaskIQ progress surfaces. Only the
*source* of lifecycle events changes.

## Stage 0 — runner seam, no behaviour change

New `app/services/agentic/sdk_runner.py`:

```python
class AgentsSdkRunner:  # implements AgentRunner (contracts.py:524)
    async def run(
        self,
        request: AgentExecutionRequest,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> AgentExecutionResult: ...
```

It builds a `SandboxAgent` per request from `request.model`, `reasoning_effort` and
`skill_names`; translates `hidden_paths` / `read_only_paths` / `writable_paths` into
`SandboxPathGrant` and `SandboxWorkspaceScope`; runs `Runner.run_streamed`; and maps SDK
stream events onto the existing `ProgressReporter.emit` and `TurnAudit` shapes so
`events.py` and TaskIQ progress stay untouched.

Changed: `_build_runner_registry()` (`app/tasks/agents.py:48`) registers both runners.
New settings `agent_extraction_runner`, `agent_author_runner`, `agent_reviewer_runner`
default to `"codex"`, so a node flips by configuration rather than by deploy.

Removed: `app/services/agentic/test_new_agent.py`. It sits under a `test_*.py` name
inside the application package, calls `load_dotenv()`, and reaches the network.

Tests: `tests/services/agentic/test_framework.py` and `test_process_isolation.py` gain a
runner-parametrised fixture so both runners execute identical fixtures.

**Exit criterion:** no production behaviour change; both runners green on the same fixtures.

### Stage 0 outcome (landed 2026-09-19)

Delivered: `app/services/agentic/sdk_runner.py` (`AgentsSdkRunner`, `name = "agents"`),
registry wiring, the three settings, and `tests/services/agentic/test_sdk_runner.py`.
`pytest tests/services/agentic/ -q` → 64 passed.

Two findings from reading the installed SDK changed the design from this document's
original wording:

1. **`hidden_paths` has no positive representation.** `SandboxPathGrant` only grants
   paths *outside* an already-scoped manifest root; it has no subtractive counterpart to
   bwrap's `--tmpfs` / `--ro-bind` remounts, so a path already inside the root cannot be
   masked. The runner therefore honours `restrict_workspace` by choosing *where the root
   points*: `True` → a sandbox-virtual root with nothing mounted, so only granted
   `read_only_paths` / `writable_paths` are visible and a path is hidden by never being
   granted. This is a true allowlist and is stronger than masking. `restrict_workspace=False`
   leaves the whole workspace visible and grants become decorative — acceptable only
   because both `slides_adapter` and `news_adapter` set `stage_isolation = True`, which
   makes `restrict_workspace` true for every production stage. **Any new adapter that
   omits `stage_isolation` silently loses stage isolation on this runner.**
2. **`output_schema` does not pass through.** Codex's raw per-turn JSON schema has no
   `SandboxAgent` equivalent (`output_type` wants a Python type). The runner raises
   rather than silently ignoring it. Stage 2's `output_type=ReviewOutcome` is the fix.

Also noted for Stage 4: `sdk_runner.py` imports `ProgressReporter`, `safe_error` and
`_turn_phase` from `runner.py`. When `runner.py` is dismantled these three must move to a
shared module rather than being deleted. `AgentExecutionResult.provider_run_id` is now
the job's own run id rather than a provider thread id; telemetry consumers should not
read provider meaning into it.

`tests/services/agentic/test_process_isolation.py` was left unparametrised: it exercises
`build_bwrap_launch_args` directly, which is Codex-only and has no runner-neutral
equivalent. It is deleted in Stage 4, not migrated.

Unrelated pre-existing bug, confirmed reproducible on a clean tree: several tests build
`CodexRunner(api_key="key")` with a plain `str` where `api_key` is `SecretStr`. Fixed in
`test_framework.py`; `tests/tasks/test_agents.py::test_worker_builds_allowlisted_codex_runner_with_default_model`
still fails and is out of scope here.

## Stage 1 — extraction node

Flip `extraction_runner = "agents"`. One turn, file output into `work/extracted/`, no
structured output, narrowest grants (`input/` read-only, `work/extracted/` writable).
This proves the `Skills` capability can load the staged
`source-document-extraction/SKILL.md` and that `post_extraction` →
`consolidate_evidence` still produces an equivalent `work/evidence.json`.

**Exit criterion:** semantically equivalent evidence store on a fixture corpus.

## Stage 2 — reviewer node

Flip `reviewer_runner = "agents"` and give the reviewer `output_type=ReviewOutcome`.

Deleted: `review_output_schema` and `parse_review` in `app/services/slides/adapter.py`
(the duplicated JSON schema at `:120-160` and the parser at `:433-518`), and
`_parse_review` in `service.py`. The SDK returns a validated `ReviewOutcome`; the
existing policy still decides publish / retry / reject.

The reviewer sandbox is read-only, with grants limited to `evidence.json`,
`deck_snapshot.json` and `rendered/final` — already expressed at `adapter.py:231-249`.

**Exit criterion:** `tests/services/agentic/test_review_policy.py` passes unmodified.

### Stages 1-2 outcome (landed 2026-09-19)

`pytest tests/services/agentic/ tests/services/slides/ -q` → 125 passed, 3 skipped.
`test_review_policy.py` passes unmodified. `slides/adapter.py` fell from 572 to 457
lines: `review_output_schema` (~41) and `parse_review` (~85) deleted, along with
`_parse_review` in `service.py` (~15). `evaluate_review`, `normalize_review_outcome`,
`review_finding_identity` and the stagnation policy are unchanged.

`extraction_runner` and `reviewer_runner` are now properties reading
`settings.agent_extraction_runner` / `agent_reviewer_runner`, both defaulting to
`"codex"`. Typed output flows as `AgentExecutionRequest.output_type` →
`AgentExecutionResult.output`.

One deliberate scope expansion: `CodexAgentRunner` also gained typed-output support
(deriving `TurnRequest.output_schema` from `output_type.model_json_schema()` and
validating the response). This is ~44 lines in a file Stage 4 deletes, accepted because
it let `parse_review` be removed cleanly now instead of half-deleted. The legacy
turn-callback branch's one remaining parse call site was inlined rather than kept as a
shared function, since Stage 4 removes it wholesale.

**Open manual gate — Stages 1 and 2 are structurally complete but not behaviourally
verified.** Evidence-store equivalence and real structured review output both require a
live model run with an API key. Neither `agent_extraction_runner` nor
`agent_reviewer_runner` should be flipped to `"agents"` in any real environment until
that run is done, and **Stage 3 should not start until it is** — flipping the hardest
node while the two easy ones are unproven would build on unverified ground.

## Stage 3 — author node

The long pole. It needs `Shell` + `Filesystem` + `Skills` with python-pptx, the bundled
chart-image script, LibreOffice rendering, `FONTCONFIG_FILE` and `PPTX_CJK_FONT` from
`build_job_environment`, and offline package installation (`PIP_NO_INDEX`, `UV_OFFLINE`).

Prefer the `docker` sandbox backend over `unix_local` here; the repository already ships
`docker-compose.yml` and it replaces bwrap cleanly. Verify against
`tests/services/slides/test_render_slides.py` and `test_chart_deck_integration.py`
before flipping.

If parity cannot be reached, Stages 0–2 still stand and the author stays on `codex`
through the registry. That is a configuration choice, not a rewrite.

## Stage 4 — collapse

Delete the legacy `elif build_review is not None:` branch (`service.py:1063-1270`),
`_uses_execution_request`, `_as_execution_result`, the `modern_runner` detection, the
bwrap helpers (`runner.py:49-169`), `CodexRunner`, `CodexAgentRunner`, and
`run_codex` in `app/services/slides/runtime.py`. Drop `openai-codex` from
`pyproject.toml`. `news_adapter` then migrates by changing three class attributes.

Expected: `service.py` from ~1308 to ~600 lines; `runner.py` folded away.

## Stage 5 — planning phase

Placement is extraction → **planning** → author. The outline must be grounded in
already-frozen evidence; a plan built before extraction would be guessing.

Waiting for a human is a persisted task state, never a blocked worker.

### Contracts (`app/models/slides.py`)

```python
class OutlineNode(BaseModel):
    id: str
    heading: str
    intent: str                                    # what this section must accomplish
    key_points: list[str]                          # 2-5, evidence-grounded
    evidence_refs: list[str]                       # ids from the frozen EvidenceStore
    emphasis: Literal["light", "normal", "deep"]   # the user's tuning knob
    approx_slides: int                             # a hint, not a page-by-page breakdown


class SlideOutline(BaseModel):
    title: str
    narrative: str                                 # 1-3 sentences: the through-line
    nodes: list[OutlineNode]
    total_slides: int
```

Granularity is deliberately medium. `emphasis` and `approx_slides` let a user say "go
deeper here" without dictating a per-page structure.

New `AgentPhase.PLANNING` and `AgentPhase.AWAITING_OUTLINE`; new
`JobStatus.AWAITING_INPUT`.

### Schema

New Alembic revision adding `slide_outlines`: `job_id` foreign key, `revision`,
`outline` JSON, `session_id`, `created_at`, `approved_at`. `SlideJob` is unchanged apart
from the new status and phase strings.

Outline revisions are immutable and append-only. Revision *N* cannot be approved once
*N+1* exists; approval carries the expected revision and returns 409 when stale.

The planner's conversation lives in a persisted Agents SDK `Session` keyed by
`session_id`. Serialised `RunState` is deliberately **not** used: it exists for resuming
mid-tool-call interruptions, and an outline discussion that stays open for days is
better served by a persisted session plus immutable revisions.

### Worker

In `service.py`, after `_execute_extraction`, when the adapter declares a planning agent:
run it with `output_type=SlideOutline`, persist revision 1, emit `awaiting_outline`, and
return a non-terminal `AgentTaskResult`. `app/tasks/agents.py` must release the lease
without marking the job failed.

On re-enqueue with `resume_from="author"`, extraction and planning are skipped and the
frozen `work/outline.json` is read.

### API (`app/api/routes/slides.py`)

- `GET /slides/jobs/{id}/outline` — latest revision.
- `POST /slides/jobs/{id}/outline/messages` — SSE. Rehydrates the session, streams the
  planner's reply, persists revision *n+1*. Mirrors `_stream_with_heartbeat` in
  `app/api/routes/chat.py:108`.
- `POST /slides/jobs/{id}/outline/approve` — carries the expected revision and an
  idempotency key. In one transaction: verify the revision is current, record approval,
  write `work/outline.json`, enqueue `agents.run` with `resume_from="author"`.

### Author integration

`work/outline.json` is added to the author's `stage_read_only_paths`. `build_prompt`
instructs node-order adherence and `emphasis` weighting. An output guardrail on the
planner rejects any `evidence_refs` id absent from `work/evidence.json`.

`app/services/slides/validation.py` gains a deterministic check that every outline node
id appears in the deck snapshot and that `total_slides` matches `slides_count`, so a
drifting author fails the existing validate-and-revise loop rather than silently
discarding the user's plan.

### Frontend

`AgentJobPhase` gains `planning` and `awaiting_outline`; `useAgentJob` stops polling and
surfaces the outline; a new `OutlineReview.tsx` (node list with emphasis controls plus a
chat panel) sits between `SlideSettings` and `SlideGenerationStatus`.
`analyzeSlideReadiness` is unaffected.

### Stage 5a outcome (landed 2026-09-19)

Data layer only. `OutlineNode` / `SlideOutline` in `app/models/slides.py` with bounds
consistent with sibling contracts (`key_points` 2-5, `nodes` 1-30, `approx_slides` 1-25,
unique node ids); `AgentPhase.PLANNING` / `AWAITING_OUTLINE`; `JobStatus.AWAITING_INPUT`;
migration `783d53229171` (chains from `d91e3f4a5b6c`, now head) creating `slide_outlines`
with a unique index on `(job_id, revision)`; and `app/services/slides/outline_repository.py`
with `create_next_revision` / `get_latest` / `get_revision` / `approve`. No update method —
append-only by construction. `StaleOutlineRevisionError` carries both the expected and
latest revision and is the 409 case.

Field bounds were chosen before any real planner output existed; revisit once Stage 5b
has observed live model output.

### Stage 5b outcome (landed 2026-09-19)

Planner node, worker pause/resume, and the three outline endpoints.
`pytest tests/ -q` → 288 passed, 3 skipped, plus the two known pre-existing failures.

Gating is stricter than this document originally specified. Planning inserts a
product-visible human pause, not merely a runner choice for an existing stage, so it sits
behind `settings.agent_planner_enabled` (default `False`) as well as the adapter's
`planner_role` hook. With the flag off, slides runs byte-for-byte as before.

`_execute_planning` mirrors `_execute_extraction`: one request with
`output_type=SlideOutline`, `Sandbox.read_only`, `restrict_workspace=True`, and a
read-only grant on `work/evidence.json`. Persistence goes through a new `post_planning`
adapter hook so `service.py` stays workflow-neutral. The session id is the job id — one
planner session per job, reused across every outline turn.

The pause path: `agents.run` intercepts `status=RUNNING, phase=AWAITING_OUTLINE` before
the terminal path and calls `SlideJobRepository.pause_for_outline_approval()`, which sets
`AWAITING_INPUT`, clears the lease, and deliberately leaves `finished_at` unset. Workspace
cleanup is skipped so the frozen evidence survives for resume.

Approval concurrency rests on `outline_repository.approve()`'s `SELECT ... FOR UPDATE`
(`outline_repository.py:204`). `OutlineAlreadyApprovedError` — defined but unused in
Stage 5a — turned out to be exactly the idempotency primitive, so `(job_id,
expected_revision)` is itself the retry key. The separate `idempotency_key` wire field
was removed: it was required but decorative, and implied a guarantee the tuple already
provides more strongly.

The former approval crash window is closed by migration `f46c2a84e1d3`: the approval and
one unique `(job_id, revision)` outbox row commit together. Immediate delivery plus a
60-second recovery task writes the exact approved outline, publishes `agents.run`, and
marks the event delivered only after TaskIQ accepts it. A crash after publication may
publish again; the existing durable worker claim is the idempotency boundary.

The other Stage 5b deferrals are also closed. Validation matches each outline node id as
an exact standalone speaker-notes line in the OOXML snapshot, and the abandoned-outline
TTL sweep expires still-parked jobs without racing a concurrent resume.

### Stage 5c outcome (landed 2026-09-20)

The frontend now understands `planning` / `awaiting_outline`, stops polling while a job
is parked, and renders `OutlineReview` with narrative, nodes, emphasis controls, planner
chat and approval bound to the displayed revision. A stale 409 refetches and explains
the new revision without approving it. Approval resumes polling only after the user has
approved the revision they saw. Refreshing an already-approved but not-yet-dispatched
job also resumes polling for outbox recovery. `npm run typecheck` and all 66 frontend
tests pass.

The production frontend build could not be verified in this environment: Turbopack is
not permitted to bind its internal port, while the webpack fallback fails in Next's
TypeScript `--showConfig` parser before application compilation.

### Decision: planner evidence access

The planner reads `work/evidence.json` through a read-only sandbox grant, exactly as the
author does. It does **not** receive inlined evidence and does **not** use `file_search`.
The evidence store is already frozen, consolidated and local; a vector store would add an
indexing dependency and latency for content already on disk, and would break the
guarantee that planner and author see byte-identical evidence. This closes the second
open decision below.

**Superseded for the "agents" runner path (2026-09-22).** This decision still holds
exactly as written for `planner_runner == "codex"`: the Codex planner keeps reading
`work/evidence.json` through its bwrap sandbox unchanged. It does not hold for
`planner_runner == "agents"`: that path instead receives a compact, delimited rendering
of the same frozen evidence directly in its input. See "Architecture decisions
(2026-09-22)" below for why a sandbox grant turned out to be the wrong shape for that
runner specifically.

## Running the planner on Gemini

Set `AGENT_PLANNER_ENABLED=true`, `AGENT_PLANNER_RUNNER=agents`,
`AGENT_PLANNER_MODEL=litellm/gemini/<model-id>`, and `GEMINI_API_KEY` in
`backend/.env`; keep the existing required `OPENAI_API_KEY` setting. The SDK
routes the `litellm/` prefix, while the application passes the configured
Gemini key explicitly, requests usage, and disables SDK tracing for that run.
For a later Claude trial, use `litellm/anthropic/<model-id>` and
`ANTHROPIC_API_KEY` instead. Bare model names still use the existing OpenAI
path. Do not approve the outline during a planner-only trial: the job remains
parked and the author never runs.

Read the initial planner turn's token counts in
`<AGENT_JOBS_ROOT>/<job-id>/work/agents/planning/attempt-1.json`, under
`turns[0].usage` (`input_tokens`, `output_tokens`, and `total_tokens`). Outline
chat turns currently do not persist a turn audit. This path has not been run
live with Gemini. The first-run risk is `SlideOutline` structured output:
some providers reject strict JSON schema or return malformed JSON, and the
SDK documents no reliable fallback. Validate Gemini's response and usage
before relying on cost estimates.

### First live runs (2026-09-22) and the next step

These were the first live runs of `AgentsSdkRunner` for any node. Both failed
before Gemini was called, so they cost only the extraction.

1. **Job `f18fdd82`: "hidden stage path escapes the workflow workspace".** The
   slides adapter hides the original source documents on the shared documents
   volume, outside the job workspace, so that no stage after extraction can
   reread them. `_build_path_grants` required every hidden path to be inside the
   workspace. Fixed: in this sandbox nothing is visible unless granted, so a hidden
   path is invisible by construction and may lie anywhere. The grant check was also
   strengthened from exact equality to containment in both directions.
2. **Job `dca3530b`: "Sandbox execution requires `run_config.sandbox.client`".**
   The runner builds a sandbox policy but never supplies a sandbox backend
   (`sandbox_client` defaults to `None`). Unit tests used fakes, so this surfaced
   only live. **Not yet fixed**; see the next step below.

**Security finding before supplying a backend.** The installed SDK offers
`UnixLocalSandboxClient` (runs in the worker container) and `DockerSandboxClient`
(needs a Docker daemon the worker does not have). Reading `unix_local.py`:

- it **inherits the worker's full environment by default**
  (`inherit_host_environment=True`), including `OPENAI_API_KEY`, `GEMINI_API_KEY`
  and the database URL;
- **on Linux it does not confine shell commands**: its only isolation mechanism is
  `sandbox-exec`, which exists only on macOS;
- the SDK's default capabilities are `Filesystem`, `Shell` and `Compaction`.

So wiring the local backend with defaults would give the planner an unconfined
shell holding the API keys: a prompt injection in an uploaded document could read
the keys or the original sources. The Codex runner prevented this with bwrap and
by stripping credentials from the environment.

**Superseded: the "make `Filesystem` the only default capability" plan below was the
original next step drafted right after the live-run failures above, before the
`Filesystem` capability itself was read.** Reading
`agents/sandbox/capabilities/filesystem.py` in the installed SDK (0.22.2) shows it
exposes exactly two tools: `view_image` and `apply_patch`. There is no text-read tool.
Restricting the planner to `Filesystem` alone, as the crossed-out plan below proposed,
would not have let it read `work/evidence.json` at all -- `apply_patch` is a write-shaped
diff tool, not a read primitive. Reading a JSON file's contents at all, on this SDK
version, requires `Shell`. So dropping `Shell` to close the "unconfined shell holding API
keys" hole (previous section) would have reopened the planner's only reason to have
sandbox access in the first place: it cannot read anything without a capability this SDK
does not offer for plain text.

~~**Decided next step for the planner:**~~
~~- make `Filesystem` the only default capability, so `Shell` must be requested~~
~~  explicitly by a node that has a confining backend;~~
~~- supply `UnixLocalSandboxClient(inherit_host_environment=False)`;~~
~~- add a real-sandbox test (no model call) proving the file tools refuse an~~
~~  ungranted path such as an original source document.~~

**Actual decision: run the planner as a plain agent -- no sandbox, no tools at all.** The
planner reads one known file and returns a structured outline; it needs no tools to do
that. `AgentsSdkRunner` now treats "no path grants" (`read_only_paths` and
`writable_paths` both empty) as a distinct, simpler case: it builds a plain `agents.Agent`
instead of a `SandboxAgent` -- no `Manifest`, no `SandboxRunConfig`, no
`sandbox_client` to select or defend at all, so the `UnixLocalSandboxClient` environment
and Linux-confinement problems above never apply to this node. The frozen evidence goes
into the prompt instead, rendered compactly by `render_compact_evidence`
(`app/services/slides/evidence.py`): per block, only `id` (needed for `evidence_refs`),
`text`, and a citation label survive, with the source shown by its knowledge-base
`display_name` (`work/sources.json`) rather than a staged filename, hash, or provenance
internal.

This is architecturally standard, not a workaround forced by the missing read tool: a
text-read tool, had one existed, would only have put the same file's bytes into the
model's context after an extra tool-call round trip -- the model cannot use file contents
without them entering its context one way or the other. For evidence that is already
frozen, consolidated, and known not to change mid-run, putting it in the input directly
is the same information delivered in fewer turns. Task instructions and the evidence stay
structurally separate to keep this safe: instructions go on the agent's own
`instructions` (a new `AgentExecutionRequest.instructions` field, `agents`-path only),
and the evidence occupies its own `<evidence>...</evidence>` block with an explicit
sentence that it is source material to cite, never instructions to follow -- uploaded
documents are untrusted input. `agent_planner_max_evidence_chars` (default 150,000 --
measured real documents run 25k-93k characters of block text) fails the turn fast with a
clear message rather than silently truncating evidence a planner would then cite
incompletely. The Codex planner is entirely unaffected: `planner_runner == "codex"` still
reads `work/evidence.json` through its bwrap sandbox exactly as before, and the default
runner for every node remains `"codex"`.

**Consequence for Stage 3 (the author).** The author needs a shell (python-pptx,
chart rendering, LibreOffice). The local backend cannot confine one on Linux, so
**the author must not run on `UnixLocalSandboxClient`**. Moving it to the SDK
requires the Docker backend, with a Docker daemon available to the worker, or an
equivalent confinement such as bwrap.

### Architecture decisions (2026-09-22)

- **Reasoning nodes use plain SDK calls, not sandboxed tool use.** The planner (this
  stage's subject) only ever reads one already-frozen artifact and returns a structured
  judgment; it needs no tools once that artifact is in its input, so it now runs on the
  plain-agent path `AgentsSdkRunner` takes for any request with no path grants. The
  reviewer is the same shape in spirit -- it too only reads frozen artifacts and returns a
  structured verdict -- but this change does not touch it: its grants
  (`work/evidence.json`, `work/intermediate/deck_snapshot.json`,
  `work/rendered/final`) are unchanged, and its rendered-PNG review specifically needs
  `Filesystem`'s `view_image` tool, so moving it to the plain path later is a smaller
  step (drop `Shell`, keep `Filesystem`) rather than the planner's full "no tools at all".
- **The author stays on Codex for now.** It needs a real shell (python-pptx, chart
  rendering, LibreOffice), and the Agents SDK's only local sandbox backend cannot confine
  one on Linux (see "Consequence for Stage 3" just above) -- moving the author means
  either the Docker sandbox backend (needs a Docker daemon the worker does not have) or
  wiring it through the same bwrap isolation Codex already provides.
- **If the author ever moves off Codex, Pi is an option.** Pi is a TypeScript
  coding-agent harness; run inside the existing bwrap isolation through this runner's
  seam -- not through `UnixLocalSandboxClient` -- it would not need a new sandbox backend
  built and trusted before the author could carry a real shell on the Agents SDK.
- **Strands Agents is the fallback if LiteLLM routing proves unreliable.** This stage's
  Gemini routing goes through the SDK's `litellm/` prefix and a keyed `LitellmModel`
  provider (see "Running the planner on Gemini" above). Strands Agents has native
  Gemini and Anthropic support, so if LiteLLM's translation layer turns out to be
  unreliable in practice, Strands avoids depending on it at all rather than trying to
  work around it.
- **The author must not run on `UnixLocalSandboxClient`.** Restated here because it is
  the one hard constraint the rest of this list depends on: that backend inherits the
  worker's environment by default (API keys included) and does not confine shell commands
  on Linux at all, so any node that needs a real shell must reach isolation some other
  way -- Docker, bwrap, or a harness that brings its own, never this backend with its
  defaults.

## Risks

1. **Author-node parity is the long pole.** Mitigated by the runner seam: if the SDK
   sandbox cannot reproduce the offline, fontconfig and LibreOffice environment, the
   author stays on `codex` by configuration.
2. ~~**Lease semantics.**~~ **Resolved in Stage 5b, and this risk was misdiagnosed.**
   `backend/scripts/reconcile.py` reconciles OpenAI vector-store attachments and has
   nothing to do with slide-job leases; no lease-sweep for slide jobs exists anywhere in
   the repository. The real mechanism is `SlideJobRepository.claim`, which now refuses to
   reclaim a job in `AWAITING_INPUT` unless the caller passes
   `allow_resume_from_awaiting_input=True` — set only by `agents.run` when
   `payload.resume_from == "author"`. A duplicate delivery therefore falls through the
   existing "not claimed" branch and returns the paused phase untouched.
3. **Abandoned outlines.** A job stuck in `awaiting_input` holds a workspace under
   `agent_jobs_root` indefinitely. Needs a TTL and cleanup, most likely in
   `app/scheduler.py`.
4. **Unbounded SDK constraint.** `openai-agents>=0.22.2` should be pinned before Stage 3;
   the sandbox API surface is new and moves.

## Open decisions

- Model assignment per node (extractor / planner / author / reviewer). The existing
  scratch sketch used `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-sol`.
- Whether the planner gets `file_search` over the evidence store or receives the
  consolidated JSON in-prompt.
