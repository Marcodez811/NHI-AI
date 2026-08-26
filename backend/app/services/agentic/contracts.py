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

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowStatus(StrEnum):
    """Stable terminal states used on the task boundary."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DeterministicValidationError(ValueError):
    """Expected generated-artifact findings that a correction can address."""


class AgentTaskPayload(BaseModel):
    """The only information accepted by the generic ``agents.run`` task."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID | str = Field()
    workflow: str = Field(min_length=1, max_length=80)
    input: Any

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


class AgentTaskResult(BaseModel):
    """Safe terminal result returned by ``agents.run``."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID | str
    workflow: str
    status: WorkflowStatus
    output: Any = None
    started_at: datetime
    finished_at: datetime
    error: str | None = None


class TurnAudit(BaseModel):
    """Serializable audit record for one Codex turn."""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    kind: str
    status: str
    duration_ms: int | None = None
    usage: Any = None
    response: str | None = None


class TurnRequest(BaseModel):
    """A labeled prompt in a multi-turn workflow."""

    kind: str = Field(min_length=1, max_length=40)
    prompt: str = Field(min_length=1)
    # Sandbox is deliberately part of the turn contract.  Generation and
    # correction turns may write the job workspace, while semantic review is
    # read-only.  Keeping this on the request also makes the transition
    # visible to test doubles and audit consumers.
    sandbox: Any = None
    output_schema: dict[str, Any] | None = None


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

    def prepare_workspace(self, value: InputT, workspace: Path) -> Any: ...

    def prepare_input(self, value: InputT, workspace: Path) -> Any: ...

    def semantic_review_context(self, value: InputT, workspace: Path) -> Any: ...

    def revision_feedback(self, value: InputT, review: Any, result: Any) -> Any: ...

    def publish(self, value: InputT, result: Any, workspace: Path) -> OutputT: ...

    def cleanup(self, value: InputT, workspace: Path, *, success: bool) -> Any: ...


class BaseWorkflowAdapter(Generic[InputT, OutputT]):
    """Convenient safe-default implementation for workflow adapters."""

    name = ""
    declared_skills: Sequence[str] = ()
    input_type: type[InputT] | None = None
    output_type: type[OutputT] | None = None

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
