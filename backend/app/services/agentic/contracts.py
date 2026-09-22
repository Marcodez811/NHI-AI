"""Contracts shared by allowlisted agentic workflows.

The contracts in this module are deliberately workflow-neutral.  A workflow
owns its typed input/output models and domain policy; the runner only knows
how to stage declared skills, invoke Codex, and return a safe audit trail.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowStatus(StrEnum):
    """Stable terminal states used on the task boundary."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentPhase(StrEnum):
    """Stable, user-facing lifecycle phases for an agent workflow.

    These values deliberately describe the workflow rather than a provider
    SDK event or a worker implementation detail.  They are safe to persist in
    progress metadata and to expose from workflow-specific APIs.
    """

    QUEUED = "queued"
    PREPARING = "preparing"
    EXTRACTING = "extracting"
    # Planning sits between extraction and authoring: the outline must be
    # grounded in already-frozen evidence, so it cannot run earlier.
    PLANNING = "planning"
    # Non-terminal pause while a human reviews or discusses the outline.
    # The worker releases its lease in this phase rather than blocking.
    AWAITING_OUTLINE = "awaiting_outline"
    DRAFTING = "drafting"
    VALIDATING = "validating"
    REVIEWING = "reviewing"
    REVISING = "revising"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentReasoningEffort(StrEnum):
    """Reasoning levels supported by the current Codex model family."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ReviewSeverity(StrEnum):
    """Severity assigned to a reviewer finding."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class ReviewFindingStatus(StrEnum):
    """Whether a finding is still present in the current candidate."""

    OPEN = "open"
    RESOLVED = "resolved"


class ReviewDecision(StrEnum):
    """Deterministic decision made after a semantic review."""

    PUBLISH = "publish"
    RETRY = "retry"
    REJECT_MAX_ATTEMPTS = "max_attempts"
    REJECT_STAGNATED = "stagnated"


class ReviewFinding(BaseModel):
    """A structured, identity-bearing semantic review finding.

    ``issue_key`` and ``locations`` are intentionally provider-neutral.  The
    workflow adapter maps domain concepts (slides, claims, artifacts, etc.)
    into these canonical identifiers so the coordinator can compare rounds
    without comparing free-form prose.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: str | None = Field(default=None, min_length=1, max_length=120)
    status: ReviewFindingStatus = ReviewFindingStatus.OPEN
    severity: ReviewSeverity = ReviewSeverity.BLOCKING
    category: str = Field(min_length=1, max_length=80)
    # ``issue_key`` and ``locations`` are retained for the generic workflow
    # contract.  Slides reviewers use the more useful domain fields below;
    # the model validator derives the generic values when they are omitted.
    issue_key: str = Field(default="", max_length=120)
    locations: tuple[str, ...] = Field(default=(), max_length=20)
    description: str = Field(default="", max_length=4000)
    correction: str = Field(default="", max_length=4000)
    slide_number: int | None = Field(default=None, ge=1)
    claim: str = Field(default="", max_length=4000)
    judgement: str = Field(default="", max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def complete_domain_fields(self) -> "ReviewFinding":
        """Make the slide-facing response ergonomic without weakening identity.

        The coordinator still compares canonical ``category``/``issue_key``
        pairs.  A semantic reviewer may instead return the fields from the
        public slide-review schema, so derive those compatibility fields once
        at the trust boundary.
        """

        claim = self.claim.strip()
        reason = self.reason.strip()
        description = self.description.strip() or reason or claim
        issue_key = self.issue_key.strip()
        if not issue_key:
            issue_key = f"slide-{self.slide_number or 'unknown'}::{claim[:96] or self.category}"
        locations = self.locations
        if not locations and self.slide_number is not None:
            locations = (f"slide:{self.slide_number}",)
        if not reason:
            reason = description
        # Mutate the instance so Pydantic's ``__init__`` path and
        # ``model_validate`` expose the same normalized values.
        self.claim = claim
        self.reason = reason
        self.description = description
        self.issue_key = issue_key
        self.locations = locations
        return self


class ReviewOutcome(BaseModel):
    """Normalized result of one semantic review activation."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=4000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=200)


