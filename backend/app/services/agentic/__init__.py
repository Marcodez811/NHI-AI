"""Reusable allowlisted agentic workflow framework."""

from .contracts import (
    AgentTaskPayload,
    AgentTaskResult,
    AgentPhase,
    BaseWorkflowAdapter,
    DeterministicValidationError,
    TurnAudit,
    TurnRequest,
    WorkflowAdapter,
    WorkflowStatus,
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
    CodexRunResult,
    CodexRunner,
    ProgressReporter,
    WorkflowExecutionError,
    WorkflowTimeoutError,
    run_codex_workflow,
    safe_error,
)
from .service import execute_workflow

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
    "AgentPhase", "AgentTaskPayload", "AgentTaskResult", "BaseWorkflowAdapter", "DeterministicValidationError", "TurnAudit",
    "TurnRequest", "WorkflowAdapter", "WorkflowStatus", "WorkflowRegistry",
    "WorkflowRegistryError", "UnknownWorkflowError", "default_registry",
    "workflow_registry", "register_workflow", "SkillStagingError",
    "stage_declared_skills", "validate_skill_name",
    "CodexRunResult", "CodexRunner", "ProgressReporter", "WorkflowExecutionError",
    "WorkflowTimeoutError", "run_codex_workflow", "safe_error", "execute_workflow",
    "slides_adapter",
]
