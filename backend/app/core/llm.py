"""The single door to the Anthropic API.

Everything that talks to a model goes through `LLMClient` - generation, the
judge, query rewriting, claim verification. That is a deliberate constraint from
PROJECT_SPEC.md S3.4, and it buys three things:

1. **A persistent cache** keyed on `(model, prompt, params)`. Re-running an eval
   over 300 items costs nothing the second time, which is what makes the
   "measure everything" workflow affordable.
2. **Honest cost accounting.** Every call records tokens and dollars, so a
   per-query cost lands in the trace and a per-run total lands in the
   experiment file. Nothing is estimated after the fact.
3. **One place for model quirks.** Current models reject parameters that older
   ones required (see `_supports_temperature`), and getting that wrong is a
   400 at request time rather than a type error.

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
# Client
# ---------------------------------------------------------------------------
class LLMClient:
    """Cached, cost-aware wrapper over the Anthropic Messages API."""

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

    def _ensure_client(self) -> Any:
        if self._client is None:
            if not self.settings.has_anthropic_key:
                raise MissingAPIKeyError(
                    "ANTHROPIC_API_KEY is not set. Generation and judging need it; "
                    "retrieval-only evals (`make eval MODE=retrieval`) do not."
                )
            import anthropic  # imported lazily so retrieval-only paths stay light

            key = self.settings.anthropic_api_key
            assert key is not None  # guarded by has_anthropic_key above
            self._client = anthropic.Anthropic(api_key=key.get_secret_value())
        return self._client

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
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            request["system"] = system
        if stop_sequences:
            request["stop_sequences"] = stop_sequences
        # Silently dropping an unsupported temperature beats a 400 mid-eval; we
        # log it so a config asking for something it cannot get stays visible.
        if temperature is not None:
            if _supports_temperature(model):
                request["temperature"] = temperature
            else:
                log.debug("llm.temperature_unsupported", model=model, temperature=temperature)

        cache_key = LLMCache.make_key(request)
        if use_cache:
            hit = self.cache.get(cache_key)
            if hit is not None:
                response = self._from_cache(hit, model)
                self.tracker.record(response)
                log.debug("llm.cache_hit", model=model, key=cache_key[:12])
                return response

        started = time.perf_counter()
        client = self._ensure_client()
        raw = client.messages.create(**request)
        latency_ms = (time.perf_counter() - started) * 1000

        text = "".join(block.text for block in raw.content if block.type == "text")
        usage = LLMUsage(
            input_tokens=getattr(raw.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw.usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(raw.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(raw.usage, "cache_creation_input_tokens", 0) or 0,
        )
        cost = estimate_cost_usd(
            model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens,
            usage.cache_creation_input_tokens,
        )
        response = LLMResponse(
            text=text,
            model=model,
            usage=usage,
            cached=False,
            latency_ms=latency_ms,
            cost_usd=cost,
            list_cost_usd=cost,
            stop_reason=getattr(raw, "stop_reason", None),
            request_id=getattr(raw, "_request_id", None),
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
