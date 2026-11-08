"""Runtime configuration loaded from environment variables (and an optional .env file).

Secrets are only ever read from the environment. Nothing here has a real credential default.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "JAXON OS"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: CsvList = Field(default_factory=lambda: ["http://localhost:3000"])
    # Optional shared secret for the API. When set, every /api/v1 request must send
    # `Authorization: Bearer <JAXON_API_TOKEN>`. Health endpoints stay open.
    jaxon_api_token: SecretStr | None = None

    # --- Storage ---
    database_url: str = "postgresql+asyncpg://jaxon:jaxon@localhost:5432/jaxon"
    redis_url: str = "redis://localhost:6379/0"
    db_echo: bool = False

    # --- Model provider ---
    model_provider: str = "anthropic"
    anthropic_api_key: SecretStr | None = None
    default_model: str = "claude-opus-5-5"
    # Effort for Claude 5.x models: low | medium | high | xhigh | max.
    model_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    model_max_tokens: int = 16000
    model_timeout_seconds: float = 600.0
    # Server-side refusal fallback (Anthropic beta). Re-runs a declined request on a
    # fallback model inside the same call. Set to false to disable.
    anthropic_refusal_fallback: bool = True

    # --- Agents / skills ---
    agent_max_steps: int = 12
    skill_timeout_seconds: float = 60.0
    # Extra python packages to scan for skills, comma separated (e.g. "my_company.skills").
    skill_packages: CsvList = Field(default_factory=list)

    # --- Memory ---
    embedding_dim: int = 384

    # --- Web / browser skills (authorized automation only) ---
    # Hosts the fetch/browser skills may reach. Empty = deny all outbound skill traffic.
    allowed_web_domains: CsvList = Field(default_factory=list)
    web_fetch_max_bytes: int = 2_000_000
    browser_executable_path: str | None = None

    # --- Queue ---
    run_queue_name: str = "jaxon:runs"

    # --- Integrations (optional; only reported as configured, never assumed working) ---
    github_token: SecretStr | None = None

    @field_validator("cors_origins", "skill_packages", "allowed_web_domains", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                import json

                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def sync_database_url(self) -> str:
        """URL for sync drivers (Alembic offline mode / psycopg)."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
