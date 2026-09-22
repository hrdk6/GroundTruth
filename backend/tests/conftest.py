"""Shared fixtures.

Tests must never touch the developer's real LLM cache or read their `.env`, so
fixtures here redirect both to a tmp path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.llm import CostTracker, LLMCache, LLMClient
from app.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep `get_settings()` from leaking state between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings isolated from the developer's `.env` and caches."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        postgres_host="localhost",
        gt_llm_cache_dir=str(tmp_path / "llm-cache"),
        anthropic_api_key=None,
    )


@pytest.fixture
def llm_cache(tmp_path: Path) -> LLMCache:
    return LLMCache(tmp_path / "llm-cache", enabled=True)


@pytest.fixture
def llm_client(settings: Settings, llm_cache: LLMCache) -> LLMClient:
    return LLMClient(settings=settings, cache=llm_cache, tracker=CostTracker())


class FakeUsage:
    def __init__(
        self,
        input_tokens: int = 100,
        output_tokens: int = 50,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeMessage:
    """Stands in for `anthropic.types.Message` with the fields we read."""

    def __init__(self, text: str = "hello", usage: FakeUsage | None = None) -> None:
        self.content = [FakeTextBlock(text)]
        self.usage = usage or FakeUsage()
        self.stop_reason = "end_turn"
        self._request_id = "req_test123"


class FakeAnthropic:
    """Records the kwargs each call was made with, so tests can assert on them."""

    def __init__(self, text: str = "hello", usage: FakeUsage | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._text = text
        self._usage = usage
        self.messages = self

    def create(self, **kwargs: object) -> FakeMessage:
        self.calls.append(kwargs)
        return FakeMessage(self._text, self._usage)
