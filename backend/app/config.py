"""Application configuration, loaded from environment variables (see .env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    log_format: str = "auto"  # auto (pretty in development, json otherwise) | pretty | json
    log_dir: Path | None = Path("./data/logs")  # rotating JSON log files; empty to disable
    log_slow_request_ms: int = 2000  # requests slower than this are logged as warnings

    # Database: PostgreSQL in production, SQLite is fine for local development and tests.
    database_url: str = "sqlite:///./data/app.db"

    # Storage: "local" (a directory; one server, or a shared volume) or an S3-compatible object store
    # shared by every server: "r2" (Cloudflare R2) or "s3" (AWS S3, MinIO, ...).
    storage_backend: Literal["local", "s3", "r2"] = "local"
    storage_root: Path = Path("./data/storage")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None  # R2: https://<account_id>.r2.cloudflarestorage.com (EU: .eu.r2...)
    r2_account_id: str | None = None  # R2 shortcut: builds the endpoint when S3_ENDPOINT_URL is empty
    s3_region: str = "auto"  # R2 uses "auto"; AWS needs the bucket's region
    s3_access_key_id: str | None = None  # R2: an API token's Access Key ID. Empty: the AWS credential chain
    s3_secret_access_key: str | None = None
    s3_prefix: str = ""  # optional folder inside the bucket (e.g. to share a bucket between environments)

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
    # Jobs each worker process runs at the same time (threads). Jobs mostly wait on the AI provider and
    # storage, so a few per process use the machine well. SQLite always runs one at a time.
    worker_concurrency: int = Field(2, ge=1, le=32)

    # Rate limiting (expensive endpoints), requests per minute per user
    rate_limit_generate_per_min: int = 10
    rate_limit_render_per_min: int = 20

    # LLM
    llm_provider: str = "anthropic"  # anthropic | bedrock | openai_compatible | none
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "high"
    # Task-based routing. Unset task models fall back to LLM_MODEL (no switching).
    llm_model_answer: str | None = None  # writing answers; escalates to LLM_MODEL on failure
    llm_model_answer_hard: str | None = None  # hard questions (figures, maths, high marks); default LLM_MODEL
    llm_model_transcribe: str | None = None  # handwriting transcripts and OCR of scanned pages
    llm_model_verify: str | None = None  # checking automatically cut-out letters
    # Models a user may pick per task in the app (comma separated). Empty: the current Claude family
    # (with the same cloud prefix as LLM_MODEL) plus every model configured above.
    llm_model_choices: str | None = None
    llm_effort_light: str = "medium"  # effort for transcription / verification calls
    # Lower effort for small questions (<= 3 marks or "concise"); keeps LLM_EFFORT for the rest
    llm_adaptive_effort: bool = True
    # Retry a light-model task once on LLM_MODEL if the light model fails or declines
    llm_escalate: bool = True
    # Answers must reach at least this share of their target length (else regenerated once)
    llm_min_length_ratio: float = Field(0.95, ge=0.0, le=1.0)  # 0 disables the check
    llm_timeout_s: float = 300.0
    llm_max_retries: int = 2
    anthropic_api_key: str | None = None
    anthropic_fallbacks: bool = True
    # Amazon Bedrock (bedrock-runtime). Auth: AWS_BEARER_TOKEN_BEDROCK (Bedrock API key, from the environment or
    # .env) or standard AWS credentials. LLM_MODEL must be a Bedrock model / inference-profile id, e.g.
    # global.anthropic.claude-opus-5-5
    aws_region: str | None = None
    aws_bearer_token_bedrock: str | None = None
    bedrock_fallback_model: str | None = "global.anthropic.claude-opus-4-8"
    openai_compat_base_url: str | None = None
    openai_compat_api_key: str | None = None
    # Per-model prices, USD per million tokens, as JSON: {"model-id": [input, output], ...}. Models not
    # listed use Anthropic list prices (labelled as estimates for Bedrock/Vertex ids).
    llm_pricing: str | None = None
    # Optional monthly AI spending limit per user (USD); new AI work is refused once reached
    llm_monthly_budget_usd: float | None = None

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
