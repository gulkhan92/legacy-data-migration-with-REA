from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Runtime connection: the application role, subject to Row Level Security.
    database_url: str = "postgresql+psycopg://legacylift_app:change-me-app@localhost:5544/legacylift"
    # Migration connection: the owner role. Never used by request handling code.
    database_admin_url: str = "postgresql+psycopg://legacylift:change-me-owner@localhost:5544/legacylift"

    app_db_user: str = "legacylift_app"
    app_db_password: str = "change-me-app"

    embedding_dimensions: int = 384


@lru_cache
def get_settings() -> Settings:
    return Settings()
