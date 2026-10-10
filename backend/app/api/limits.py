"""Admission control for `/query`: a rate limit, then a concurrency cap.

A query is the expensive thing this API does: a worker thread, CPU for the
encoder, and five to ten model calls against a provider quota every client
shares (NVIDIA's free tier allows 40 calls a minute). Without a gate, a
public deployment's quota belongs to whoever sends the most requests, and a
burst queues dozens of CPU-bound queries on one small machine.

Two checks, cheapest first:

* **Rate** -- queries accepted per minute, across all clients. Over it, a 429
  with `Retry-After`. Global on purpose: behind the frontend's proxy every
  request arrives from the same address, and per-client limits keyed on a
  forwarded header are only as honest as whoever sets it.
* **Concurrency** -- queries running at once. Past the cap a request waits
  briefly for a slot, then gets a 503. Waiting is what makes a burst of three
  on a cap of two succeed instead of failing one at random.

A rejected request never ran, so it leaves a log line, not a trace.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import anyio
from fastapi import HTTPException

from app.core.logging import get_logger
from app.core.settings import Settings

log = get_logger(__name__)

WINDOW_SECONDS = 60.0


class RateLimiter:
    """At most `per_minute` acquisitions in any trailing 60-second window."""

    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._accepted: deque[float] = deque()

    def try_acquire(self) -> float | None:
        """None if admitted; otherwise seconds until a slot frees up."""
        if self.per_minute <= 0:
            return None
        now = self._clock()
        while self._accepted and self._accepted[0] <= now - WINDOW_SECONDS:
            self._accepted.popleft()
        if len(self._accepted) >= self.per_minute:
            return max(0.0, self._accepted[0] + WINDOW_SECONDS - now)
        self._accepted.append(now)
        return None


class QueryGate:
    """The two checks, as one object kept on `app.state`."""

    def __init__(
        self,
        *,
        max_concurrent: int,
        queue_seconds: float,
        rate_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = RateLimiter(rate_per_minute, clock=clock)
        self.max_concurrent = max_concurrent
        self.queue_seconds = queue_seconds
        self._slots = anyio.Semaphore(max_concurrent)

    @classmethod
    def from_settings(cls, settings: Settings) -> QueryGate:
        return cls(
            max_concurrent=settings.gt_max_concurrent_queries,
            queue_seconds=settings.gt_query_queue_seconds,
            rate_per_minute=settings.gt_query_rate_per_minute,
        )

    def check_rate(self) -> None:
        retry_after = self.rate.try_acquire()
        if retry_after is not None:
            log.warning("query.rate_limited", per_minute=self.rate.per_minute)
            raise HTTPException(
                429,
                f"Too many questions: this server answers {self.rate.per_minute} a minute. "
                "Try again shortly.",
                headers={"Retry-After": str(math.ceil(retry_after))},
            )

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one of the concurrent-query slots for the duration of the block."""
        busy = HTTPException(
            503,
            f"The server is answering {self.max_concurrent} questions already. "
            "Try again in a minute.",
            headers={"Retry-After": "30"},
        )
        try:
            self._slots.acquire_nowait()
        except anyio.WouldBlock:
            if not self.queue_seconds:
                log.warning("query.busy", max_concurrent=self.max_concurrent)
                raise busy from None
            try:
                with anyio.fail_after(self.queue_seconds):
                    await self._slots.acquire()
            except TimeoutError:
                log.warning("query.busy", max_concurrent=self.max_concurrent)
                raise busy from None
        try:
            yield
        finally:
            self._slots.release()
