# Architecture observations — 2026-09-21

Notes captured while the context was fresh, as a starting point for a design session.

**This is not a plan.** It records what is measurably true about the agentic layer today
and the questions worth deciding together. It deliberately stops short of proposing a
target architecture, because that is the conversation this is meant to seed.

Related: [`agents-sdk-migration-plan.md`](./agents-sdk-migration-plan.md) (what landed),
[`9_19_implementation_plan.md`](./9_19_implementation_plan.md) (remaining work).

---

## The central measurement

In `backend/app/services/agentic/service.py`:

| Measure | Count |
| --- | --- |
| `getattr(adapter, ...)` call sites | **57** |
| Distinct adapter hooks probed dynamically | **44** |
| Methods declared on the `WorkflowAdapter` Protocol | **14** |
| Defaults supplied by `BaseWorkflowAdapter` | **20** |

The coordinator depends on 44 named attributes. Fourteen are declared in the Protocol.
**Thirty are contract in practice but documented nowhere**, discoverable only by reading
`service.py` and noticing which strings appear inside `getattr`.

The full probed set:

```
author_model author_reasoning_effort author_role author_runner
build_extraction_prompt build_planning_prompt build_review_prompt
build_revision_feedback build_semantic_review_context consolidate_extraction
declared_skills deterministic_validate extraction_model
extraction_reasoning_effort extraction_role extraction_runner extraction_skills
independent_semantic_review max_author_attempts max_review_rounds output_type
parse_review planner_model planner_output_type planner_reasoning_effort
planner_role planner_runner post_author_completion_check post_extraction
post_planning review_output_schema review_stagnation_limit reviewer_model
reviewer_reasoning_effort reviewer_role reviewer_runner revision_feedback
semantic_review_context stage_hidden_paths stage_isolation
stage_read_only_paths stage_writable_paths validate_deterministically
validate_generated
```

A Protocol exists, but the caller does not trust it. Every hook is treated as optional and
probed defensively — which gives neither a static guarantee nor runtime clarity. And
`BaseWorkflowAdapter` already supplies defaults for most of them, so much of the
defensiveness is vestigial rather than load-bearing.

---

## Two seams, compared — neither is a reference design

The existing code was largely generated rather than hand-designed, so it should be treated
as evidence of what happened, not as a model of what should be. That caveat matters for
the comparison below.

Two Protocols exist in the same package, and they have fared differently.

**`AgentRunner` — narrow, and it held once.** Stage 0 added `AgentsSdkRunner` under a hard
constraint that `service.py`, `contracts.py` and `events.py` stay untouched, and the
constraint held: a new class implementing `run()`, registered by name, selected per node
by configuration. That is one verified fact.

