"""Server-owned configuration for provider and workflow policy."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, SecretStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.services.agentic.contracts import AgentReasoningEffort


class Settings(BaseSettings):
    """Keep optional provider credentials separate from model selection."""

    redis_url: str
    openai_api_key: SecretStr
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    # Agent model policy is separate from the chat/vector-store settings. The
    # old OPENAI_MODEL variable remains an input alias for the generic default
    # during migration.
    agent_default_model: str = Field(
        default="gpt-5.6-luna",
        min_length=1,
        validation_alias=AliasChoices("AGENT_DEFAULT_MODEL", "OPENAI_MODEL"),
    )
    agent_author_model: str | None = Field(
        default="gpt-5.6-luna",
        min_length=1,
        validation_alias=AliasChoices("AGENT_AUTHOR_MODEL"),
    )
    agent_reviewer_model: str | None = Field(
        default="gpt-5.6-sol",
        min_length=1,
        validation_alias=AliasChoices("AGENT_REVIEWER_MODEL"),
    )
    agent_extraction_model: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("AGENT_EXTRACTION_MODEL"),
    )
    # Stage 5 (docs/agents-sdk-migration-plan.md): the planner node's model policy
    # follows the same per-node override pattern as extraction/author/reviewer.
    agent_planner_model: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("AGENT_PLANNER_MODEL"),
    )
    agent_default_reasoning_effort: AgentReasoningEffort = Field(
        default=AgentReasoningEffort.HIGH,
        validation_alias=AliasChoices("AGENT_DEFAULT_REASONING_EFFORT"),
    )
    agent_author_reasoning_effort: AgentReasoningEffort | None = Field(
        default=AgentReasoningEffort.HIGH,
        validation_alias=AliasChoices("AGENT_AUTHOR_REASONING_EFFORT"),
    )
    agent_reviewer_reasoning_effort: AgentReasoningEffort | None = Field(
        default=AgentReasoningEffort.HIGH,
        validation_alias=AliasChoices("AGENT_REVIEWER_REASONING_EFFORT"),
    )
    agent_extraction_reasoning_effort: AgentReasoningEffort | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_EXTRACTION_REASONING_EFFORT"),
    )
    agent_planner_reasoning_effort: AgentReasoningEffort | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_PLANNER_REASONING_EFFORT"),
    )
    openai_chat_model: str = "gpt-5.6-luna"
    openai_vector_store_id: str | None = None
    openai_vector_store_name: str = "NHI-AI Knowledge Base"
    openai_vector_store_bootstrap_timeout_seconds: float = Field(default=10.0, gt=0)
    chat_stream_heartbeat_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias=AliasChoices("CHAT_STREAM_HEARTBEAT_SECONDS"),
    )
    chat_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        validation_alias=AliasChoices("CHAT_TIMEOUT_SECONDS"),
    )
    database_url: str = "sqlite:///./nhi_ai.db"
    # Canonical Luna 1 names.  AliasChoices keeps deployments using the old
    # SLIDES_* names working during migration; the new name is intentionally
    # first, so it wins when both are present.
    documents_root: Path = Field(
        default=Path("/tmp/documents"),
        validation_alias=AliasChoices("DOCUMENTS_ROOT", "SLIDES_DOCUMENTS_ROOT"),
    )
    agent_jobs_root: Path = Field(
        default=Path("/tmp/agents/jobs"),
        validation_alias=AliasChoices("AGENT_JOBS_ROOT", "SLIDES_JOBS_ROOT"),
    )
    agent_output_root: Path = Field(
        default=Path("/tmp/agents/output"),
        validation_alias=AliasChoices("AGENT_OUTPUT_ROOT", "SLIDES_OUTPUT_ROOT"),
    )
    agent_timeout_minutes: int = Field(
        default=60,
        gt=0,
        validation_alias=AliasChoices("AGENT_TIMEOUT_MINUTES", "SLIDES_TIMEOUT_MINUTES"),
    )
    agent_heartbeat_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias=AliasChoices("AGENT_HEARTBEAT_SECONDS"),
    )
    agent_keep_workspace_on_failure: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGENT_KEEP_WORKSPACE_ON_FAILURE", "SLIDES_KEEP_WORKSPACE_ON_FAILURE"),
    )
    agent_max_author_attempts: int = Field(
        default=5,
        ge=1,
        validation_alias=AliasChoices("AGENT_MAX_AUTHOR_ATTEMPTS", "AGENT_MAX_REVIEW_ROUNDS"),
    )
    agent_review_stagnation_limit: int = Field(
        default=2,
        ge=1,
        validation_alias=AliasChoices("AGENT_REVIEW_STAGNATION_LIMIT"),
    )
    agent_require_process_isolation: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGENT_REQUIRE_PROCESS_ISOLATION"),
    )
    # Runner selection per node (docs/agents-sdk-migration-plan.md, Stage 0). Each
    # defaults to "codex" so registering "agents" in the runner allowlist is a no-op
    # until an operator flips one of these by configuration rather than by deploy.
    agent_extraction_runner: str = Field(
        default="codex",
        min_length=1,
        validation_alias=AliasChoices("AGENT_EXTRACTION_RUNNER"),
    )
    agent_author_runner: str = Field(
        default="codex",
        min_length=1,
        validation_alias=AliasChoices("AGENT_AUTHOR_RUNNER"),
    )
    agent_reviewer_runner: str = Field(
        default="codex",
        min_length=1,
        validation_alias=AliasChoices("AGENT_REVIEWER_RUNNER"),
    )
    agent_planner_runner: str = Field(
        default="codex",
        min_length=1,
        validation_alias=AliasChoices("AGENT_PLANNER_RUNNER"),
    )
    # Stage 5 inserts a new human-in-the-loop pause into the slides pipeline,
    # not just a runner choice for an existing stage, so it defaults off: every
    # slides job completes exactly as it does today until an operator opts in.
    agent_planner_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("AGENT_PLANNER_ENABLED"),
    )
    # The "agents" planner path folds a compact rendering of the frozen evidence into
    # its prompt instead of reading it through a sandbox grant (see sdk_runner.py's "no
    # grants -> no sandbox" rule and docs/agents-sdk-migration-plan.md's Stage 5
    # architecture note). This bounds that rendering by the total characters of block
    # *text* it carries -- the field the guard is measuring, not the compact JSON's
    # id/citation overhead. Real documents measured so far range 25k-93k characters of
    # block text; 150,000 gives roughly 1.6x headroom over the largest observed
    # document set while still failing fast on a document large enough to risk
    # crowding out the outline instructions and other context in one turn, rather than
    # silently truncating evidence a planner would then cite incompletely.
    agent_planner_max_evidence_chars: int = Field(
        default=150_000,
        gt=0,
        validation_alias=AliasChoices("AGENT_PLANNER_MAX_EVIDENCE_CHARS"),
    )
    # Risk 3 (docs/agents-sdk-migration-plan.md, Stage 5b): a job parked in
    # AWAITING_INPUT holds its workspace under ``agent_jobs_root`` until a human
    # approves or rejects the outline. Seven days covers a full review cycle,
    # including a weekend, without leaking workspace disk indefinitely for an
    # outline nobody is coming back to.
    agent_awaiting_outline_ttl_seconds: int = Field(
        default=604_800,
        gt=0,
        validation_alias=AliasChoices("AGENT_AWAITING_OUTLINE_TTL_SECONDS"),
    )
    # Sweep cadence is independent of the TTL above so expiry granularity can
    # be tuned without changing how long a human is given to respond. Hourly
    # matches the coarse granularity a multi-day TTL needs.
    agent_awaiting_outline_sweep_interval_seconds: int = Field(
        default=3_600,
        gt=0,
        validation_alias=AliasChoices("AGENT_AWAITING_OUTLINE_SWEEP_INTERVAL_SECONDS"),
    )
    # Agent telemetry is short-lived diagnostic data, not workflow history.
    agent_event_retention_seconds: int = Field(
        default=86_400,
        ge=1,
        validation_alias=AliasChoices("AGENT_EVENT_RETENTION_SECONDS"),
    )
    enable_agent_dev_routes: bool = Field(
        default=False,
        validation_alias=AliasChoices("ENABLE_AGENT_DEV_ROUTES"),
    )
    documents_worker_processes: int = Field(default=1, gt=0)
    documents_worker_max_async_tasks: int = Field(default=4, gt=0)
    tasks_worker_processes: int = Field(default=2, gt=0)
    tasks_worker_max_async_tasks: int = Field(default=2, gt=0)
    documents_queue_name: str = "documents"
    tasks_queue_name: str = "tasks"
    max_upload_bytes: int = 250 * 1024 * 1024
    # Promoted-resource cleanup is deliberately conservative: retries are
    # delayed exponentially and reconciliation provides a second recovery
    # path when Redis or a worker is unavailable during promotion.
    document_cleanup_lease_seconds: int = Field(default=300, gt=0)
    document_cleanup_retry_base_seconds: float = Field(default=60.0, gt=0)
    document_cleanup_retry_max_seconds: float = Field(default=3600.0, gt=0)
    document_cleanup_reconcile_interval_seconds: int = Field(default=900, gt=0)
    document_cleanup_reconcile_batch_size: int = Field(default=100, gt=0)
    taskiq_schedule_prefix: str = "nhi-ai"

    # Short aliases used by operator configuration and older deployment
    # manifests.  The canonical names above remain the serialized settings.
    @property
    def cleanup_lease_seconds(self) -> int:
        return self.document_cleanup_lease_seconds

    @property
    def cleanup_retry_base_seconds(self) -> float:
        return self.document_cleanup_retry_base_seconds

    @property
    def cleanup_retry_max_seconds(self) -> float:
        return self.document_cleanup_retry_max_seconds

    @property
    def cleanup_reconcile_interval_seconds(self) -> int:
        return self.document_cleanup_reconcile_interval_seconds

    # env variables storage
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_constructor_names(cls, values):
        """Accept old Python keyword names with canonical names taking priority."""

        if not isinstance(values, dict):
            return values
        values = dict(values)
        for old_name, new_name in {
            "openai_model": "agent_default_model",
            "slides_jobs_root": "agent_jobs_root",
            "slides_documents_root": "documents_root",
            "slides_output_root": "agent_output_root",
            "slides_timeout_minutes": "agent_timeout_minutes",
            "slides_keep_workspace_on_failure": "agent_keep_workspace_on_failure",
            "agent_max_review_rounds": "agent_max_author_attempts",
        }.items():
            if new_name not in values and old_name in values:
                values[new_name] = values[old_name]
        return values

    # Transitional Python attribute alias for callers that still use the old
    # setting name (the queued agent worker uses ``agent_default_model``).
    @property
    def openai_model(self) -> str:
        return self.agent_default_model

    @openai_model.setter
    def openai_model(self, value: str) -> None:
        self.agent_default_model = value

    # Transitional Python attribute aliases.  Setters are deliberate: a
    # number of integrations override settings in tests/startup hooks.
    @property
    def slides_jobs_root(self) -> Path:
        return self.agent_jobs_root

    @slides_jobs_root.setter
    def slides_jobs_root(self, value: Path) -> None:
        self.agent_jobs_root = Path(value)

    @property
    def slides_documents_root(self) -> Path:
        return self.documents_root

    @slides_documents_root.setter
    def slides_documents_root(self, value: Path) -> None:
        self.documents_root = Path(value)

    @property
    def slides_output_root(self) -> Path:
        return self.agent_output_root

    @slides_output_root.setter
    def slides_output_root(self, value: Path) -> None:
        self.agent_output_root = Path(value)

    @property
    def slides_timeout_minutes(self) -> int:
        return self.agent_timeout_minutes

    @slides_timeout_minutes.setter
    def slides_timeout_minutes(self, value: int) -> None:
        self.agent_timeout_minutes = int(value)

    @property
    def slides_keep_workspace_on_failure(self) -> bool:
        return self.agent_keep_workspace_on_failure

    @slides_keep_workspace_on_failure.setter
    def slides_keep_workspace_on_failure(self, value: bool) -> None:
        self.agent_keep_workspace_on_failure = bool(value)

    @property
    def agent_max_review_rounds(self) -> int:
        """Deprecated compatibility alias for the author-attempt budget."""

        return self.agent_max_author_attempts

    @agent_max_review_rounds.setter
    def agent_max_review_rounds(self, value: int) -> None:
        self.agent_max_author_attempts = int(value)

settings = Settings()