class ReviewEvaluation(BaseModel):
    """Pure policy output used by both coordinator execution paths."""

    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    blocking_count: int = Field(ge=0)
    advisory_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    new_count: int = Field(ge=0)
    persistent_count: int = Field(ge=0)
    stagnant_transitions: int = Field(ge=0)


def review_finding_identity(finding: ReviewFinding) -> str:
    """Return the canonical identity used to compare findings across rounds."""

    # Locations deliberately do not participate in identity.  An author can
    # fix a claim by moving or replacing it, which naturally changes its
    # canonical location while the underlying issue remains the same.
    return f"{finding.category.strip().lower()}::{finding.issue_key.strip().lower()}"


def normalize_review_outcome(
    outcome: ReviewOutcome,
    previous: ReviewOutcome | None,
    *,
    attempt: int,
) -> ReviewOutcome:
    """Assign stable IDs and fail closed on invalid reviewer continuity.

    The reviewer receives IDs for the previous round's open blockers and must
    return each one exactly once.  A newly worded finding with the same
    structured identity inherits the prior ID, preventing wording changes from
    resetting the stagnation counter.
    """

    previous_findings = {item.finding_id: item for item in (previous.findings if previous else ()) if item.finding_id}
    previous_blocker_ids = {
        item.finding_id
        for item in (previous.findings if previous else ())
        if item.finding_id and item.status is ReviewFindingStatus.OPEN and item.severity is ReviewSeverity.BLOCKING
    }
    previous_by_identity = {
        review_finding_identity(item): item.finding_id
        for item in (previous.findings if previous else ())
        if item.finding_id
    }
    previous_identity_by_id = {
        item.finding_id: review_finding_identity(item)
        for item in (previous.findings if previous else ())
        if item.finding_id
    }

    seen_ids: set[str] = set()
    seen_identities: set[str] = set()
    normalized: list[ReviewFinding] = []
    for index, item in enumerate(outcome.findings, start=1):
        identity = review_finding_identity(item)
        if identity in seen_identities:
            raise ValueError("semantic review contained duplicate finding identities")
        seen_identities.add(identity)

        finding_id = item.finding_id
        if finding_id is not None:
            if finding_id not in previous_findings:
                raise ValueError("semantic review referenced an unknown previous finding")
            if finding_id in seen_ids:
                raise ValueError("semantic review referenced a previous finding more than once")
            if previous_identity_by_id[finding_id] != identity:
                raise ValueError("semantic review changed a previous finding identity")
            if previous_findings[finding_id].severity != item.severity:
                raise ValueError("semantic review changed a previous finding severity")
        else:
            inherited_id = previous_by_identity.get(identity)
            if inherited_id and previous_findings[inherited_id].severity != item.severity:
                raise ValueError("semantic review changed a previous finding severity")
            finding_id = inherited_id or f"review-{attempt}-finding-{index}"
        seen_ids.add(finding_id)
        normalized.append(item.model_copy(update={"finding_id": finding_id}))

    missing = previous_blocker_ids - seen_ids
    if missing:
        raise ValueError("semantic review omitted a previous blocking finding")
    return outcome.model_copy(update={"findings": normalized})


def evaluate_review(
    outcome: ReviewOutcome,
    previous: ReviewOutcome | None,
    *,
    attempt: int,
    max_attempts: int,
    stagnant_transitions: int,
    stagnation_limit: int,
) -> ReviewEvaluation:
    """Evaluate publication/retry/rejection without provider-specific logic."""

    current_blockers = {
        item.finding_id
        for item in outcome.findings
        if item.finding_id and item.status is ReviewFindingStatus.OPEN and item.severity is ReviewSeverity.BLOCKING
    }
    previous_blockers = {
        item.finding_id
        for item in (previous.findings if previous else ())
        if item.finding_id and item.status is ReviewFindingStatus.OPEN and item.severity is ReviewSeverity.BLOCKING
    }
    resolved_count = len(previous_blockers - current_blockers)
    persistent_count = len(previous_blockers & current_blockers)
    new_count = len(current_blockers - previous_blockers)
    next_stagnant = stagnant_transitions
    if previous is not None:
        next_stagnant = 0 if resolved_count else stagnant_transitions + 1

    if not current_blockers:
        decision = ReviewDecision.PUBLISH
    elif attempt >= max_attempts:
        decision = ReviewDecision.REJECT_MAX_ATTEMPTS
    elif next_stagnant >= stagnation_limit:
        decision = ReviewDecision.REJECT_STAGNATED
    else:
        decision = ReviewDecision.RETRY

    return ReviewEvaluation(
        decision=decision,
        blocking_count=len(current_blockers),
        advisory_count=sum(
            1
            for item in outcome.findings
            if item.status is ReviewFindingStatus.OPEN and item.severity is ReviewSeverity.ADVISORY
        ),
        resolved_count=resolved_count,
        new_count=new_count,
        persistent_count=persistent_count,
        stagnant_transitions=next_stagnant,
    )


