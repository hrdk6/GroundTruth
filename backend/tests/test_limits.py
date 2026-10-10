"""Serving safeguards: admission control, served configs, and one model load.

Each guards a public deployment against something a visitor can do: spend the
shared provider quota, queue dozens of CPU-bound queries, or pick a config
that loads a gigabyte model.
"""

from __future__ import annotations

import threading
import time

import anyio
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.limits import QueryGate, RateLimiter
from app.core.pipeline import EmbeddingConfig
from app.core.settings import Settings
from app.ingestion.embed import Embedder
from app.main import create_app


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------
def test_the_rate_limit_admits_n_a_minute_then_says_when_to_retry() -> None:
    clock = _Clock()
    limiter = RateLimiter(2, clock=clock)

    assert limiter.try_acquire() is None
    clock.now += 10
    assert limiter.try_acquire() is None
    clock.now += 5
    assert limiter.try_acquire() == pytest.approx(45.0), (
        "the first slot frees 60s after it was taken"
    )

    clock.now += 46
    assert limiter.try_acquire() is None, "the window slid past the first request"


def test_a_zero_rate_disables_the_limit() -> None:
    limiter = RateLimiter(0)
    assert all(limiter.try_acquire() is None for _ in range(1000))


def test_the_api_answers_429_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GT_QUERY_RATE_PER_MINUTE", "1")
    client = TestClient(create_app())
    # An unknown config is a cheap request that still counts against the rate.
    first = client.post("/query", json={"question": "q", "config_name": "nope"})
    second = client.post("/query", json={"question": "q", "config_name": "nope"})

    assert first.status_code == 404
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert "a minute" in second.json()["detail"]


# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------
def _gate(max_concurrent: int, queue_seconds: float) -> QueryGate:
    return QueryGate(max_concurrent=max_concurrent, queue_seconds=queue_seconds, rate_per_minute=0)


def test_a_query_past_the_cap_is_turned_away_with_503() -> None:
    async def scenario() -> int:
        gate = _gate(1, queue_seconds=0.05)
        async with gate.slot():
            try:
                async with gate.slot():
                    return 200
            except HTTPException as exc:
                return exc.status_code

    assert anyio.run(scenario) == 503


def test_a_query_waits_for_a_slot_that_frees_in_time() -> None:
    """A burst of two on a cap of one: the second waits rather than failing."""

    async def scenario() -> list[str]:
        gate = _gate(1, queue_seconds=5.0)
        order: list[str] = []

        async def run(name: str, hold: float) -> None:
            async with gate.slot():
                order.append(f"{name} start")
                await anyio.sleep(hold)
                order.append(f"{name} end")

        async with anyio.create_task_group() as group:
            group.start_soon(run, "a", 0.05)
            await anyio.sleep(0.01)
            group.start_soon(run, "b", 0.0)
        return order

    assert anyio.run(scenario) == ["a start", "a end", "b start", "b end"]


def test_a_slot_is_released_when_the_query_fails() -> None:
    async def scenario() -> bool:
        gate = _gate(1, queue_seconds=0)
        with pytest.raises(RuntimeError):
            async with gate.slot():
                raise RuntimeError("pipeline failed")
        async with gate.slot():
            return True

    assert anyio.run(scenario)


# ---------------------------------------------------------------------------
# Served configs
# ---------------------------------------------------------------------------
def test_only_served_configs_are_listed_and_runnable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GT_SERVED_CONFIGS", '["full", "baseline"]')

    monkeypatch.setattr("app.api.query.indexed_versions", lambda session: ["1.30"])
    monkeypatch.setattr("app.api.query.session_scope", _null_session)
    client = TestClient(create_app())

    assert client.get("/versions").json()["configs"] == ["baseline", "full"]
    refused = client.post("/query", json={"question": "q", "config_name": "hybrid_rerank"})
    assert refused.status_code == 404
    assert "hybrid_rerank" not in refused.json()["detail"].split("Available:")[1]


def test_the_default_config_must_be_served() -> None:
    with pytest.raises(ValueError, match="GT_DEFAULT_CONFIG"):
        Settings(_env_file=None, gt_served_configs=["baseline"])  # type: ignore[call-arg]


def test_admin_ingest_is_not_limited_to_served_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building a chunk set to evaluate is not serving it."""
    from app.api.query import resolve_config

    monkeypatch.setenv("GT_SERVED_CONFIGS", '["full"]')
    with pytest.raises(HTTPException):
        resolve_config("hybrid_rerank")
    assert resolve_config("hybrid_rerank", served_only=False).name == "hybrid_rerank"


class _null_session:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


# ---------------------------------------------------------------------------
# One model load, however many first requests
# ---------------------------------------------------------------------------
def test_concurrent_first_queries_load_the_encoder_once(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[int] = []

    def slow_load(self: Embedder) -> object:
        loads.append(1)
        time.sleep(0.05)
        return object()

    monkeypatch.setattr(Embedder, "_load_model", slow_load)
    embedder = Embedder(EmbeddingConfig())
    threads = [threading.Thread(target=embedder.warm) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(loads) == 1
    assert embedder.loaded


def test_health_reports_whether_the_encoder_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check() -> dict[str, object]:
        return {"server_version": "16.4", "pgvector": True}

    monkeypatch.setattr("app.api.health.check_connection", fake_check)
    body = TestClient(create_app()).get("/health").json()
    assert isinstance(body["embedding_model_loaded"], bool)
