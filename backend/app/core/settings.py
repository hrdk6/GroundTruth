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

from pydantic import Field, SecretStr, computed_field, model_validator
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
    # Concurrent model calls per request (claim verification fans out one
    # call per sentence). Environment, not experiment: it changes latency and
    # nothing else, since every call is cached under its own prompt.
    gt_llm_max_concurrency: int = Field(default=4, ge=1, le=32)
    # Provider invocations per model call, retries included. Five suits an
    # eval, which would rather wait out a rate limit than lose a run. A web
    # request should give up sooner: with a 180s provider timeout, five
    # attempts can hold a query -- and its concurrency slot -- for 15 minutes.
    gt_llm_max_attempts: int = Field(default=5, ge=1, le=10)

    # --- API ----------------------------------------------------------------
    # The pipeline `/query` runs when the caller names none. `full` is the
    # shipping configuration; `baseline` exists to be beaten, and serving it by
    # default would hide verification and conflict notes behind a dropdown.
    gt_default_config: str = "full"
    # Configs `/query` will run, as a JSON list (`["full","baseline"]`). Unset
    # serves every file in configs/. A public deployment should list what it
    # can afford: a rerank config loads a ~1 GB model on first use (L27).
    gt_served_configs: list[str] | None = None
    # Queries the API runs at once. Each holds a worker thread, CPU for the
    # encoder, and model calls against the provider's rate limit; past this
    # many, a request waits up to `gt_query_queue_seconds`, then gets a 503.
    gt_max_concurrent_queries: int = Field(default=4, ge=1, le=64)
    gt_query_queue_seconds: float = Field(default=20.0, ge=0.0, le=600.0)
    # Queries accepted per minute across all clients; 0 disables the limit.
    # Global rather than per client: behind the frontend's proxy every
    # request arrives from one address, and what runs out is the provider's
    # quota, which every client shares. A full query makes ~5-10 model calls
    # against a free tier allowing 40 a minute.
    gt_query_rate_per_minute: int = Field(default=30, ge=0)
    # Load the default config's embedding model at startup, so the first
    # query does not wait for it (36 s on the dev machine).
    gt_warm_models: bool = True
    # Origins allowed to call the API from a browser.
    gt_cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # Shared secret for admin endpoints (`POST /ingest`). Unset disables them:
    # an endpoint that re-embeds the corpus should not be open by default.
    gt_admin_token: SecretStr | None = None

    # --- Observability -----------------------------------------------------
    # Traces older than this are deleted by the API every few hours
    # (`app.tracing.retention`). 0 keeps them forever.
    gt_trace_retention_days: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def _default_config_is_served(self) -> Settings:
        # Otherwise every question asked without naming a pipeline -- which is
        # every question from the Ask page's default -- is a 404.
        if self.gt_served_configs is not None and self.gt_default_config not in (
            self.gt_served_configs
        ):
            raise ValueError(
                f"GT_DEFAULT_CONFIG={self.gt_default_config!r} is not in "
                f"GT_SERVED_CONFIGS={self.gt_served_configs}"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """SQLAlchemy URL. An explicit DATABASE_URL always wins.

        Managed Postgres hands out `postgres://` or `postgresql://`. SQLAlchemy
        reads the first as an unknown dialect and the second as psycopg2, which
        is not installed, so either one pasted as-is fails at the first query.
        Both are rewritten to the one driver this project uses.
        """
        if self.database_url_override:
            url = self.database_url_override
            for scheme in ("postgres://", "postgresql://"):
                if url.startswith(scheme):
                    return "postgresql+psycopg://" + url.removeprefix(scheme)
            return url
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
