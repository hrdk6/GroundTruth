"""Trace endpoints: the list, and one full span tree."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.core.db import session_scope
from app.models import Feedback, Span, Trace

router = APIRouter(tags=["traces"], prefix="/traces")


class TraceSummary(BaseModel):
    trace_id: str
    question: str
    config_name: str
    version_used: str | None
    abstained: bool
    status: str
    latency_ms: float
    cost_usd: float
    started_at: datetime
    span_count: int
    feedback: bool | None = None


class SpanModel(BaseModel):
    span_id: str
    parent_span_id: str | None
    name: str
    sequence: int
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float
    status: str
    input: dict[str, Any]
    output: dict[str, Any]
    attributes: dict[str, Any]


class TraceDetail(TraceSummary):
    answer: str
    spans: list[SpanModel]


@router.get("", response_model=list[TraceSummary], summary="Recent traces")
async def list_traces(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[TraceSummary]:
    def run() -> list[TraceSummary]:
        with session_scope() as session:
            rows = session.execute(
                select(Trace).order_by(desc(Trace.started_at)).limit(limit).offset(offset)
            ).scalars()
            summaries = []
            for trace in rows:
                span_count = len(trace.spans)
                vote = session.execute(
                    select(Feedback.helpful)
                    .where(Feedback.trace_id == trace.id)
                    .order_by(desc(Feedback.created_at))
                    .limit(1)
                ).scalar_one_or_none()
                summaries.append(
                    TraceSummary(
                        trace_id=trace.id,
                        question=trace.question,
                        config_name=trace.config_name,
                        version_used=trace.version_used,
                        abstained=trace.abstained,
                        status=trace.status,
                        latency_ms=trace.latency_ms,
                        cost_usd=trace.cost_usd,
                        started_at=trace.started_at,
                        span_count=span_count,
                        feedback=vote,
                    )
                )
            return summaries

    return await anyio.to_thread.run_sync(run)


@router.get("/{trace_id}", response_model=TraceDetail, summary="Full span tree for one query")
async def get_trace(trace_id: str) -> TraceDetail:
    def run() -> TraceDetail | None:
        with session_scope() as session:
            trace = session.get(Trace, trace_id)
            if trace is None:
                return None
            spans = session.execute(
                select(Span).where(Span.trace_id == trace_id).order_by(Span.sequence)
            ).scalars()
            vote = session.execute(
                select(Feedback.helpful)
                .where(Feedback.trace_id == trace_id)
                .order_by(desc(Feedback.created_at))
                .limit(1)
            ).scalar_one_or_none()

            span_models = [
                SpanModel(
                    span_id=s.id,
                    parent_span_id=s.parent_span_id,
                    name=s.name,
                    sequence=s.sequence,
                    started_at=s.started_at,
                    ended_at=s.ended_at,
                    duration_ms=s.duration_ms,
                    status=s.status,
                    input=s.input,
                    output=s.output,
                    attributes=s.attributes,
                )
                for s in spans
            ]
            return TraceDetail(
                trace_id=trace.id,
                question=trace.question,
                config_name=trace.config_name,
                version_used=trace.version_used,
                abstained=trace.abstained,
                status=trace.status,
                latency_ms=trace.latency_ms,
                cost_usd=trace.cost_usd,
                started_at=trace.started_at,
                span_count=len(span_models),
                feedback=vote,
                answer=trace.answer,
                spans=span_models,
            )

    detail = await anyio.to_thread.run_sync(run)
    if detail is None:
        raise HTTPException(404, f"No trace {trace_id!r}")
    return detail
