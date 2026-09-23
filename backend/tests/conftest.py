"""Shared fixtures.

Tests must never touch the developer's real LLM cache or read their `.env`, so
fixtures here redirect both to a tmp path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.llm import CostTracker, LLMCache, LLMClient, ProviderResult
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


class FakeProvider:
    """Stands in for a provider, recording the kwargs it was invoked with.

    The client talks to providers through `invoke(...)`, so this is the seam
    both the Anthropic and OpenAI-compatible backends share.
    """

    name = "fake"

    def __init__(
        self,
        text: str = "hello",
        input_tokens: int = 100,
        output_tokens: int = 50,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._text = text
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._error = error

    def invoke(self, **kwargs: object) -> ProviderResult:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return ProviderResult(
            text=self._text,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            stop_reason="end_turn",
            request_id="req_test123",
        )


class FlakyProvider(FakeProvider):
    """Rate-limits for the first `fail_times` calls, then succeeds."""

    def __init__(self, fail_times: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.fail_times = fail_times

    def invoke(self, **kwargs: object) -> ProviderResult:
        if len(self.calls) < self.fail_times:
            self.calls.append(kwargs)
            raise RuntimeError("429 rate limit exceeded")
        return super().invoke(**kwargs)


class _FakeAnthropicUsage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAnthropicMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeAnthropicUsage()
        self.stop_reason = "end_turn"
        self._request_id = "req_test123"


class FakeAnthropicSDK:
    """The slice of the Anthropic SDK that AnthropicProvider actually uses."""

    def __init__(self, text: str = "hello") -> None:
        self.calls: list[dict[str, object]] = []
        self._text = text
        self.messages = self

    def create(self, **kwargs: object) -> _FakeAnthropicMessage:
        self.calls.append(kwargs)
        return _FakeAnthropicMessage(self._text)
