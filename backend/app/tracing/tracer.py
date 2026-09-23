"""Span recording.

A `Tracer` collects spans in memory during a query and flushes once at the end.
Writing each span as it closes would put a database round-trip inside every
retrieval stage and make the latency numbers the tracer reports partly its own.

Tracing must never break a query: `flush` swallows its own failures and logs
them. An observability layer that can take down the thing it observes is worse
than no observability layer.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Span, Trace

log = get_logger(__name__)

# Long strings are truncated before storage: a trace is a diagnostic record,
# not a second copy of the corpus.
MAX_FIELD_CHARS = 4000


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

    @contextmanager
    def span(self, name: str, **input_fields: Any) -> Iterator[SpanRecord]:
        """Open a span; it closes when the block exits, error or not."""
        self._sequence += 1
        record = SpanRecord(
            id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            parent_span_id=self._stack[-1] if self._stack else None,
            name=name,
            sequence=self._sequence,
            started_at=datetime.now(UTC),
            input=_truncate(input_fields),
        )
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
            record.output = _truncate(record.output)
            record.attributes = _truncate(record.attributes)
            self._stack.pop()

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
                    meta=meta or {},
                )
            )
            for record in self.spans:
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
    """No-op tracer for evals, which record results in experiment files instead.

    Running 300 eval items through full tracing would write tens of thousands of
    spans that nothing reads.
    """

    def flush(self, session: Session, **kwargs: Any) -> str | None:
        return None
