"""Span recording.

A `Tracer` collects spans in memory during a query and flushes once at the end.
Writing each span as it closes would put a database round-trip inside every
retrieval stage and make the latency numbers the tracer reports partly its own.

Tracing must never break a query: `flush` swallows its own failures and logs
them. An observability layer that can take down the thing it observes is worse
than no observability layer.

Two kinds of span:

* **Stages**, opened with `span()` by the thread running the query. They nest
  through a stack, so they must only be opened from that one thread.
* **Model calls**, recorded after the fact by `record_llm_call`, which the
  `LLMClient` invokes for every call -- cache hits and failures included. These
  can arrive from worker threads (claim verification fans out), so they take
  their parent from the stack instead of pushing onto it, under a lock. A
  stage's span rolls up its calls' tokens, cost and errors when it closes, so
  "which stage spent the money" is answerable without summing children.

A stage that fell back -- a rewrite whose call failed, a verifier that could
not be parsed -- is marked `degraded`, not `ok`. A fallback is designed to keep
the query alive, which is exactly why it is invisible unless recorded.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Span, Trace

if TYPE_CHECKING:
    from app.core.llm import LLMCallRecord

log = get_logger(__name__)

# Long strings are truncated before storage: a trace is a diagnostic record,
# not a second copy of the corpus.
MAX_FIELD_CHARS = 4000

# The span name for one model call. A fixed name, so the stats endpoint can
# group every call together; the parent span says which stage made it.
LLM_SPAN = "llm"

# Span statuses, worst last. `degraded` means the stage returned a fallback.
STATUSES = ("ok", "degraded", "error")


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"... [{len(value) - MAX_FIELD_CHARS} more chars]"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value[:50]]
    return value


@dataclass
class SpanRecord:
    id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    sequence: int
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float = 0.0
    status: str = "ok"
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "sequence": self.sequence,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "attributes": self.attributes,
        }


class Tracer:
    """Collects spans for one query."""

    def __init__(self, question: str, *, config_name: str = "", config_hash: str = "") -> None:
        self.trace_id = str(uuid.uuid4())
        self.question = question
        self.config_name = config_name
        self.config_hash = config_hash
        self.spans: list[SpanRecord] = []
        self.started_at = datetime.now(UTC)
        self._stack: list[str] = []
        self._sequence = 0
        # Guards `spans` and `_sequence` against model calls recorded from
        # worker threads while the query thread opens its next stage.
        self._lock = threading.Lock()

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    @contextmanager
    def span(self, name: str, **input_fields: Any) -> Iterator[SpanRecord]:
        """Open a span; it closes when the block exits, error or not.

        Only the thread running the query may open spans: they nest through a
        stack that has no notion of threads.
        """
        record = SpanRecord(
            id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            parent_span_id=self._stack[-1] if self._stack else None,
            name=name,
            sequence=self._next_sequence(),
            started_at=datetime.now(UTC),
            input=_truncate(input_fields),
        )
        with self._lock:
            self.spans.append(record)
        self._stack.append(record.id)
        started = time.perf_counter()
        try:
            yield record
        except Exception as exc:
            record.status = "error"
            record.output = {"error": type(exc).__name__, "message": str(exc)[:500]}
            raise
        finally:
            record.duration_ms = (time.perf_counter() - started) * 1000
            record.ended_at = datetime.now(UTC)
            self._roll_up_llm_calls(record)
            record.output = _truncate(record.output)
            record.attributes = _truncate(record.attributes)
            self._stack.pop()

    def record_llm_call(self, call: LLMCallRecord) -> None:
        """Record one model call as a child of the stage that made it.

        The `LLMClient` observer hook: safe from any thread, and never raises.
        """
        try:
            parent = self._stack[-1] if self._stack else None
            record = SpanRecord(
                id=str(uuid.uuid4()),
                trace_id=self.trace_id,
                parent_span_id=parent,
                name=LLM_SPAN,
                sequence=self._next_sequence(),
                started_at=call.started_at,
                ended_at=call.started_at + timedelta(milliseconds=call.latency_ms),
                duration_ms=call.latency_ms,
                status="error" if call.error else "ok",
                input={
                    "model": call.model,
                    "provider": call.provider,
                    "prompt_chars": call.prompt_chars,
                    "max_tokens": call.max_tokens,
                },
                output=_truncate(
                    {"error": call.error} if call.error else {"text": call.text_preview}
                ),
                attributes={
                    "cached": call.cached,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cost_usd": round(call.cost_usd, 6),
                    "stop_reason": call.stop_reason,
                    "attempts": call.attempts,
                    # A reasoning model that spent the whole budget thinking
                    # returns a fragment that looks like a model ignoring its
                    # instructions. These two make that visible per call.
                    "truncated": call.truncated,
                    "empty": not call.error and not call.text_preview.strip(),
                },
            )
            with self._lock:
                self.spans.append(record)
        except Exception as exc:  # noqa: BLE001 - observability must not break a query
            log.warning("tracing.llm_call_not_recorded", error=str(exc))

    def _roll_up_llm_calls(self, record: SpanRecord) -> None:
        """Total the model calls made directly inside `record` onto it."""
        with self._lock:
            calls = [s for s in self.spans if s.parent_span_id == record.id and s.name == LLM_SPAN]
        if not calls:
            return
        record.attributes.update(
            {
                "llm_calls": len(calls),
                "llm_cached": sum(1 for c in calls if c.attributes.get("cached")),
                "llm_errors": sum(1 for c in calls if c.status == "error"),
                "llm_retries": sum(max(0, int(c.attributes.get("attempts", 1)) - 1) for c in calls),
                "input_tokens": sum(int(c.attributes.get("input_tokens", 0)) for c in calls),
                "output_tokens": sum(int(c.attributes.get("output_tokens", 0)) for c in calls),
                "cost_usd": round(sum(float(c.attributes.get("cost_usd", 0.0)) for c in calls), 6),
            }
        )

    def degraded_stages(self) -> list[str]:
        """Names of stages that returned a fallback, in the order they ran."""
        return list(dict.fromkeys(s.name for s in self.spans if s.status == "degraded"))

    def stage_breakdown(self) -> dict[str, dict[str, Any]]:
        """Per stage: total time, model calls, tokens, cost, and worst status.

        Stages that run more than once (a rewrite per sub-query, a second
        verification after a retry) are summed under one name. This is what an
        eval record keeps per item, where a full span tree would be noise.
        """
        breakdown: dict[str, dict[str, Any]] = {}
        for span in self.spans:
            if span.parent_span_id is not None:
                continue
            entry = breakdown.setdefault(
                span.name,
                {
                    "ms": 0.0,
                    "runs": 0,
                    "llm_calls": 0,
                    "llm_cached": 0,
                    "llm_errors": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "status": "ok",
                },
            )
            entry["ms"] = round(entry["ms"] + span.duration_ms, 2)
            entry["runs"] += 1
            for key in ("llm_calls", "llm_cached", "llm_errors", "input_tokens", "output_tokens"):
                entry[key] += int(span.attributes.get(key, 0))
            entry["cost_usd"] = round(
                entry["cost_usd"] + float(span.attributes.get("cost_usd", 0)), 6
            )
            if STATUSES.index(span.status) > STATUSES.index(entry["status"]):
                entry["status"] = span.status
        return breakdown

    def flush(
        self,
        session: Session,
        *,
        answer: str = "",
        abstained: bool = False,
        version_used: str | None = None,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        total_tokens: int = 0,
        status: str = "ok",
        meta: dict[str, Any] | None = None,
    ) -> str | None:
        """Persist the trace and its spans. Never raises."""
        meta = {**(meta or {}), "degraded_stages": self.degraded_stages()}
        try:
            session.add(
                Trace(
                    id=self.trace_id,
                    question=self.question[:MAX_FIELD_CHARS],
                    config_name=self.config_name,
                    config_hash=self.config_hash,
                    version_used=version_used,
                    answer=answer[:MAX_FIELD_CHARS],
                    abstained=abstained,
                    status=status,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    total_tokens=total_tokens,
                    started_at=self.started_at,
                    meta=meta,
                )
            )
            with self._lock:
                spans = list(self.spans)
            for record in spans:
                session.add(
                    Span(
                        id=record.id,
                        trace_id=record.trace_id,
                        parent_span_id=record.parent_span_id,
                        name=record.name,
                        sequence=record.sequence,
                        started_at=record.started_at,
                        ended_at=record.ended_at,
                        duration_ms=record.duration_ms,
                        status=record.status,
                        input=record.input,
                        output=record.output,
                        attributes=record.attributes,
                    )
                )
            session.flush()
            return self.trace_id
        except Exception as exc:  # noqa: BLE001 - tracing must not break a query
            log.warning("tracing.flush_failed", error=str(exc), trace_id=self.trace_id)
            session.rollback()
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "question": self.question,
            "config_name": self.config_name,
            "started_at": self.started_at.isoformat(),
            "spans": [s.to_dict() for s in self.spans],
        }


class NullTracer(Tracer):
    """A tracer that never persists, for evals.

    It still collects spans in memory, which is how an eval item gets its
    per-stage breakdown (`stage_breakdown`) into the experiment record. Writing
    them would put tens of thousands of spans that nothing reads into the
    database the API serves from.
    """

    def flush(self, session: Session, **kwargs: Any) -> str | None:
        return None
