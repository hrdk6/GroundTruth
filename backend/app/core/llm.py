"""The single door to whichever LLM provider is configured.

Everything that talks to a model goes through `LLMClient` - generation, the
judge, query rewriting, claim verification. That is a deliberate constraint from
PROJECT_SPEC.md S3.4, and it buys three things:

1. **A persistent cache** keyed on `(model, prompt, params)`. Re-running an eval
   over 300 items costs nothing the second time, which is what makes the
   "measure everything" workflow affordable.
2. **Honest cost accounting.** Every call records tokens and dollars, so a
   per-query cost lands in the trace and a per-run total lands in the
   experiment file. Nothing is estimated after the fact.
3. **One place for model quirks.** Current Claude models reject parameters
   that older ones required (see `_supports_temperature`), and getting that
   wrong is a 400 at request time rather than a type error.
4. **One place to swap backends.** `GT_LLM_PROVIDER` selects Anthropic (the
   default, per PROJECT_SPEC.md S4) or any OpenAI-compatible endpoint, so the
   harness can be run on a free tier. The provider is part of the cache key
   and is recorded on every experiment, so results stay attributable to the
   model that produced them.

Cache semantics worth knowing: a cache *hit* reports `cost_usd == 0.0` because
no money changed hands, while `list_cost_usd` keeps what the call would have
cost. Experiment totals use `cost_usd`, so a re-run honestly reports ~$0.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings

log = get_logger(__name__)

# Bump when the cached payload shape changes, to invalidate old entries.
CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
# USD per million tokens, from the Anthropic pricing table (cached 2026-06-24).
# Keep this table honest: a wrong number here silently corrupts every cost
# figure in the README.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    # model id: (input, output)
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Dated snapshot ids resolve to the same pricing as their base model.
_MODEL_ALIASES: dict[str, str] = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
}

# Cache reads are billed at ~0.1x the input rate; cache writes at ~1.25x.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


def canonical_model(model: str) -> str:
    return _MODEL_ALIASES.get(model, model)


def _supports_temperature(model: str) -> bool:
    """Whether the model accepts sampling parameters.

    Claude 4.6 and later removed `temperature`/`top_p`/`top_k`: sending them is
    a 400, not a warning. Only Haiku 4.5 and older models still take them.

    The practical consequence for this project: we cannot pin `temperature=0`
    on the generation model, so answers are not bit-for-bit reproducible from
    the API alone. The LLM cache is what gives eval runs their determinism,
    which is a good reason to keep it enabled when reproducing a result.
    """
    m = canonical_model(model)
    return m.startswith(("claude-haiku-4-5", "claude-3", "claude-sonnet-4-5", "claude-opus-4-5"))


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Dollar cost of one call. Unknown models cost 0 and warn loudly."""
    rates = PRICING_PER_MTOK.get(canonical_model(model))
    if rates is None:
        log.warning("llm.unknown_model_pricing", model=model)
        return 0.0
    input_rate, output_rate = rates
    per_token_in = input_rate / 1_000_000
    per_token_out = output_rate / 1_000_000
    return (
        input_tokens * per_token_in
        + output_tokens * per_token_out
        + cache_read_tokens * per_token_in * CACHE_READ_MULTIPLIER
        + cache_write_tokens * per_token_in * CACHE_WRITE_MULTIPLIER
    )


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage
    cached: bool
    latency_ms: float
    # What this call actually cost (0.0 on a cache hit).
    cost_usd: float
    # What it would have cost at list price, cache hit or not.
    list_cost_usd: float
    stop_reason: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["usage"] = asdict(self.usage)
        return d


@dataclass
class CostTracker:
    """Running totals for a request, an eval run, or a whole process."""

    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    list_cost_usd: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)

    def record(self, response: LLMResponse) -> None:
        self.calls += 1
        if response.cached:
            self.cached_calls += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        self.cost_usd += response.cost_usd
        self.list_cost_usd += response.list_cost_usd
        self.by_model[response.model] = self.by_model.get(response.model, 0.0) + response.cost_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cached_calls": self.cached_calls,
            "cache_hit_rate": (self.cached_calls / self.calls) if self.calls else 0.0,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "list_cost_usd": round(self.list_cost_usd, 6),
            "by_model": {k: round(v, 6) for k, v in self.by_model.items()},
        }


