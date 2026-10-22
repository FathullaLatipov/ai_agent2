"""Application settings. All configuration comes from the environment (AGENTOS_ prefix)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root .env (two levels up from this file: backend/app/core -> repo root)
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTOS_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    secret_key: str = Field(min_length=16)
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Comma-separated list of allowed browser origins.
    frontend_origin: str = "http://localhost:3000"

    @property
    def frontend_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agentos"
    test_database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agentos_test"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: Literal["anthropic", "none"] = "none"
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"

    embeddings_provider: Literal["voyage", "openai", "local-hash"] = "local-hash"
    voyage_api_key: str = ""
    openai_api_key: str = ""

    upload_dir: str = "./var/uploads"
    max_upload_bytes: int = 25 * 1024 * 1024
    max_request_bytes: int = 10 * 1024 * 1024

    access_token_minutes: int = 60
    stream_token_seconds: int = 60

    max_plan_tasks: int = 8
    max_tool_calls_per_task: int = 15
    max_llm_calls_per_run: int = 40
    run_timeout_s: int = 600
    task_timeout_s: int = 180
    max_review_iterations: int = 2
    approval_ttl_hours: int = 24

    mcp_config_path: str = "./mcp.servers.json"

    rate_limit_auth_per_minute: int = 10
    rate_limit_default_per_minute: int = 120

    @property
    def effective_database_url(self) -> str:
        return self.test_database_url if self.env == "test" else self.database_url

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider == "anthropic" and bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
