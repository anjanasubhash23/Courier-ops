"""Typed configuration from environment variables / .env, validated at startup."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Same variables docker-compose.yml uses to initialise the Postgres container.
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "courier"
    postgres_password: SecretStr = Field(min_length=8)
    postgres_db: str = "courier_ops"

    # Signs login tokens. Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: SecretStr = Field(min_length=32)
    token_minutes: int = Field(default=480, ge=5)

    # LLM for Text-to-SQL and triage. "none" disables both; everything else works without a model.
    llm_provider: Literal["none", "ollama", "openai", "anthropic"] = "none"
    llm_model: str | None = None
    llm_base_url: str | None = None  # default: the provider's usual endpoint
    llm_api_key: SecretStr | None = None  # openai; anthropic falls back to ANTHROPIC_API_KEY or an `ant` profile
    llm_timeout_seconds: float = Field(default=120, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_max_tokens: int = Field(default=4096, ge=256)
    llm_temperature: float | None = Field(default=0, ge=0, le=2)  # ignored by anthropic

    @model_validator(mode="after")
    def _llm_complete(self) -> Self:
        if self.llm_provider != "none" and not self.llm_model:
            raise ValueError(f"LLM_MODEL is required when LLM_PROVIDER={self.llm_provider}")
        if self.llm_provider == "openai" and not self.llm_base_url and not self.llm_api_key:
            raise ValueError("LLM_API_KEY is required for api.openai.com (or set LLM_BASE_URL for a local server)")
        return self

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters in the password.
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
