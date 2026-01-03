"""Configuration management using pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required
    linear_api_key: str = Field(description="Linear API key")

    # CLI commands
    claude_cmd: str = Field(default="claude", description="Claude CLI command")
    codex_cmd: str = Field(default="codex", description="Codex CLI command")

    # HTTP settings
    http_timeout_secs: int = Field(default=60, description="HTTP timeout in seconds")

    # Pipeline defaults
    dry_run_default: bool = Field(default=True, description="Default dry-run mode")
    max_iterations_default: int = Field(default=5, description="Max plan iterations")
    confidence_threshold_default: int = Field(
        default=97, description="Confidence threshold (0-100)"
    )
    stable_passes_default: int = Field(
        default=2, description="Required stable passes"
    )

    # Feature flags
    no_linear_writeback_default: bool = Field(
        default=False, description="Disable Linear comment writeback"
    )
    use_worktree_default: bool = Field(
        default=True, description="Use git worktree for isolation"
    )

    # Optional CI auth
    codex_api_key: str | None = Field(
        default=None, description="Codex API key (CI only)"
    )

    # OpenAI critique settings
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    critique_model: str = Field(default="gpt-4.1", description="Model for critique")
    critique_max_concurrent: int = Field(
        default=3, description="Max concurrent critique API calls"
    )


def get_settings() -> Settings:
    """Get application settings, loading from .env if present."""
    return Settings()


def get_prompts_dir() -> Path:
    """Get the prompts directory path."""
    # __file__ is src/ai_loop/config.py, go up to ai-loop/ then into prompts/
    return Path(__file__).parent.parent.parent / "prompts"


def get_schemas_dir() -> Path:
    """Get the schemas directory path."""
    return Path(__file__).parent.parent.parent / "schemas"