class DeterministicValidationError(ValueError):
    """Expected generated-artifact findings that a correction can address.

    This remains a ``ValueError`` for compatibility with callers that used the
    original validation contract.  The coordinator only treats this explicit
    exception as a retry signal; arbitrary ``ValueError`` instances are
    terminal failures.
    """

    def __init__(
        self,
        message: str,
        *,
        findings: Sequence[Any] = (),
        diagnostic_codes: Sequence[str] = (),
        codes: Sequence[str] | None = None,
    ) -> None:
        self.findings = tuple(findings)
        selected_codes = diagnostic_codes if codes is None else codes
        self.diagnostic_codes = tuple(str(code) for code in selected_codes if str(code).strip())
        # ``codes`` is a short compatibility alias useful to operational
        # consumers that do not need to know the longer field name.
        self.codes = self.diagnostic_codes
        super().__init__(message)


class ValidationInfrastructureError(RuntimeError):
    """Validator infrastructure failed and the candidate must not be retried.

    This deliberately does not inherit from ``ValueError``.  A provider or
    validator outage is an operational failure, not author-correctable
    feedback.  Stable diagnostic codes are retained for telemetry and safe
    task results without exposing validator paths or raw exception details.
    """

    def __init__(
        self,
        message: str,
        *,
        findings: Sequence[Any] = (),
        diagnostic_codes: Sequence[str] = (),
        codes: Sequence[str] | None = None,
    ) -> None:
        self.findings = tuple(findings)
        selected_codes = diagnostic_codes if codes is None else codes
        self.diagnostic_codes = tuple(str(code) for code in selected_codes if str(code).strip())
        self.codes = self.diagnostic_codes
        super().__init__(message)


class AgentTaskPayload(BaseModel):
    """The only information accepted by the generic ``agents.run`` task."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID | str = Field()
    workflow: str = Field(min_length=1, max_length=80)
    input: Any
    # Stage 5 (docs/agents-sdk-migration-plan.md): ``None`` is an ordinary first
    # pass. ``"author"`` re-enters a paused workflow after a human has approved an
    # outline -- extraction and planning are skipped and the frozen
    # ``work/outline.json`` the approval endpoint wrote is read instead. This stays
    # on the generic task envelope, not a workflow-specific payload, because any
    # workflow with a human-in-the-loop pause could eventually name a resume point.
    resume_from: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("job_id", "workflow")
    @classmethod
    def safe_boundary_name(cls, value: UUID | str) -> UUID | str:
        if isinstance(value, UUID):
            return value
        value = value.strip()
        # These values become registry keys or directory names.  Reject path
        # syntax at deserialization, before any workspace or SDK is touched.
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("job_id and workflow must be simple names")
        return value

    @field_validator("resume_from")
    @classmethod
    def safe_resume_point(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("resume_from must be a simple stage name")
        return value


class AgentTaskResult(BaseModel):
    """Safe terminal result returned by ``agents.run``."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID | str
    workflow: str
    status: WorkflowStatus
    phase: AgentPhase | None = None
    output: Any = None
    started_at: datetime
    finished_at: datetime
    error: str | None = None

    @model_validator(mode="after")
    def default_terminal_phase(self) -> "AgentTaskResult":
        """Keep older task constructors valid while normalizing terminals."""

        if self.status is WorkflowStatus.COMPLETED:
            self.phase = AgentPhase.COMPLETED
        elif self.status is WorkflowStatus.FAILED:
            self.phase = AgentPhase.FAILED
        return self


