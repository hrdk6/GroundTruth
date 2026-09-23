"""Trace, span, and feedback tables.

An OpenTelemetry-shaped schema stored in the same Postgres as everything else:
`trace_id` groups one query, `parent_span_id` gives the tree, and `attributes`
holds whatever the stage wants to record (k, scores, tokens, cost).

Why this exists at all: when an answer is wrong, "was it retrieval or
generation?" must be answerable from data rather than by re-running the query
and guessing. A span per stage, with its inputs, outputs and scores, is what
makes a ranking miss *visible* -- the gold chunk sitting at rank 2 after dense
retrieval and rank 40 after reranking.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Trace(Base):
    """One `/query` call."""

    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    config_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    config_hash: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    version_used: Mapped[str | None] = mapped_column(String(16), default=None)

    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    abstained: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")

    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    spans: Mapped[list[Span]] = relationship(
        back_populates="trace", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_traces_started_at", "started_at"),)


class Span(Base):
    """One stage within a trace."""

    __tablename__ = "spans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("traces.id", ondelete="CASCADE"), nullable=False
    )
    parent_span_id: Mapped[str | None] = mapped_column(String(36), default=None)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Ordering key: timestamps at millisecond resolution tie too often to sort
    # sibling spans reliably.
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    trace: Mapped[Trace] = relationship(back_populates="spans")

    __table_args__ = (
        Index("ix_spans_trace", "trace_id", "sequence"),
        Index("ix_spans_name", "name"),
    )


class Feedback(Base):
    """Thumbs up/down on an answer."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Not a foreign key: feedback must survive trace retention pruning.
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    helpful: Mapped[bool] = mapped_column(nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_feedback_trace", "trace_id"),)
