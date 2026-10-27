"""Application configuration, loaded from environment variables (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # Database: PostgreSQL in production, SQLite is fine for local development and tests.
    database_url: str = "sqlite:///./data/app.db"

    # Storage
    storage_backend: str = "local"
    storage_root: Path = Path("./data/storage")

    # Auth
    secret_key: str = Field(default="dev-insecure-change-me-0123456789abcdef", min_length=32)
    access_token_ttl_minutes: int = 60 * 24 * 7
    cookie_secure: bool = False
    allow_registration: bool = True

    # CORS (only needed when the frontend is served from a different origin)
    cors_origins: list[str] = ["http://localhost:3000"]

    # Upload limits
    max_upload_mb: int = 25
    max_pdf_pages: int = 40
    max_sample_files_per_profile: int = 20
    max_questions_per_generation: int = 60

    # Retention: 0 disables automatic deletion
    retention_days: int = 0

    # Jobs
    job_max_attempts: int = 3
    job_poll_interval_s: float = 1.0
    job_stale_after_s: int = 900
    run_worker_in_process: bool = False

    # Rate limiting (expensive endpoints), requests per minute per user
    rate_limit_generate_per_min: int = 10
    rate_limit_render_per_min: int = 20

    # LLM
    llm_provider: str = "anthropic"  # anthropic | bedrock | openai_compatible | none
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "high"
    # Task-based routing. Unset task models fall back to LLM_MODEL (no switching).
    llm_model_transcribe: str | None = None  # handwriting transcripts and OCR of scanned pages
    llm_model_verify: str | None = None  # checking automatically cut-out letters
    llm_effort_light: str = "medium"  # effort for transcription / verification calls
    # Lower effort for small questions (<= 3 marks or "concise"); keeps LLM_EFFORT for the rest
    llm_adaptive_effort: bool = True
    # Retry a light-model task once on LLM_MODEL if the light model fails or declines
    llm_escalate: bool = True
    llm_timeout_s: float = 300.0
    llm_max_retries: int = 2
    anthropic_api_key: str | None = None
    anthropic_fallbacks: bool = True
    # Amazon Bedrock (bedrock-runtime). Auth: AWS_BEARER_TOKEN_BEDROCK (Bedrock API key) or standard AWS
    # credentials. LLM_MODEL must be a Bedrock model / inference-profile id, e.g. global.anthropic.claude-opus-5-5
    aws_region: str | None = None
    bedrock_fallback_model: str | None = "global.anthropic.claude-opus-4-8"
    openai_compat_base_url: str | None = None
    openai_compat_api_key: str | None = None
    # Optional pricing override, USD per million tokens, used for cost estimates
    llm_input_price_per_mtok: float | None = None
    llm_output_price_per_mtok: float | None = None

    # OCR: auto | tesseract | llm | none
    ocr_provider: str = "auto"
    tesseract_cmd: str | None = None

    # Rendering
    fallback_font_path: str | None = None

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