class TurnAudit(BaseModel):
    """Serializable audit record for one Codex turn."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    kind: str
    status: str
    duration_ms: int | None = None
    usage: Any = None
    response: str | None = None
    error: str | None = None
    prompt: str | None = None


class TurnRequest(BaseModel):
    """A labeled prompt in a multi-turn workflow."""

    kind: str = Field(min_length=1, max_length=40)
    prompt: str = Field(min_length=1)
    # Sandbox is deliberately part of the turn contract. Workflow adapters may
    # narrow provider access further with the stage path policy carried by
    # ``AgentExecutionRequest``. Keeping this field visible also lets test
    # doubles and audit consumers verify the requested provider sandbox.
    sandbox: Any = None
    output_schema: dict[str, Any] | None = None


class AgentExecutionRequest(BaseModel):
    """Provider-neutral description of one logical agent activation.

    Workflow inputs never construct this object directly.  The coordinator
    creates it from an allowlisted workflow plan, which keeps provider choice,
    prompts, and skill paths out of the public task boundary.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: UUID | str
    node_id: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=120)
    attempt: int = Field(default=1, ge=1)
    # Model selection is per logical activation, not a property of the
    # runner.  ``None`` means the selected runner's configured default.
    model: str | None = Field(default=None, min_length=1, max_length=160)
    # Reasoning effort follows the same per-activation policy as model
    # selection. ``None`` delegates to the selected runner's default.
    reasoning_effort: AgentReasoningEffort | None = None
    workspace: Path
    prompt: str = Field(min_length=1)
    # Optional system-prompt text, kept structurally separate from ``prompt``
    # (the per-turn input). Only the Agents SDK runner's grant-less path uses
    # this -- it sets it on the native ``agents.Agent.instructions`` field, so
    # task instructions and untrusted turn data (e.g. evidence a planner must
    # cite, never obey) never share one undifferentiated string. ``None``
    # means the caller relies on ``prompt`` alone, matching every runner's
    # behavior before this field existed.
    instructions: str | None = None
    sandbox: Any = None
    # A raw provider JSON schema, understood only by Codex's per-turn schema
    # pass-through.  ``output_type`` below is the provider-neutral successor:
    # a runner that can validate structured output natively (the Agents SDK)
    # or by deriving a schema from the type itself (Codex) uses it to hand
    # the coordinator an already-validated model instead of free-form text.
    # A request is expected to set at most one of the two.
    output_schema: dict[str, Any] | None = None
    output_type: type[BaseModel] | None = None
    skill_names: tuple[str, ...] = Field(default=(), validation_alias=AliasChoices("skill_names", "staged_skills"))
    audit_path: Path | None = None
    # Paths the provider process must not be able to inspect for this stage.
    # The coordinator populates these from an allowlisted adapter policy;
    # callers cannot provide them through the public task payload.
    hidden_paths: tuple[Path, ...] = ()
    read_only_paths: tuple[Path, ...] = ()
    writable_paths: tuple[Path, ...] = ()
    restrict_workspace: bool = False

    @property
    def staged_skills(self) -> tuple[str, ...]:
        """Readable alias used by provider integrations."""

        return self.skill_names

    @field_validator("node_id", "role")
    @classmethod
    def safe_node_name(cls, value: str) -> str:
        value = value.strip()
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("agent node names must be simple names")
        return value

    @field_validator("model")
    @classmethod
    def normalize_model_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("model must not be empty")
        return value


class AgentExecutionResult(BaseModel):
    """Provider-neutral result returned by one runner activation."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    provider_run_id: str
    response: str | None = None
    duration_ms: int | None = None
    usage: Any = None
    audits: list[TurnAudit] = Field(default_factory=list)
    # Populated only when the originating ``AgentExecutionRequest`` declared
    # ``output_type``: the runner-validated model (e.g. ``ReviewOutcome``),
    # so a caller that asked for typed output never has to re-parse
    # ``response`` text itself.
    output: BaseModel | None = None

    @property
    def thread_id(self) -> str:
        """Compatibility alias for callers that still use Codex terminology."""

        return self.provider_run_id

    @property
    def last_audit(self) -> TurnAudit | None:
        return self.audits[-1] if self.audits else None


class AgentNode(BaseModel):
    """A server-defined logical node in a bounded workflow."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=120)
    runner: str = Field(default="codex", min_length=1, max_length=80)
    depends_on: tuple[str, ...] = ()

    @field_validator("id", "runner")
    @classmethod
    def safe_plan_name(cls, value: str) -> str:
        value = value.strip()
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("agent plan names must be simple names")
        return value


