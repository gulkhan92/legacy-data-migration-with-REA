from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development only values. Startup refuses them when ENVIRONMENT is not "local" or "test".
DEV_JWT_SECRET = "dev-only-jwt-secret-change-me-before-any-shared-use-0123456789"
DEV_ENCRYPTION_KEY = "aD9nzMUw42jmxquM/GvfmcZwWzAD4fqgXuSisylD+WA="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    environment: str = "local"

    # Runtime connection: the application role, subject to Row Level Security.
    database_url: str = "postgresql+psycopg://legacylift_app:change-me-app@localhost:5544/legacylift"
    # Migration connection: the owner role. Never used by request handling code.
    database_admin_url: str = "postgresql+psycopg://legacylift:change-me-owner@localhost:5544/legacylift"

    app_db_user: str = "legacylift_app"
    app_db_password: str = "change-me-app"

    embedding_dimensions: int = 384
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = "./var/models"
    mapping_thresholds_file: str = ""

    # Authentication
    jwt_secret: str = DEV_JWT_SECRET
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    password_min_length: int = 12
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    csrf_header_name: str = "X-Requested-With"
    csrf_header_value: str = "LegacyLift"

    # HTTP security
    cors_origins: str = "http://localhost:5173"
    rate_limit_enabled: bool = True
    login_rate_limit: str = "5/minute"
    default_rate_limit: str = "300/minute"
    hsts_enabled: bool = True

    # REA static analysis. The command is run without a shell and must end where the REA command
    # name is appended. Default: the wrapper script in the repository run with the local Node.js.
    # In a container deployment: "docker run --rm --network none --read-only ... -v {workdir}:/work:ro
    # legacylift-rea-runner:6.7.0" together with REA_CONTAINER_WORKDIR=/work.
    rea_command: str = ""
    rea_timeout_seconds: int = 120
    rea_container_workdir: str = ""
    rea_sample_rows: int = 5

    # LLM router (Phase 7). Keys come from the environment only and are never stored or logged.
    groq_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    groq_model: str = "openai/gpt-oss-20b"
    # Confirm the current free models and quotas in Google AI Studio before choosing a default.
    # A Flash-Lite class model is the safest start.
    gemini_model: str = "gemini-2.5-flash-lite"
    llm_provider_order: str = "groq,gemini"
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 800
    llm_job_token_budget: int = 6000
    llm_org_daily_token_budget: int = 100_000
    llm_max_calls_per_job: int = 2
    llm_retry_attempts: int = 2  # tries per provider for 429, 5xx and timeouts
    llm_backoff_base_seconds: float = 0.5
    llm_backoff_cap_seconds: float = 8.0
    llm_max_retry_after_seconds: float = 10.0  # a longer Retry-After means failover, not waiting
    llm_breaker_failures: int = 3
    llm_breaker_open_seconds: float = 60.0
    groq_tokens_per_minute: int = 8000
    groq_requests_per_minute: int = 30
    gemini_tokens_per_minute: int = 250_000
    gemini_requests_per_minute: int = 15

    # Job orchestration (Phase 9). "celery" sends stages to workers through Redis; "eager" runs them inside the
    # API process (local development without Redis, tests). Eager is refused outside local use.
    redis_url: str = "redis://localhost:6379/0"
    jobs_execution: str = "celery"
    job_task_max_retries: int = 3  # retries of a stage after a transient failure (database or network), with backoff
    job_task_retry_base_seconds: int = 5
    job_stage_busy_retries: int = 20  # a second stage for the same job waits for the first one this many times
    job_task_time_limit_seconds: int = 3600
    # Analytics responses are cached for a minute: in Redis, in memory (local work), or not at all.
    cache_backend: str = "redis"
    analytics_cache_seconds: int = 60
    sse_poll_seconds: float = 0.5
    sse_heartbeat_seconds: float = 15.0
    sse_max_seconds: int = 300  # a stream ends after this long; the browser reconnects with Last-Event-ID

    # Uploads
    upload_dir: str = "./var/uploads"
    upload_max_bytes: int = 50 * 1024 * 1024
    upload_encryption_key: str = DEV_ENCRYPTION_KEY  # base64 of 32 random bytes

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _refuse_dev_secrets_outside_dev(self) -> "Settings":
        if self.cache_backend not in {"redis", "memory", "off"}:
            raise ValueError("CACHE_BACKEND must be redis, memory or off")
        if self.jobs_execution not in {"celery", "eager"}:
            raise ValueError("JOBS_EXECUTION must be celery or eager")
        if self.environment not in {"local", "test"} and self.jobs_execution == "eager":
            raise ValueError("JOBS_EXECUTION=eager would run long stages inside the API process; use celery")
        if self.environment not in {"local", "test"}:
            if self.jwt_secret == DEV_JWT_SECRET or self.upload_encryption_key == DEV_ENCRYPTION_KEY:
                raise ValueError("JWT_SECRET and UPLOAD_ENCRYPTION_KEY must be set outside local use")
            if len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
