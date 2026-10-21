from functools import lru_cache

from pydantic import model_validator
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

    # Uploads
    upload_dir: str = "./var/uploads"
    upload_max_bytes: int = 50 * 1024 * 1024
    upload_encryption_key: str = DEV_ENCRYPTION_KEY  # base64 of 32 random bytes

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _refuse_dev_secrets_outside_dev(self) -> "Settings":
        if self.environment not in {"local", "test"}:
            if self.jwt_secret == DEV_JWT_SECRET or self.upload_encryption_key == DEV_ENCRYPTION_KEY:
                raise ValueError("JWT_SECRET and UPLOAD_ENCRYPTION_KEY must be set outside local use")
            if len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