class WorkflowPlan(BaseModel):
    """Allowlisted workflow nodes; this is intentionally not user supplied."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[AgentNode] = Field(min_length=1)


@runtime_checkable
class AgentRunner(Protocol):
    """Provider-neutral runner interface used by ``WorkflowCoordinator``."""

    name: str
    timeout_seconds: float | None

    async def run(
        self,
        request: AgentExecutionRequest,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> AgentExecutionResult: ...


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@runtime_checkable
class WorkflowAdapter(Protocol, Generic[InputT, OutputT]):
    """Typed policy surface implemented by each allowlisted workflow.

    ``input_type`` and ``output_type`` may be Pydantic models or ordinary
    Python types.  The hooks can be synchronous or asynchronous.  Optional
    hooks are intentionally discovered by name by the orchestrator so small
    workflows only need to implement the parts they use.
    """

    name: str
    declared_skills: Sequence[str]
    input_type: type[InputT] | None
    output_type: type[OutputT] | None
    author_model: str | None
    reviewer_model: str | None
    author_reasoning_effort: AgentReasoningEffort | None
    reviewer_reasoning_effort: AgentReasoningEffort | None
    max_author_attempts: int
    review_stagnation_limit: int

    def validate_input(self, value: Any) -> InputT: ...

    def deterministic_validate(self, value: InputT, workspace: Path) -> Any: ...

    def build_prompt(
        self,
        value: InputT,
        workspace: Path,
        *,
        semantic_review_context: Any = None,
        revision_feedback: str | None = None,
    ) -> str: ...

    def build_planning_prompt(self, value: InputT, workspace: Path) -> str: ...

    def post_planning(self, value: InputT, result: AgentExecutionResult, workspace: Path) -> Any: ...

    def prepare_workspace(self, value: InputT, workspace: Path) -> Any: ...

    def prepare_input(self, value: InputT, workspace: Path) -> Any: ...

    def post_author_completion_check(self, value: InputT, result: Any, workspace: Path) -> Any: ...

    def semantic_review_context(self, value: InputT, workspace: Path) -> Any: ...

    def build_review_prompt(
        self,
        value: InputT,
        workspace: Path,
        audit: Any,
        review_context: Any,
        previous_review: ReviewOutcome | None = None,
    ) -> str: ...

    def parse_review(
        self,
        response: str | None,
        workspace: Path,
        previous_review: ReviewOutcome | None = None,
    ) -> ReviewOutcome: ...

    def revision_feedback(self, value: InputT, review: Any, result: Any) -> Any: ...

    def publish(self, value: InputT, result: Any, workspace: Path) -> OutputT: ...

    def cleanup(self, value: InputT, workspace: Path, *, success: bool) -> Any: ...


class BaseWorkflowAdapter(Generic[InputT, OutputT]):
    """Convenient safe-default implementation for workflow adapters."""

    name = ""
    declared_skills: Sequence[str] = ()
    input_type: type[InputT] | None = None
    output_type: type[OutputT] | None = None
    # Provider and role selection belongs to the server-side adapter.  These
    # defaults let simple adapters opt into the generic author/reviewer state
    # machine without exposing runner selection in task input.
    author_runner = "codex"
    reviewer_runner = "codex"
    # ``None`` delegates to the runner's configured default.  Workflows that
    # use asymmetric model policy override these on the adapter.
    author_model: str | None = None
    reviewer_model: str | None = None
    author_reasoning_effort: AgentReasoningEffort | None = None
    reviewer_reasoning_effort: AgentReasoningEffort | None = None
    author_role = "presentation_author"
    reviewer_role = "presentation_reviewer"
    # Stages opt in explicitly.  Generic workflows continue to use their
    # declared skill set as one bundle for the legacy path.
    extraction_skills: Sequence[str] = ()
    author_skills: Sequence[str] = ()
    reviewer_skills: Sequence[str] = ()
    independent_semantic_review = False
    stage_isolation = False
    # Stage 5: planning is opt-in the same way extraction is -- ``planner_role``
    # stays ``None`` for every workflow that does not declare a planning agent, so
    # ``_execute_workflow`` never runs or pauses for one and existing workflows
    # (e.g. ``news``) are byte-for-byte unaffected by this stage's addition.
    planner_role: str | None = None
    planner_skills: Sequence[str] = ()
    planner_model: str | None = None
    planner_reasoning_effort: AgentReasoningEffort | None = None
    planner_runner = "codex"
    # The concrete structured-output contract (e.g. ``SlideOutline``) belongs to
    # the workflow, not to this workflow-neutral module.
    planner_output_type: type[BaseModel] | None = None

    def validate_input(self, value: Any) -> InputT:
        if self.input_type is None:
            return value
        model = self.input_type
        if isinstance(value, model):
            return value
        if issubclass(model, BaseModel):
            return model.model_validate(value)  # type: ignore[return-value]
        if isinstance(value, model):
            return value
        raise ValueError("workflow input has an invalid type")

    def deterministic_validate(self, value: InputT, workspace: Path) -> None:
        """Hook for cheap policy checks that must precede Codex invocation."""
        return None

    def validate_deterministically(self, value: InputT, workspace: Path) -> None:
        return self.deterministic_validate(value, workspace)

    def build_prompt(self, value: InputT, workspace: Path, *, semantic_review_context: Any = None, revision_feedback: str | None = None) -> str:
        return str(value)

    def prepare_workspace(self, value: InputT, workspace: Path) -> None:
        return None

    def prepare_input(self, value: InputT, workspace: Path) -> None:
        return None

    def build_extraction_prompt(self, value: InputT, workspace: Path) -> str:
        """Return the prompt for the one-time source extraction activation."""

        return str(value)

    def post_extraction(self, value: InputT, workspace: Path) -> Any:
        """Validate and freeze extraction artifacts into the EvidenceStore."""

        return None

    def build_planning_prompt(self, value: InputT, workspace: Path) -> str:
        """Return the prompt for the planning activation.

        Only called when ``planner_role`` is set; a workflow that declares a
        planner is expected to override this the same way it overrides
        ``build_extraction_prompt``.
        """

        return str(value)

    def post_planning(self, value: InputT, result: Any, workspace: Path) -> Any:
        """Persist the planner's typed output as a durable, immutable revision.

        The generic coordinator only knows a planning activation completed with
        some ``AgentExecutionResult``; it is deliberately ignorant of any
        concrete outline schema or storage, so a workflow that declares a
        planner is expected to override this the same way it overrides
        ``post_extraction``.
        """

        return None

    def stage_hidden_paths(self, stage: str, workspace: Path) -> Sequence[Path]:
        """Return source/history paths hidden from one provider activation."""

        return ()

    def stage_read_only_paths(self, stage: str, workspace: Path) -> Sequence[Path]:
        """Return artifacts mounted read-only for one provider activation."""

        return ()

    def stage_writable_paths(self, stage: str, workspace: Path) -> Sequence[Path]:
        """Return the only workspace paths writable by an isolated activation."""

        return ()

    def post_author_completion_check(self, value: InputT, result: Any, workspace: Path) -> None:
        return None

    def semantic_review_context(self, value: InputT, workspace: Path) -> Any:
        return None

    # Verbose aliases make the lifecycle intent explicit for adapters whose
    # codebase uses ``build_*`` naming; the orchestrator accepts either form.
    def build_semantic_review_context(self, value: InputT, workspace: Path) -> Any:
        return self.semantic_review_context(value, workspace)

    def revision_feedback(self, value: InputT, review: Any, result: Any) -> str | None:
        return None

    def build_revision_feedback(self, value: InputT, review: Any, result: Any) -> str | None:
        return self.revision_feedback(value, review, result)

    def publish(self, value: InputT, result: Any, workspace: Path) -> OutputT:  # type: ignore[return-value]
        output = getattr(result, "response", result)
        if self.output_type is not None and issubclass(self.output_type, BaseModel):
            return self.output_type.model_validate(output)  # type: ignore[return-value]
        return output

    def cleanup(self, value: InputT, workspace: Path, *, success: bool) -> None:
        return None
