"""Reusable allowlisted agentic workflow framework."""

from .contracts import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentNode,
    AgentReasoningEffort,
    ReviewDecision,
    ReviewEvaluation,
    ReviewFinding,
    ReviewFindingStatus,
    ReviewOutcome,
    ReviewSeverity,
    AgentTaskPayload,
    AgentTaskResult,
    AgentPhase,
    AgentRunner,
    BaseWorkflowAdapter,
    DeterministicValidationError,
    TurnAudit,
    TurnRequest,
    WorkflowPlan,
    WorkflowAdapter,
    WorkflowStatus,
    evaluate_review,
    normalize_review_outcome,
    review_finding_identity,
)
from .registry import (
    UnknownWorkflowError,
    WorkflowRegistry,
    WorkflowRegistryError,
    default_registry,
    register_workflow,
    workflow_registry,
)
from .staging import SkillStagingError, stage_declared_skills, validate_skill_name
from .runner import (
    RunnerRegistry,
    RunnerRegistryError,
    UnknownRunnerError,
    CodexAgentRunner,
    CodexRunResult,
    CodexRunner,
    ProgressReporter,
    WorkflowExecutionError,
    WorkflowTimeoutError,
    run_codex_workflow,
    safe_error,
    runner_registry,
)
from .service import execute_workflow
from .coordinator import WorkflowCoordinator
from .events import (
    AgentEvent,
    AgentEventPage,
    AgentEventType,
    AgentNodeSnapshot,
    AgentRunListResponse,
    AgentRunSnapshot,
    AgentTelemetryError,
    AgentTelemetryStore,
)

def __getattr__(name: str):
    """Lazily expose built-ins without creating an import cycle.

    ``app.services.slides.adapter`` imports the generic contracts directly. A
    lazy attribute keeps direct adapter imports valid while still providing a
    convenient ``app.services.agentic.slides_adapter`` integration surface.
    """

    if name == "slides_adapter":
        from app.services.slides.adapter import slides_adapter

        return slides_adapter
    raise AttributeError(name)

__all__ = [
    "AgentExecutionRequest", "AgentExecutionResult", "AgentNode", "AgentPhase", "AgentReasoningEffort", "AgentTaskPayload", "AgentTaskResult",
    "AgentRunner", "BaseWorkflowAdapter", "DeterministicValidationError", "ReviewDecision", "ReviewEvaluation", "ReviewFinding", "ReviewFindingStatus", "ReviewOutcome", "ReviewSeverity", "TurnAudit", "TurnRequest", "WorkflowPlan",
    "WorkflowAdapter", "WorkflowStatus", "WorkflowRegistry", "evaluate_review", "normalize_review_outcome", "review_finding_identity",
    "WorkflowRegistryError", "UnknownWorkflowError", "default_registry",
    "workflow_registry", "register_workflow", "SkillStagingError",
    "stage_declared_skills", "validate_skill_name",
    "CodexAgentRunner", "CodexRunResult", "CodexRunner", "ProgressReporter", "RunnerRegistry", "RunnerRegistryError",
    "UnknownRunnerError", "runner_registry", "WorkflowExecutionError", "WorkflowTimeoutError", "run_codex_workflow",
    "safe_error", "execute_workflow", "WorkflowCoordinator",
    "AgentEvent", "AgentEventPage", "AgentEventType", "AgentNodeSnapshot", "AgentRunListResponse",
    "AgentRunSnapshot", "AgentTelemetryError", "AgentTelemetryStore",
    "slides_adapter",
]
