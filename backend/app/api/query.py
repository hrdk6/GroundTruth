"""Query, feedback, and experiment endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.llm import MissingAPIKeyError
from app.core.logging import get_logger
from app.core.pipeline import list_configs, load_config
from app.core.settings import REPO_ROOT
from app.generation.answer import AnswerService
from app.models import Feedback
from app.retrieval.versioning import indexed_versions

router = APIRouter(tags=["query"])
log = get_logger(__name__)

EXPERIMENTS_DIR = REPO_ROOT / "experiments"


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    version: str | None = Field(default=None, description="Force a corpus version, e.g. '1.28'")
    config_name: str | None = Field(
        default=None, description="Pipeline config; defaults to baseline"
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


class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationModel]
    version_used: str | None
    version_reason: str
    conflicts: list[dict[str, Any]]
    verification: dict[str, Any]
    abstained: bool
    trace_id: str | None
    latency_ms: float
    cost_usd: float


@router.post("/query", response_model=QueryResponse, summary="Ask a grounded question")
async def query(
    request: QueryRequest, session: AsyncSession = Depends(get_session)
) -> QueryResponse:
    config_name = request.config_name or "baseline"
    try:
        config = load_config(config_name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    service = AnswerService(config)

    # The retrieval and generation stack is synchronous (SQLAlchemy sync
    # session, CPU-bound encoders), so it runs in a worker thread rather than
    # blocking the event loop for every other request.
    def run() -> Any:
        from app.core.db import session_scope

        with session_scope() as sync_session:
            return service.answer(sync_session, request.question, version=request.version)

    import anyio

    try:
        result = await anyio.to_thread.run_sync(run)
    except MissingAPIKeyError as exc:
        raise HTTPException(
            503,
            "Generation needs ANTHROPIC_API_KEY. Retrieval-only evaluation does not.",
        ) from exc

    return QueryResponse(
        answer=result.answer,
        citations=[CitationModel(**c.to_dict()) for c in result.citations],
        version_used=result.version_used,
        version_reason=result.version_reason,
        conflicts=result.conflicts,
        verification=result.verification,
        abstained=result.abstained,
        trace_id=getattr(result, "trace_id", None),
        latency_ms=round(result.latency_ms, 2),
        cost_usd=round(result.cost_usd, 6),
    )


class VersionsResponse(BaseModel):
    versions: list[str]
    configs: list[str]


@router.get("/versions", response_model=VersionsResponse, summary="Indexed versions and configs")
async def versions(session: AsyncSession = Depends(get_session)) -> VersionsResponse:
    def run() -> list[str]:
        from app.core.db import session_scope

        with session_scope() as sync_session:
            return indexed_versions(sync_session)

    import anyio

    return VersionsResponse(versions=await anyio.to_thread.run_sync(run), configs=list_configs())


class FeedbackRequest(BaseModel):
    trace_id: str
    helpful: bool
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/feedback", status_code=201, summary="Record thumbs up/down on an answer")
async def feedback(request: FeedbackRequest) -> dict[str, str]:
    def run() -> None:
        from app.core.db import session_scope

        with session_scope() as sync_session:
            sync_session.add(
                Feedback(
                    trace_id=request.trace_id,
                    helpful=request.helpful,
                    comment=request.comment,
                )
            )

    import anyio

    await anyio.to_thread.run_sync(run)
    return {"status": "recorded"}


class ExperimentSummary(BaseModel):
    id: str
    config_name: str
    split: str
    mode: str
    timestamp: str
    git_sha: str | None = None
    dataset_version: str | None = None
    metrics: dict[str, Any] = {}
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
        out.append(
            ExperimentSummary(
                id=data["id"],
                config_name=data.get("config", {}).get("name", "?"),
                split=data.get("split", "?"),
                mode=data.get("mode", "?"),
                timestamp=data.get("timestamp", ""),
                git_sha=data.get("git_sha"),
                dataset_version=data.get("dataset_version"),
                metrics=data.get("metrics", {}),
                cost_usd=data.get("cost", {}).get("cost_usd"),
            )
        )
    return out


@router.get("/experiments/{experiment_id}", summary="Full experiment record")
async def experiment(experiment_id: str) -> dict[str, Any]:
    # Resolve inside the directory and compare: an id like "../../.env" must
    # not escape into the rest of the filesystem.
    path = (EXPERIMENTS_DIR / f"{experiment_id}.json").resolve()
    if not path.is_relative_to(EXPERIMENTS_DIR.resolve()) or not path.exists():
        raise HTTPException(404, f"No experiment {experiment_id!r}")

    data = _load_experiment(path)
    if data is None:
        raise HTTPException(500, "Experiment file is unreadable")
    return data
