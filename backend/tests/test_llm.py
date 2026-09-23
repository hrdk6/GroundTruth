"""Tests for the LLM wrapper: caching, cost accounting, and model quirks.

These are the guarantees the whole eval workflow leans on, so they get real
assertions rather than smoke tests.
"""

from __future__ import annotations

import pytest

from app.core.llm import (
    CACHE_READ_MULTIPLIER,
    AnthropicProvider,
    CostTracker,
    LLMCache,
    LLMClient,
    MissingAPIKeyError,
    _supports_temperature,
    canonical_model,
    estimate_cost_usd,
)
from tests.conftest import FakeAnthropicSDK, FakeProvider, FlakyProvider


# --- pricing --------------------------------------------------------------
def test_cost_matches_published_rates() -> None:
    # Sonnet 5 is $2.00 / MTok in, $10.00 / MTok out.
    cost = estimate_cost_usd("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(2.00)
    cost = estimate_cost_usd("claude-sonnet-5", input_tokens=0, output_tokens=1_000_000)
    assert cost == pytest.approx(10.00)


def test_haiku_cheaper_than_sonnet() -> None:
    """The cheap-model split only pays off if the table says so."""
    args = {"input_tokens": 10_000, "output_tokens": 1_000}
    assert estimate_cost_usd("claude-haiku-4-5", **args) < estimate_cost_usd(
        "claude-sonnet-5", **args
    )


def test_cache_reads_are_discounted() -> None:
    full = estimate_cost_usd("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0)
    cached = estimate_cost_usd(
        "claude-sonnet-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    )
    assert cached == pytest.approx(full * CACHE_READ_MULTIPLIER)


def test_dated_model_id_resolves_to_base_pricing() -> None:
    """PROJECT_SPEC names the dated Haiku snapshot; it must not cost $0."""
    assert canonical_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert estimate_cost_usd("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.00)


def test_unknown_model_costs_zero_rather_than_crashing() -> None:
    assert estimate_cost_usd("some-future-model", 1000, 1000) == 0.0


# --- model capabilities ---------------------------------------------------
def test_temperature_support_matches_model_generation() -> None:
    # 4.6+ removed sampling params: sending temperature is a 400.
    assert not _supports_temperature("claude-sonnet-5")
    assert not _supports_temperature("claude-opus-5")
    # Haiku 4.5 still accepts them.
    assert _supports_temperature("claude-haiku-4-5")
    assert _supports_temperature("claude-haiku-4-5-20251001")


def test_anthropic_provider_drops_temperature_for_models_that_reject_it() -> None:
    """The provider owns this: sending `temperature` to Sonnet 5 is a 400."""
    provider = AnthropicProvider.__new__(AnthropicProvider)
    sdk = FakeAnthropicSDK()
    provider._client = sdk  # type: ignore[attr-defined]

    provider.invoke(
        model="claude-sonnet-5",
        prompt="hi",
        system=None,
        max_tokens=64,
        temperature=0.0,
        stop_sequences=None,
    )
    assert "temperature" not in sdk.calls[0]


def test_anthropic_provider_passes_temperature_when_supported() -> None:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    sdk = FakeAnthropicSDK()
    provider._client = sdk  # type: ignore[attr-defined]

    provider.invoke(
        model="claude-haiku-4-5",
        prompt="hi",
        system=None,
        max_tokens=64,
        temperature=0.0,
        stop_sequences=None,
    )
    assert sdk.calls[0]["temperature"] == 0.0


def test_anthropic_provider_sends_system_as_a_top_level_field() -> None:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    sdk = FakeAnthropicSDK()
    provider._client = sdk  # type: ignore[attr-defined]

    provider.invoke(
        model="claude-sonnet-5",
        prompt="q",
        system="be terse",
        max_tokens=64,
        temperature=None,
        stop_sequences=None,
    )
    assert sdk.calls[0]["system"] == "be terse"
    assert sdk.calls[0]["messages"] == [{"role": "user", "content": "q"}]


# --- provider switching ---------------------------------------------------
def test_provider_is_part_of_the_cache_key(llm_client: LLMClient) -> None:
    """The same prompt on a different backend is a different call.

    Serving one provider's cached answer for another would silently mix two
    systems' outputs into one experiment.
    """
    fake = FakeProvider()
    llm_client._client = fake
    llm_client.complete("q", model="m")

    llm_client.settings = llm_client.settings.model_copy(
        update={"gt_llm_provider": "openai", "gt_llm_base_url": "https://example.test/v1"}
    )
    llm_client.complete("q", model="m")

    assert len(fake.calls) == 2, "switching provider must not reuse the cached entry"


def test_rate_limit_is_retried(llm_client: LLMClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Free tiers are rate limited; a long eval must survive a 429."""
    monkeypatch.setattr("app.core.llm.time.sleep", lambda _: None)
    flaky = FlakyProvider(fail_times=2)
    llm_client._client = flaky

    response = llm_client.complete("q", model="m")
    assert response.text == "hello"
    assert len(flaky.calls) == 3, "two failures then a success"


def test_non_rate_limit_errors_are_not_retried(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(error=ValueError("bad model id"))
    with pytest.raises(ValueError, match="bad model id"):
        llm_client.complete("q", model="m")


def test_openai_provider_cost_uses_configured_rates(settings, llm_cache) -> None:
    """Zero is right for a free tier; a paid endpoint must be able to say so."""
    paid = settings.model_copy(
        update={
            "gt_llm_provider": "openai",
            "gt_llm_cost_per_mtok_in": 1.0,
            "gt_llm_cost_per_mtok_out": 3.0,
        }
    )
    client = LLMClient(settings=paid, cache=llm_cache, tracker=CostTracker())
    client._client = FakeProvider(input_tokens=1_000_000, output_tokens=1_000_000)

    response = client.complete("q", model="whatever")
    assert response.cost_usd == pytest.approx(4.0)


def test_openai_provider_cost_is_zero_by_default(settings, llm_cache) -> None:
    free = settings.model_copy(update={"gt_llm_provider": "openai"})
    client = LLMClient(settings=free, cache=llm_cache, tracker=CostTracker())
    client._client = FakeProvider(input_tokens=1_000_000, output_tokens=1_000_000)

    assert client.complete("q", model="whatever").cost_usd == 0.0


# --- caching --------------------------------------------------------------
def test_identical_calls_hit_the_cache(llm_client: LLMClient) -> None:
    fake = FakeProvider(text="cached answer")
    llm_client._client = fake

    first = llm_client.complete("What is a Pod?", model="claude-sonnet-5")
    second = llm_client.complete("What is a Pod?", model="claude-sonnet-5")

    assert len(fake.calls) == 1, "second identical call should not reach the API"
    assert first.cached is False
    assert second.cached is True
    assert second.text == first.text


def test_cache_hit_costs_nothing_but_remembers_list_price(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(input_tokens=1000, output_tokens=500)

    first = llm_client.complete("q", model="claude-sonnet-5")
    second = llm_client.complete("q", model="claude-sonnet-5")

    assert first.cost_usd > 0
    assert second.cost_usd == 0.0
    assert second.list_cost_usd == pytest.approx(first.list_cost_usd)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "claude-haiku-4-5"},
        {"max_tokens": 4096},
        {"system": "different system prompt"},
    ],
)
def test_changing_any_parameter_misses_the_cache(llm_client: LLMClient, kwargs: dict) -> None:
    fake = FakeProvider()
    llm_client._client = fake
    base = {"model": "claude-sonnet-5", "max_tokens": 1024}

    llm_client.complete("q", **base)
    llm_client.complete("q", **{**base, **kwargs})

    assert len(fake.calls) == 2


def test_disabled_cache_always_calls_the_api(llm_client: LLMClient) -> None:
    fake = FakeProvider()
    llm_client._client = fake
    llm_client.complete("q", use_cache=False)
    llm_client.complete("q", use_cache=False)
    assert len(fake.calls) == 2


def test_corrupt_cache_entry_is_a_miss_not_a_crash(llm_cache: LLMCache) -> None:
    key = LLMCache.make_key({"model": "m", "messages": []})
    path = llm_cache._path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert llm_cache.get(key) is None


# --- cost tracking --------------------------------------------------------
def test_tracker_accumulates_across_calls(llm_client: LLMClient) -> None:
    llm_client._client = FakeProvider(input_tokens=100, output_tokens=50)

    llm_client.complete("a", model="claude-sonnet-5")
    llm_client.complete("b", model="claude-sonnet-5")
    llm_client.complete("a", model="claude-sonnet-5")  # cache hit

    stats = llm_client.tracker.to_dict()
    assert stats["calls"] == 3
    assert stats["cached_calls"] == 1
    assert stats["cache_hit_rate"] == pytest.approx(1 / 3)
    assert stats["cost_usd"] > 0
    assert "claude-sonnet-5" in stats["by_model"]


# --- key handling ---------------------------------------------------------
def test_missing_key_raises_only_when_a_call_is_made(llm_client: LLMClient) -> None:
    """Retrieval-only evals build the client and never call it; CI has no key."""
    with pytest.raises(MissingAPIKeyError):
        llm_client.complete("q")