It is **not** evidence of good design. `service.py` still carries
`_uses_execution_request()` (inspects a runner to decide which calling convention it
speaks), `_as_execution_result()` (normalizes "provider/fake results at the coordinator
boundary"), and a `modern_runner` heuristic that sniffs the runner object's shape to pick a
code path. The caller does not fully trust this Protocol either. `AgentRunner` is less
compromised than `WorkflowAdapter`, not clean.

**`WorkflowAdapter` — fourteen declared methods, forty-four real ones.** Every addition
(extraction, then planning) widened it, and each new hook arrived as another `getattr`
rather than a contract change.

What the comparison supports, and no more: **the narrower seam degraded less.** One
obligation survived a provider replacement with its callers untouched; forty-four optional
ones accumulated synonyms and dead entries. Surface width looks like a stronger predictor
than the typing mechanism — but that is an inference from two data points in generated
code, not a finding.

Worth carrying into the design session: both seams share the same defect — callers that
probe instead of trust. Whatever mechanism is chosen (`Protocol`, `ABC`, base class), the
design should make that defensiveness unnecessary rather than merely tolerated.

---

## Evidence of accretion, not design

Three findings that show the surface grew by addition rather than intent:

1. **Dead probes.** `service.py:1312` and `:1325` still probe `review_output_schema` and
   `parse_review`. Stage 2 deleted both from `SlidesWorkflowAdapter` — no adapter
   implements them. They survive only because the legacy turn-callback branch still
   references them, and that branch is deleted in Stage 4.
2. **Synonym pairs.** `post_extraction` / `consolidate_extraction` (`:473`) and
   `max_author_attempts` / `max_review_rounds` (`:635`, `:1236`) are compatibility aliases
   resolved at call time with `or` and nested `getattr`. The coordinator carries both
   vocabularies permanently.
3. **Two parallel code paths.** `_execute_workflow` is ~400 lines spanning a modern
   per-activation path and a legacy turn-callback path, selected by a `modern_runner`
   heuristic that inspects the runner object's shape. Stage 4 removes the legacy half.

None of this is dangerous. All of it is the procedural sprawl that makes the layer feel
like "whatever" rather than a design.

---

## Stage 4 is a refactor, not a deletion

Worth correcting an impression the migration plan leaves. Stage 4 ("delete the Codex
runtime") reads like removing a file. It is not, because `runner.py` is a **mixed module**:

```python
# sdk_runner.py:95
from .runner import (ProgressReporter, WorkflowExecutionError,
                     WorkflowTimeoutError, _turn_phase, safe_error)
```

The *new* SDK runner imports five symbols from the file to be deleted. `runner.py` holds
Codex-specific code (`CodexRunner`, `CodexAgentRunner`, `CodexRunResult`, the bwrap
helpers) alongside provider-neutral infrastructure everything depends on
(`ProgressReporter`, both exception types, `_turn_phase`, `safe_error`, `RunnerRegistry`,
`_strict_output_schema`).

So Stage 4 is: split the module, move the shared half, repoint `sdk_runner.py`,
`service.py`, `tasks/agents.py` and the tests, then delete what remains.

Two tells that the mixing was accidental: `_turn_phase` is private by naming convention
yet imported across module boundaries, and both Stage 2's typed-output support and the
later strict-schema fix were *added to `runner.py`* because that is where the machinery
already lived. The file accretes by default.

---

## What went right, and why

The planning work is the counter-example worth studying. `planner.py`,
`outline_repository.py` and the `SlideOutline` models formed their **own coherent cluster**
in the knowledge graph rather than smearing into `service.py`. The coordinator gained one
narrow hook (`post_planning`, mirroring `post_extraction`) and stayed workflow-neutral;
everything domain-specific lived in the new modules.

This is a more trustworthy data point than `AgentRunner`, because it was built and reviewed
deliberately within this effort rather than inherited: give a concern its own module and
one narrow obligation, and the structure held — at least through one feature.

Also worth noting what the graph showed after the work: `CodexRunner` became the repo's
4th most-connected node. Complexity moved *toward* the thing being removed, not away from
it — a useful signal that the migration's remaining cost is concentrated, not diffuse.

---

## Questions for the design session

Deliberately unanswered.

1. **Which of the 44 hooks earn their existence?** Several are pure configuration
   (`author_model`, `reviewer_reasoning_effort`, `*_runner`) rather than behaviour. Does a
   workflow *adapter* own model policy at all, or should that be a separate per-node
   configuration object the coordinator resolves?
2. **Is "workflow" even the right unit?** Slides and news share extraction, evidence
   freezing and staging, and differ mainly in prompts, validation and publication. The
   adapter may be bundling several concerns that want separate seams.
3. **What is the node abstraction?** Extraction, planning, author, validator and reviewer
   are each hand-written blocks in `service.py` with near-identical
   emit-event/run/handle-failure shapes. Is there a `WorkflowNode` that makes the state
   machine data rather than code — and does that help, or just relocate the branching?
4. **Where does stage isolation belong?** `stage_hidden_paths` / `stage_read_only_paths` /
   `stage_writable_paths` are a security boundary expressed as three optional adapter
   hooks resolved by `getattr`, with a silent failure mode: an adapter omitting
   `stage_isolation` loses isolation entirely on the SDK runner. A security property
   probably should not be opt-in by attribute presence.
5. **Protocol, ABC, or neither?** Both existing Protocols are probed defensively by their
   callers, so neither settles this. The question may be less "which typing mechanism"
   than "can each seam be made narrow and trusted enough that the mechanism stops
   mattering" — to be decided on design merit, not by precedent in generated code.

---

## Suggested order

Do **Stage 3 and Stage 4 first**, before any architectural redesign. Stage 4 alone deletes
the legacy branch, the dead probes, one of each synonym pair, and roughly a third of
`_execute_workflow`. Refactoring before that means designing around code that is already
scheduled for deletion.

The honest sequence: finish the migration, see what the layer looks like once the Codex
runtime is gone, *then* decide what the architecture should be. The measurements above
should be retaken at that point — the 57/44/14/20 figures will have moved, and the gap
that remains is the real target.
