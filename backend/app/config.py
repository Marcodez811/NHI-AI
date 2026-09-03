from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.agentic.contracts import AgentReasoningEffort


class Settings(BaseSettings):
    redis_url: str
    openai_api_key: str
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
    openai_chat_model: str = "gpt-5.6-luna"
    openai_vector_store_id: str | None = None
    openai_vector_store_name: str = "NHI-AI Knowledge Base"
    openai_vector_store_bootstrap_timeout_seconds: float = Field(default=10.0, gt=0)
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
