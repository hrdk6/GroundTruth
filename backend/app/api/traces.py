"""Trace endpoints: the list, one full span tree, and per-stage statistics.

`/traces/stats` is the view across queries that a single trace cannot give:
for each stage, how often it ran, how long it takes at the median and the
tail, how often it errored or fell back, and what its model calls cost. A
stage whose p95 doubled, or whose fallback rate is creeping up, is invisible
one trace at a time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Float, Integer, and_, case, desc, func, select

from app.core.db import session_scope
from app.models import Feedback, Span, Trace
from app.tracing.tracer import LLM_SPAN

router = APIRouter(tags=["traces"], prefix="/traces")

# The order a query passes through the stages (`retriever.py`, `answer.py`),
# which is the order the stats table reads in. Model calls come last.
STAGE_ORDER = [
    "version_detection",
    "decomposition",
    "query_rewrite",
    "dense",
    "lexical",
    "fusion",
    "rerank",
    "generation",
    "verification",
    "regeneration",
    "conflict_detection",
    LLM_SPAN,
]


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
    abstain_reason: str | None = None
    # Stages that returned a fallback: the query succeeded, but not as designed.
    degraded_stages: list[str] = []


def _meta_fields(meta: dict[str, Any] | None) -> dict[str, Any]:
    meta = meta or {}
    return {
        "abstain_reason": meta.get("abstain_reason"),
        "degraded_stages": list(meta.get("degraded_stages") or []),
    }


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
    # Citation/conflict counts on success; the error type and message when
    # the query failed, which is when a trace is most worth reading.
    meta: dict[str, Any] = {}
    spans: list[SpanModel]


@router.get("", response_model=list[TraceSummary], summary="Recent traces")
async def list_traces(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[TraceSummary]:
    def run() -> list[TraceSummary]:
        # One round trip. Counting spans through the relationship loaded every
        # span of every listed trace -- JSONB payloads included -- only to
        # take len() of it, and fetched each trace's feedback separately.
        span_counts = (
            select(Span.trace_id, func.count().label("span_count"))
            .group_by(Span.trace_id)
            .subquery()
        )
        latest_vote = (
            select(Feedback.helpful)
            .where(Feedback.trace_id == Trace.id)
            .order_by(desc(Feedback.created_at))
            .limit(1)
            .correlate(Trace)
            .scalar_subquery()
        )
        statement = (
            select(
                Trace,
                func.coalesce(span_counts.c.span_count, 0).label("span_count"),
                latest_vote.label("vote"),
            )
            .outerjoin(span_counts, span_counts.c.trace_id == Trace.id)
            .order_by(desc(Trace.started_at))
            .limit(limit)
            .offset(offset)
        )
        with session_scope() as session:
            return [
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
                    **_meta_fields(trace.meta),
                )
                for trace, span_count, vote in session.execute(statement).all()
            ]

    return await anyio.to_thread.run_sync(run)


class StageStats(BaseModel):
    name: str
    # Spans, and the traces they belong to: a rewrite runs once per sub-query.
    runs: int
    traces: int
    errors: int
    degraded: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    llm_calls: int
    llm_cached: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class TraceTotals(BaseModel):
    traces: int
    errors: int
    abstained: int
    abstain_reasons: dict[str, int]
    degraded: int
    p50_ms: float
    p95_ms: float
    cost_usd: float
    helpful: int
    unhelpful: int


class TraceStats(BaseModel):
    since: datetime
    config_name: str | None
    totals: TraceTotals
    stages: list[StageStats]


def _int_attr(key: str) -> Any:
    return func.coalesce(func.sum(Span.attributes[key].astext.cast(Integer)), 0)


@router.get("/stats", response_model=TraceStats, summary="Per-stage latency, failures and cost")
async def trace_stats(
    hours: int = Query(default=168, ge=1, le=24 * 90, description="Look-back window"),
    config_name: str | None = Query(default=None, description="Only traces of this pipeline"),
) -> TraceStats:
    since = datetime.now(UTC) - timedelta(hours=hours)

    def run() -> TraceStats:
        in_window = [Trace.started_at >= since]
        if config_name:
            in_window.append(Trace.config_name == config_name)
        window = and_(*in_window)

        # Model calls carry their own `cached` flag; a stage carries the
        # rolled-up `llm_cached` count. Summing both per name keeps one query.
        cached = func.coalesce(
            func.sum(
                case(
                    (
                        Span.name == LLM_SPAN,
                        case((Span.attributes["cached"].astext == "true", 1), else_=0),
                    ),
                    else_=func.coalesce(Span.attributes["llm_cached"].astext.cast(Integer), 0),
                )
            ),
            0,
        )
        calls = func.coalesce(
            func.sum(
                case(
                    (Span.name == LLM_SPAN, 1),
                    else_=func.coalesce(Span.attributes["llm_calls"].astext.cast(Integer), 0),
                )
            ),
            0,
        )
        stage_rows = (
            select(
                Span.name,
                func.count().label("runs"),
                func.count(func.distinct(Span.trace_id)).label("traces"),
                func.count().filter(Span.status == "error").label("errors"),
                func.count().filter(Span.status == "degraded").label("degraded"),
                func.percentile_cont(0.5).within_group(Span.duration_ms).label("p50"),
                func.percentile_cont(0.95).within_group(Span.duration_ms).label("p95"),
                func.max(Span.duration_ms).label("max"),
                calls.label("llm_calls"),
                cached.label("llm_cached"),
                _int_attr("input_tokens").label("input_tokens"),
                _int_attr("output_tokens").label("output_tokens"),
                func.coalesce(func.sum(Span.attributes["cost_usd"].astext.cast(Float)), 0.0).label(
                    "cost_usd"
                ),
            )
            .join(Trace, Trace.id == Span.trace_id)
            .where(window)
            .group_by(Span.name)
        )

        totals_row = select(
            func.count().label("traces"),
            func.count().filter(Trace.status == "error").label("errors"),
            func.count().filter(Trace.abstained.is_(True)).label("abstained"),
            # NULL for traces written before the key existed, so not counted.
            func.count()
            .filter(func.jsonb_array_length(Trace.meta["degraded_stages"]) > 0)
            .label("degraded"),
            func.percentile_cont(0.5).within_group(Trace.latency_ms).label("p50"),
            func.percentile_cont(0.95).within_group(Trace.latency_ms).label("p95"),
            func.coalesce(func.sum(Trace.cost_usd), 0.0).label("cost_usd"),
        ).where(window)

        reason = Trace.meta["abstain_reason"].astext
        reason_rows = (
            select(reason.label("reason"), func.count().label("n"))
            .where(window, Trace.abstained.is_(True))
            .group_by(reason)
        )

        # The latest vote per trace, as the list shows it.
        latest = (
            select(
                Feedback.trace_id,
                Feedback.helpful,
                func.row_number()
                .over(partition_by=Feedback.trace_id, order_by=desc(Feedback.created_at))
                .label("position"),
            )
            .join(Trace, Trace.id == Feedback.trace_id)
            .where(window)
            .subquery()
        )
        vote_rows = (
            select(latest.c.helpful, func.count().label("n"))
            .where(latest.c.position == 1)
            .group_by(latest.c.helpful)
        )

        with session_scope() as session:
            totals = session.execute(totals_row).one()
            reasons = {(row.reason or "unrecorded"): row.n for row in session.execute(reason_rows)}
            votes = {row.helpful: row.n for row in session.execute(vote_rows)}
            stages = [
                StageStats(
                    name=row.name,
                    runs=row.runs,
                    traces=row.traces,
                    errors=row.errors,
                    degraded=row.degraded,
                    p50_ms=round(float(row.p50 or 0.0), 2),
                    p95_ms=round(float(row.p95 or 0.0), 2),
                    max_ms=round(float(row.max or 0.0), 2),
                    llm_calls=int(row.llm_calls),
                    llm_cached=int(row.llm_cached),
                    input_tokens=int(row.input_tokens),
                    output_tokens=int(row.output_tokens),
                    cost_usd=round(float(row.cost_usd), 6),
                )
                for row in session.execute(stage_rows)
            ]

        order = {name: i for i, name in enumerate(STAGE_ORDER)}
        stages.sort(key=lambda s: (order.get(s.name, len(order)), s.name))

        return TraceStats(
            since=since,
            config_name=config_name,
            totals=TraceTotals(
                traces=totals.traces,
                errors=totals.errors,
                abstained=totals.abstained,
                abstain_reasons=reasons,
                degraded=totals.degraded,
                p50_ms=round(float(totals.p50 or 0.0), 2),
                p95_ms=round(float(totals.p95 or 0.0), 2),
                cost_usd=round(float(totals.cost_usd), 6),
                helpful=votes.get(True, 0),
                unhelpful=votes.get(False, 0),
            ),
            stages=stages,
        )

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
                **_meta_fields(trace.meta),
                answer=trace.answer,
                meta=trace.meta or {},
                spans=span_models,
            )

    detail = await anyio.to_thread.run_sync(run)
    if detail is None:
        raise HTTPException(404, f"No trace {trace_id!r}")
    return detail
