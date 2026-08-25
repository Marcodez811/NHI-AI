from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    redis_url: str
    openai_api_key: str
    openai_model: str = "gpt-5.6-luna"
    openai_chat_model: str = "gpt-5.6-luna"
    openai_vector_store_id: str | None = None
    database_url: str = "sqlite:///./nhi_ai.db"
    slides_jobs_root: Path = Path("/tmp/slides/jobs")
    slides_documents_root: Path = Path("/tmp/slides/documents")
    slides_output_root: Path = Path("/tmp/slides/output")
    slides_timeout_minutes: int = 45
    slides_keep_workspace_on_failure: bool = True
    max_upload_bytes: int = 250 * 1024 * 1024

    # env variables storage
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
