"""Server-owned configuration for provider and workflow policy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, SecretStr, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from app.services.agentic.contracts import AgentReasoningEffort

DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.yaml"


def config_file_path() -> Path:
    return Path(os.environ.get("CONFIG_FILE") or DEFAULT_CONFIG_FILE)


class ModelEntry(BaseModel):
    """One ``models:`` entry of config.yaml."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    provider: Literal["openai", "anthropic", "gemini"]
    context_window: int = Field(default=128_000, gt=0)
    use: list[Literal["chat", "agents", "codex"]] = Field(min_length=1)


# Nested YAML path -> flat ``Settings`` field. Env var names are unchanged.
YAML_FIELD_MAP: dict[tuple[str, ...], str] = {
    ("chat", "default_model"): "chat_default_model",
    ("chat", "timeout_seconds"): "chat_timeout_seconds",
    ("chat", "stream_heartbeat_seconds"): "chat_stream_heartbeat_seconds",
    ("chat", "compaction_threshold"): "chat_compaction_threshold",
    ("agents", "default_model"): "agent_default_model",
    ("agents", "default_reasoning_effort"): "agent_default_reasoning_effort",
    **{
        ("agents", "stages", stage, key): f"agent_{stage}_{field}"
        for stage in ("extraction", "planner", "author", "reviewer")
        for key, field in (("runner", "runner"), ("model", "model"), ("reasoning_effort", "reasoning_effort"))
    },
    ("agents", "stages", "planner", "enabled"): "agent_planner_enabled",
    **{
        ("agents", "limits", key): field
        for key, field in {
            "timeout_minutes": "agent_timeout_minutes",
            "heartbeat_seconds": "agent_heartbeat_seconds",
            "max_author_attempts": "agent_max_author_attempts",
            "review_stagnation_limit": "agent_review_stagnation_limit",
            "planner_max_evidence_chars": "agent_planner_max_evidence_chars",
            "awaiting_outline_ttl_seconds": "agent_awaiting_outline_ttl_seconds",
            "awaiting_outline_sweep_interval_seconds": "agent_awaiting_outline_sweep_interval_seconds",
            "event_retention_seconds": "agent_event_retention_seconds",
            "keep_workspace_on_failure": "agent_keep_workspace_on_failure",
            "require_process_isolation": "agent_require_process_isolation",
        }.items()
    },
    ("uploads", "max_upload_bytes"): "max_upload_bytes",
    **{
        ("uploads", "chat", key): f"chat_{key}"
        for key in (
            "max_attachments",
            "max_message_chars",
            "max_document_bytes",
            "max_pdf_bytes",
            "max_pdf_pages",
            "max_image_bytes",
            "max_image_edge",
        )
    },
    ("cleanup", "lease_seconds"): "document_cleanup_lease_seconds",
    ("cleanup", "retry_base_seconds"): "document_cleanup_retry_base_seconds",
    ("cleanup", "retry_max_seconds"): "document_cleanup_retry_max_seconds",
    ("cleanup", "reconcile_interval_seconds"): "document_cleanup_reconcile_interval_seconds",
    ("cleanup", "reconcile_batch_size"): "document_cleanup_reconcile_batch_size",
    ("knowledge_base", "vector_store_name"): "openai_vector_store_name",
    ("knowledge_base", "bootstrap_timeout_seconds"): "openai_vector_store_bootstrap_timeout_seconds",
}
_FIELD_TO_YAML_PATH = {field: ".".join(path) for path, field in YAML_FIELD_MAP.items()}
_FIELD_TO_YAML_PATH["catalog_models"] = "models"


def _leaves(node: Any, path: tuple[str, ...] = ()):
    if isinstance(node, dict) and node:
        for key, value in node.items():
            yield from _leaves(value, (*path, str(key)))
    else:
        yield path, node


def load_yaml_values(path: Path | None = None) -> dict[str, Any]:
    """Flatten config.yaml into ``Settings`` field values; errors name the YAML path."""

    path = path or config_file_path()
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    values: dict[str, Any] = {}
    models = raw.pop("models", None)
    for leaf_path, value in _leaves(raw):
        if not leaf_path:
            continue
        field = YAML_FIELD_MAP.get(leaf_path)
        if field is None:
            raise ValueError(f"{path}: unknown setting '{'.'.join(leaf_path)}'")
        values[field] = value
    if models is not None:
        if not isinstance(models, list):
            raise ValueError(f"{path}: models must be a list")
        entries: list[ModelEntry] = []
        seen: set[str] = set()
        for index, item in enumerate(models):
            try:
                entry = ModelEntry.model_validate(item)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(part) for part in first["loc"])
                raise ValueError(f"{path}: models[{index}].{where}: {first['msg']}") from exc
            if entry.id in seen:
                raise ValueError(f"{path}: models[{index}].id: duplicate model id '{entry.id}'")
            seen.add(entry.id)
            entries.append(entry)
        values["catalog_models"] = entries
    return values


