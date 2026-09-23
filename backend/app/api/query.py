"""Query, feedback, and experiment endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import session_scope
from app.core.llm import MissingAPIKeyError
from app.core.logging import get_logger
from app.core.pipeline import PipelineConfig, list_configs, load_config
from app.core.settings import REPO_ROOT, get_settings
from app.generation.answer import AnswerResult, AnswerService
from app.models import Feedback, Trace
from app.retrieval.versioning import indexed_versions
from app.tracing.tracer import Tracer

router = APIRouter(tags=["query"])
log = get_logger(__name__)

EXPERIMENTS_DIR = REPO_ROOT / "experiments"


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    version: str | None = Field(default=None, description="Force a corpus version, e.g. '1.28'")
    config_name: str | None = Field(
        default=None,
        description="Pipeline config name from configs/; defaults to GT_DEFAULT_CONFIG",
    )


class CitationModel(BaseModel):
    marker: int
    chunk_id: int
    source_path: str
    version: str
    heading_path: str
    title: str
    url: str
    text: str
    score: float


class SegmentModel(BaseModel):
    """One sentence of the answer, split on the server with its verdict."""

    text: str
    citations: list[int]
    factual: bool
    verdict: str | None = None
    reason: str = ""


class QueryResponse(BaseModel):
    answer: str
    segments: list[SegmentModel]
    citations: list[CitationModel]
    version_used: str | None
    version_reason: str
    conflicts: list[dict[str, Any]]
    conflict_note: str
    verification: dict[str, Any]
    abstained: bool
    regenerated: bool
    config_name: str
    trace_id: str | None
    latency_ms: float
    cost_usd: float


def resolve_config(name: str | None) -> PipelineConfig:
    """A config *by name*, from `configs/` only.

    `load_config` also accepts filesystem paths, which is right for a CLI and
    wrong for an HTTP endpoint: a client must not be able to make the server
    open arbitrary files. Anything not in the listing is a 404.
    """
    config_name = name or get_settings().gt_default_config
    available = list_configs()
    if config_name not in available:
        raise HTTPException(404, f"No pipeline config {config_name!r}. Available: {available}")
    return load_config(config_name)


@router.post("/query", response_model=QueryResponse, summary="Ask a grounded question")
async def query(request: QueryRequest) -> QueryResponse:
    config = resolve_config(request.config_name)
    service = AnswerService(config)
    tracer = Tracer(request.question, config_name=config.name, config_hash=config.config_hash)

    # The retrieval and generation stack is synchronous (SQLAlchemy sync
    # session, CPU-bound encoders), so it runs in a worker thread rather than
    # blocking the event loop for every other request.
    def run() -> AnswerResult:
        with session_scope() as session:
            return service.answer(session, request.question, version=request.version, tracer=tracer)

    try:
        result = await anyio.to_thread.run_sync(run)
    except Exception as exc:
        # A failed query is exactly the one worth a trace. The request's
        # transaction was rolled back, so the trace is written in a fresh one;
        # the span that raised is already marked `error`.
        trace_id = await anyio.to_thread.run_sync(_flush_failed_trace, tracer, exc)
        suffix = f" (trace {trace_id})" if trace_id else ""
        if isinstance(exc, MissingAPIKeyError):
            raise HTTPException(
                503,
                "Generation needs an LLM API key for the configured provider. "
                f"Retrieval-only evaluation does not.{suffix}",
            ) from exc
        log.exception("query.failed", trace_id=trace_id)
        raise HTTPException(
            502, f"The query failed in the pipeline: {type(exc).__name__}.{suffix}"
        ) from exc

    return QueryResponse(
        answer=result.answer,
        segments=[SegmentModel(**s) for s in result.segments],
        citations=[CitationModel(**c.to_dict()) for c in result.citations],
        version_used=result.version_used,
        version_reason=result.version_reason,
        conflicts=result.conflicts,
        conflict_note=result.conflict_note,
        verification=result.verification,
        abstained=result.abstained,
        regenerated=result.regenerated,
        config_name=config.name,
        trace_id=result.trace_id,
        latency_ms=round(result.latency_ms, 2),
        cost_usd=round(result.cost_usd, 6),
    )


def _flush_failed_trace(tracer: Tracer, exc: Exception) -> str | None:
    try:
        with session_scope() as session:
            return tracer.flush(
                session,
                status="error",
                meta={"error": type(exc).__name__, "message": str(exc)[:500]},
            )
    except Exception:  # noqa: BLE001 - the database may be the thing that failed
        log.warning("query.error_trace_not_written", trace_id=tracer.trace_id)
        return None


class VersionsResponse(BaseModel):
    versions: list[str]
    configs: list[str]
    default_config: str


@router.get("/versions", response_model=VersionsResponse, summary="Indexed versions and configs")
async def versions() -> VersionsResponse:
    def run() -> list[str]:
        with session_scope() as session:
            return indexed_versions(session)

    return VersionsResponse(
        versions=await anyio.to_thread.run_sync(run),
        configs=list_configs(),
        default_config=get_settings().gt_default_config,
    )


class FeedbackRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=36)
    helpful: bool
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/feedback", status_code=201, summary="Record thumbs up/down on an answer")
async def feedback(request: FeedbackRequest) -> dict[str, str]:
    def run() -> bool:
        with session_scope() as session:
            # Feedback is deliberately not a foreign key (it must survive trace
            # pruning), so the existence check has to happen here. Accepting
            # votes for traces that never existed makes the table unjoinable.
            if session.get(Trace, request.trace_id) is None:
                return False
            session.add(
                Feedback(
                    trace_id=request.trace_id,
                    helpful=request.helpful,
                    comment=request.comment,
                )
            )
            return True

    if not await anyio.to_thread.run_sync(run):
        raise HTTPException(404, f"No trace {request.trace_id!r}")
    return {"status": "recorded"}


class ExperimentSummary(BaseModel):
    id: str
    config_name: str
    split: str
    mode: str
    timestamp: str
    git_sha: str | None = None
    git_dirty: bool | None = None
    dataset_version: str | None = None
    dataset_size: int | None = None
    metrics: dict[str, Any] = {}
    confidence: dict[str, Any] = {}
    integrity: dict[str, Any] = {}
    cost_usd: float | None = None


def _load_experiment(path: Path) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("experiments.unreadable", path=path.name)
        return None
    data["id"] = path.stem
    return data


@router.get("/experiments", summary="List recorded experiment runs")
async def experiments(limit: int = Query(default=50, ge=1, le=500)) -> list[ExperimentSummary]:
    if not EXPERIMENTS_DIR.exists():
        return []

    out: list[ExperimentSummary] = []
    for path in sorted(EXPERIMENTS_DIR.glob("*.json"), reverse=True)[:limit]:
        data = _load_experiment(path)
        if data is None:
            continue
        integrity = data.get("integrity") or {}
        out.append(
            ExperimentSummary(
                id=data["id"],
                config_name=data.get("config", {}).get("name", "?"),
                split=data.get("split", "?"),
                mode=data.get("mode", "?"),
                timestamp=data.get("timestamp", ""),
                git_sha=data.get("git_sha"),
                git_dirty=data.get("git_dirty"),
                dataset_version=data.get("dataset_version"),
                dataset_size=data.get("dataset_size"),
                metrics=data.get("metrics", {}),
                confidence=data.get("confidence", {}),
                integrity={k: v for k, v in integrity.items() if k != "issues"},
                cost_usd=data.get("cost", {}).get("cost_usd"),
            )
        )
    return out


def _experiment_path(experiment_id: str) -> Path:
    # Resolve inside the directory and compare: an id like "../../.env" must
    # not escape into the rest of the filesystem.
    path = (EXPERIMENTS_DIR / f"{experiment_id}.json").resolve()
    if not path.is_relative_to(EXPERIMENTS_DIR.resolve()) or not path.exists():
        raise HTTPException(404, f"No experiment {experiment_id!r}")
    return path


def _read_experiment(experiment_id: str) -> dict[str, Any]:
    data = _load_experiment(_experiment_path(experiment_id))
    if data is None:
        raise HTTPException(500, "Experiment file is unreadable")
    return data


# Declared before `/experiments/{experiment_id}`, which would otherwise capture
# "compare" as an id.
@router.get("/experiments/compare", summary="Paired comparison of two runs")
async def compare_experiments(a: str, b: str) -> dict[str, Any]:
    """B minus A per metric, paired by item, with a 95% interval and p-value.

    409 when the runs are over different datasets or splits: their items do
    not pair, so there is nothing honest to report.
    """
    from evals.compare import comparability_problems, compare_records

    record_a, record_b = _read_experiment(a), _read_experiment(b)
    problems = comparability_problems(record_a, record_b)
    if problems:
        raise HTTPException(409, "; ".join(problems))
    return {
        "a": a,
        "b": b,
        "method": "paired bootstrap (10,000 resamples) + exact sign-flip test, seed 0",
        "metrics": [row.to_dict() for row in compare_records(record_a, record_b)],
    }


@router.get("/experiments/{experiment_id}", summary="Full experiment record")
async def experiment(experiment_id: str) -> dict[str, Any]:
    return _read_experiment(experiment_id)
