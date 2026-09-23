"""Process-level settings: secrets, connection strings, runtime toggles.

This is deliberately separate from `pipeline.py`. The split matters:

* `Settings` is *environment* — what differs between your laptop, CI, and prod.
  It is read from `.env` / real env vars and never committed.
* `PipelineConfig` is *experiment* — what differs between `baseline.yaml` and
  `hybrid_rerank.yaml`. It is committed, versioned, and hashed so any result in
  `experiments/` can be reproduced from `config + git SHA + dataset version`.

Mixing the two would make experiments non-reproducible (a result would depend on
whatever happened to be in someone's shell) so they stay apart.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: backend/app/core/settings.py -> backend/app/core -> backend/app -> backend -> repo
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Environment-derived settings, loaded once per process."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database ---------------------------------------------------------
    postgres_user: str = "groundtruth"
    postgres_password: str = "groundtruth"
    postgres_db: str = "groundtruth"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")

    # --- LLM provider -----------------------------------------------------
    # `anthropic` is the default and what PROJECT_SPEC.md §4 specifies.
    # `openai` targets any OpenAI-compatible endpoint -- NVIDIA NIM, Groq,
    # OpenRouter, a local Ollama -- which is how this project can be evaluated
    # without a paid key.
    gt_llm_provider: Literal["anthropic", "openai"] = "anthropic"
    gt_llm_base_url: str | None = None
    gt_llm_api_key: SecretStr | None = None

    anthropic_api_key: SecretStr | None = None

    # Per-MTok pricing for an OpenAI-compatible provider. Left at 0.0 because
    # the intended use is a free tier -- but a *wrong* zero would silently
    # report a paid run as costless, so set these when pointing at a paid
    # endpoint and the cost columns stay truthful.
    gt_llm_cost_per_mtok_in: float = 0.0
    gt_llm_cost_per_mtok_out: float = 0.0

    # --- Models (defaults; a PipelineConfig may override per experiment) ---
    gt_generation_model: str = "claude-sonnet-5"
    gt_cheap_model: str = "claude-haiku-4-5-20251001"
    gt_embedding_model: str = "BAAI/bge-small-en-v1.5"
    gt_reranker_model: str = "BAAI/bge-reranker-base"

    # --- Runtime ----------------------------------------------------------
    gt_log_level: str = "INFO"
    gt_log_format: str = "console"
    gt_llm_cache_dir: str = ".cache/llm"
    gt_llm_cache_enabled: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """SQLAlchemy URL. An explicit DATABASE_URL always wins."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_cache_path(self) -> Path:
        path = Path(self.gt_llm_cache_dir)
        return path if path.is_absolute() else REPO_ROOT / path

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_api_key(self) -> SecretStr | None:
        """The key for the active provider.

        `GT_LLM_API_KEY` wins when set, so switching providers is one variable.
        Anthropic falls back to `ANTHROPIC_API_KEY`, which is what everybody
        expects to set.
        """
        if self.gt_llm_api_key and self.gt_llm_api_key.get_secret_value():
            return self.gt_llm_api_key
        if self.gt_llm_provider == "anthropic":
            return self.anthropic_api_key
        return None

    @property
    def has_llm_key(self) -> bool:
        key = self.llm_api_key
        return bool(key and key.get_secret_value())

    @property
    def has_anthropic_key(self) -> bool:
        """Kept for the health endpoint, which reports Anthropic specifically."""
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Call `get_settings.cache_clear()` in tests that patch env."""
    return Settings()