class MissingAPIKeyError(RuntimeError):
    """Raised on first real API call when no key is configured.

    Deliberately raised lazily, not at construction: retrieval-only evals build
    the whole pipeline and never call a model, and CI runs them without a key.
    """


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
class LLMCache:
    """Content-addressed JSON cache on disk.

    One file per call, sharded into 256 directories by hash prefix so the
    directory stays usable after a few hundred thousand eval calls.
    """

    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled

    @staticmethod
    def make_key(payload: dict[str, Any]) -> str:
        blob = json.dumps(
            {"v": CACHE_VERSION, **payload}, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A truncated cache file is a miss, never a crash.
            log.warning("llm.cache_unreadable", path=str(path))
            return None
        return loaded

    def set(self, key: str, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-write cannot leave a partial entry.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def stats(self) -> dict[str, int]:
        if not self.root.exists():
            return {"entries": 0}
        return {"entries": sum(1 for _ in self.root.rglob("*.json"))}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderResult:
    """What a provider returns, normalized across backends."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str | None = None
    request_id: str | None = None


class AnthropicProvider:
    """The Anthropic Messages API. The default, per PROJECT_SPEC.md §4."""

    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        import anthropic  # imported lazily so retrieval-only paths stay light

        self._client = anthropic.Anthropic(api_key=api_key)

    def invoke(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float | None,
        stop_sequences: list[str] | None,
    ) -> ProviderResult:
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            request["system"] = system
        if stop_sequences:
            request["stop_sequences"] = stop_sequences
        # Claude 4.6+ rejects sampling parameters with a 400. Dropping one
        # beats failing an eval halfway through.
        if temperature is not None and _supports_temperature(model):
            request["temperature"] = temperature

        raw = self._client.messages.create(**request)
        usage = raw.usage
        return ProviderResult(
            text="".join(b.text for b in raw.content if b.type == "text"),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            stop_reason=getattr(raw, "stop_reason", None),
            request_id=getattr(raw, "_request_id", None),
        )


class OpenAICompatibleProvider:
    """Any endpoint that speaks the OpenAI chat-completions API.

    Covers NVIDIA NIM, Groq, OpenRouter, Together, and a local Ollama. The
    point is to make the evaluation harness runnable on a free tier: nothing in
    this project needs Claude specifically, it needs *a* model, and which one
    was used is recorded on every experiment so results stay attributable.

    Two differences from Anthropic worth knowing:

    * The system prompt is a message with `role: "system"`, not a top-level
      field.
    * Reasoning models return their chain of thought in a separate
      `reasoning_content` field. We read `content` only -- the reasoning is
      not the answer, and concatenating them would corrupt every strict-JSON
      response the judge and verifier depend on. See `MIN_MAX_TOKENS` for the
      budget consequence of that split.
    """

    name = "openai"

    # Reasoning models spend output tokens on a chain of thought the caller
    # never sees, then emit the answer. `max_tokens` caps the *total*, so a
    # budget sized for the visible answer (128 for a JSON verdict) can be
    # consumed entirely by reasoning, returning a truncated fragment of the
    # thinking instead. Raising the ceiling costs nothing -- billing is on
    # tokens produced, not on the cap -- so the floor is generous.
    MIN_MAX_TOKENS = 2048

    def __init__(self, api_key: str, base_url: str | None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError(
                "The `openai` package is required for GT_LLM_PROVIDER=openai. "
                "Run: cd backend && uv sync --all-extras"
            ) from exc

        # Generous timeout: hosted reasoning models can take a while, and a
        # timeout mid-eval costs more than waiting.
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0, max_retries=0)

    def invoke(
        self,
        *,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float | None,
        stop_sequences: list[str] | None,
    ) -> ProviderResult:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max(max_tokens, self.MIN_MAX_TOKENS),
        }
        if temperature is not None:
            request["temperature"] = temperature
        if stop_sequences:
            request["stop"] = stop_sequences

        raw = self._client.chat.completions.create(**request)
        choice = raw.choices[0] if raw.choices else None
        text = (getattr(choice.message, "content", None) or "") if choice else ""
        usage = getattr(raw, "usage", None)

        return ProviderResult(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            stop_reason=getattr(choice, "finish_reason", None) if choice else None,
            request_id=getattr(raw, "id", None),
        )


# 429 is a rate limit; 5xx and connection errors are the free tier being
# temporarily overloaded, which it does regularly. All are worth retrying.
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_RETRYABLE_TEXT = ("rate limit", "overloaded", "timeout", "connection error", "temporarily")


def _is_retryable(exc: Exception) -> bool:
    """Detect a transient failure without importing every SDK's exception type."""
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status in _RETRYABLE_STATUS:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _RETRYABLE_TEXT)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class LLMClient:
    """Cached, cost-aware wrapper over whichever provider is configured."""

    def __init__(
        self,
        settings: Settings | None = None,
        cache: LLMCache | None = None,
        tracker: CostTracker | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cache = cache or LLMCache(
            self.settings.llm_cache_path, enabled=self.settings.gt_llm_cache_enabled
        )
        self.tracker = tracker or CostTracker()
        self._client: Any | None = None

    @property
    def default_model(self) -> str:
        return self.settings.gt_generation_model

    @property
    def cheap_model(self) -> str:
        return self.settings.gt_cheap_model

    @property
    def provider_name(self) -> str:
        return self.settings.gt_llm_provider

    def _ensure_client(self) -> Any:
        if self._client is None:
            if not self.settings.has_llm_key:
                expected = (
                    "ANTHROPIC_API_KEY"
                    if self.settings.gt_llm_provider == "anthropic"
                    else "GT_LLM_API_KEY"
                )
                raise MissingAPIKeyError(
                    f"{expected} is not set for provider "
                    f"'{self.settings.gt_llm_provider}'. Generation and judging need it; "
                    "retrieval-only evals (`make eval MODE=retrieval`) do not."
                )

            key = self.settings.llm_api_key
            assert key is not None  # guarded by has_llm_key above
            if self.settings.gt_llm_provider == "anthropic":
                self._client = AnthropicProvider(key.get_secret_value())
            else:
                self._client = OpenAICompatibleProvider(
                    key.get_secret_value(), self.settings.gt_llm_base_url
                )
        return self._client

    def _cost(self, model: str, result: ProviderResult) -> float:
        """Dollar cost of one call, per the active provider."""
        if self.settings.gt_llm_provider != "anthropic":
            # Zero unless the operator supplied rates. Free tiers really are
            # $0; a paid endpoint with unset rates would under-report, which is
            # why `make llm-check` prints the configured rates.
            per_in = self.settings.gt_llm_cost_per_mtok_in / 1_000_000
            per_out = self.settings.gt_llm_cost_per_mtok_out / 1_000_000
            return result.input_tokens * per_in + result.output_tokens * per_out

        return estimate_cost_usd(
            model,
            result.input_tokens,
            result.output_tokens,
            result.cache_read_tokens,
            result.cache_write_tokens,
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float | None = None,
        stop_sequences: list[str] | None = None,
        use_cache: bool = True,
    ) -> LLMResponse:
        """Single-turn completion. Returns text plus exact usage and cost."""
        model = model or self.default_model

        # The cache key includes the provider and base URL: the same prompt to
        # the same model name on a different backend is a different call, and
        # serving one for the other would silently mix results from two systems
        # into one experiment.
        cache_key = LLMCache.make_key(
            {
                "provider": self.settings.gt_llm_provider,
                "base_url": self.settings.gt_llm_base_url or "",
                "model": model,
                "prompt": prompt,
                "system": system or "",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stop_sequences": stop_sequences or [],
            }
        )
        if temperature is not None and not _supports_temperature(model):
            log.debug("llm.temperature_unsupported", model=model, temperature=temperature)
        if use_cache:
            hit = self.cache.get(cache_key)
            if hit is not None:
                response = self._from_cache(hit, model)
                self.tracker.record(response)
                log.debug("llm.cache_hit", model=model, key=cache_key[:12])
                return response

        started = time.perf_counter()
        provider = self._ensure_client()
        result = self._invoke_with_retry(
            provider,
            model=model,
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            stop_sequences=stop_sequences,
        )
        latency_ms = (time.perf_counter() - started) * 1000

        usage = LLMUsage(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_input_tokens=result.cache_read_tokens,
            cache_creation_input_tokens=result.cache_write_tokens,
        )
        cost = self._cost(model, result)
        response = LLMResponse(
            text=result.text,
            model=model,
            usage=usage,
            cached=False,
            latency_ms=latency_ms,
            cost_usd=cost,
            list_cost_usd=cost,
            stop_reason=result.stop_reason,
            request_id=result.request_id,
        )

        if use_cache:
            self.cache.set(cache_key, self._to_cache(response))

        self.tracker.record(response)
        log.info(
            "llm.call",
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=round(cost, 6),
            latency_ms=round(latency_ms, 1),
        )
        return response

    def _invoke_with_retry(
        self,
        provider: Any,
        *,
        model: str,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float | None,
        stop_sequences: list[str] | None,
        attempts: int = 5,
    ) -> ProviderResult:
        """Call the provider, backing off on rate limits.

        Free tiers are rate limited (NVIDIA NIM allows 40 requests/minute) and
        regularly return 503 when busy. An eval makes hundreds of sequential
        calls, so without a backoff a long run dies partway through and the
        partial result is worthless.
        """
        delay = 2.0
        for attempt in range(1, attempts + 1):
            try:
                result: ProviderResult = provider.invoke(
                    model=model,
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop_sequences=stop_sequences,
                )
                return result
            except Exception as exc:
                if attempt == attempts or not _is_retryable(exc):
                    raise
                log.warning("llm.rate_limited", attempt=attempt, sleeping=delay, model=model)
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise RuntimeError("unreachable: retry loop exhausted without raising")

    # --- cache (de)serialization ----------------------------------------
    @staticmethod
    def _to_cache(response: LLMResponse) -> dict[str, Any]:
        return {
            "text": response.text,
            "model": response.model,
            "usage": asdict(response.usage),
            "stop_reason": response.stop_reason,
            "list_cost_usd": response.list_cost_usd,
        }

    @staticmethod
    def _from_cache(entry: dict[str, Any], model: str) -> LLMResponse:
        usage = LLMUsage(**entry.get("usage", {}))
        return LLMResponse(
            text=entry["text"],
            model=entry.get("model", model),
            usage=usage,
            cached=True,
            latency_ms=0.0,
            cost_usd=0.0,  # a cache hit costs nothing, and is reported as such
            list_cost_usd=entry.get("list_cost_usd", 0.0),
            stop_reason=entry.get("stop_reason"),
        )


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Process-wide client, so the cost tracker and cache are shared."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