class ConfigYamlSource(PydanticBaseSettingsSource):
    """Lowest-priority source: values from config.yaml (``CONFIG_FILE`` overrides the path)."""

    def __init__(self, settings_cls):
        super().__init__(settings_cls)
        self._values = load_yaml_values()

    def get_field_value(self, field, field_name):  # pragma: no cover - unused, __call__ is used
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._values)


class Settings(BaseSettings):
    """Keep optional provider credentials separate from model selection."""

    redis_url: str
    openai_api_key: SecretStr
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    # Agent model policy is separate from the chat/vector-store settings.
    agent_default_model: str = Field(
        default="gpt-6-luna",
        min_length=1,
        validation_alias=AliasChoices("AGENT_DEFAULT_MODEL"),
    )
    agent_author_model: str | None = Field(
        default="gpt-6-luna",
        min_length=1,
        validation_alias=AliasChoices("AGENT_AUTHOR_MODEL"),
    )
    agent_reviewer_model: str | None = Field(
        default="gpt-6.1-sol",
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
    chat_default_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CHAT_DEFAULT_MODEL"),
    )
    chat_compaction_threshold: float = Field(
        default=0.6,
        gt=0,
        lt=1,
        validation_alias=AliasChoices("CHAT_COMPACTION_THRESHOLD"),
    )
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
    documents_root: Path = Field(
        default=Path("/tmp/documents"),
        validation_alias=AliasChoices("DOCUMENTS_ROOT"),
    )
    agent_jobs_root: Path = Field(
        default=Path("/tmp/agents/jobs"),
        validation_alias=AliasChoices("AGENT_JOBS_ROOT"),
    )
    agent_output_root: Path = Field(
        default=Path("/tmp/agents/output"),
        validation_alias=AliasChoices("AGENT_OUTPUT_ROOT"),
    )
    agent_timeout_minutes: int = Field(
        default=60,
        gt=0,
        validation_alias=AliasChoices("AGENT_TIMEOUT_MINUTES"),
    )
    agent_heartbeat_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias=AliasChoices("AGENT_HEARTBEAT_SECONDS"),
    )
    agent_keep_workspace_on_failure: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGENT_KEEP_WORKSPACE_ON_FAILURE"),
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
    enable_dev_settings: bool = Field(
        default=False,
        validation_alias=AliasChoices("ENABLE_DEV_SETTINGS"),
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
    # Chat upload/message limits (config.yaml uploads.chat.*).
    chat_max_attachments: int = Field(default=10, ge=1)
    chat_max_message_chars: int = Field(default=20_000, ge=1)
    chat_max_document_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    chat_max_pdf_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    chat_max_pdf_pages: int = Field(default=100, ge=1)
    chat_max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    chat_max_image_edge: int = Field(default=1568, ge=1)
    # Model catalog (config.yaml models:), validated by ``ModelEntry``.
    catalog_models: list[ModelEntry] = Field(default_factory=list)

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        # init > env > .env > config.yaml > code defaults
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            ConfigYamlSource(settings_cls),
        )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_constructor_names(cls, values):
        """Accept the old Python keyword name with the canonical name taking priority."""

        if not isinstance(values, dict):
            return values
        values = dict(values)
        if "agent_max_author_attempts" not in values and "agent_max_review_rounds" in values:
            values["agent_max_author_attempts"] = values["agent_max_review_rounds"]
        return values

    @property
    def agent_max_review_rounds(self) -> int:
        """Deprecated compatibility alias for the author-attempt budget."""

        return self.agent_max_author_attempts

    @agent_max_review_rounds.setter
    def agent_max_review_rounds(self, value: int) -> None:
        self.agent_max_author_attempts = int(value)


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        if not any(str(e["loc"][0]) in _FIELD_TO_YAML_PATH for e in exc.errors() if e["loc"]):
            raise
        lines = []
        for error in exc.errors():
            field = str(error["loc"][0]) if error["loc"] else "?"
            lines.append(f"{_FIELD_TO_YAML_PATH.get(field, field)}: {error['msg']}")
        raise RuntimeError("Invalid configuration (config.yaml / environment): " + "; ".join(lines)) from exc


settings = _load_settings()
