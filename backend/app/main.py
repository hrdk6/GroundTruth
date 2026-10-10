"""FastAPI application factory.

Routers: health, query (+ feedback, experiments), traces, and admin ingestion.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import admin, health, query, traces
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings

log = get_logger(__name__)

# How often the API deletes traces past their retention. Daily would do; a few
# hours keeps a restart-happy deployment from never reaching its first prune.
PRUNE_INTERVAL_SECONDS = 6 * 3600


def _prune_once(days: int) -> None:
    from app.core.db import session_scope
    from app.tracing.retention import prune_traces

    with session_scope() as session:
        prune_traces(session, older_than_days=days)


async def _prune_periodically(days: int) -> None:
    """Delete expired traces now and every few hours. A failure waits for the next run."""
    while True:
        try:
            await anyio.to_thread.run_sync(_prune_once, days)
        except Exception as exc:  # noqa: BLE001 - the database may simply be down
            log.warning("traces.prune_failed", error=type(exc).__name__)
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.gt_log_level, settings.gt_log_format)
    log.info(
        "app.startup",
        version=__version__,
        generation_model=settings.gt_generation_model,
        anthropic_key_configured=settings.has_anthropic_key,
        trace_retention_days=settings.gt_trace_retention_days,
    )
    pruner = (
        asyncio.create_task(_prune_periodically(settings.gt_trace_retention_days))
        if settings.gt_trace_retention_days > 0
        else None
    )
    yield
    if pruner is not None:
        pruner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pruner
    log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="GroundTruth",
        description=(
            "A self-evaluating, version-aware RAG platform over the Kubernetes documentation."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    # The Next.js dev server is the only browser client by default (and it
    # proxies, so it rarely needs CORS at all). `GT_CORS_ORIGINS` widens it
    # for a deployment; it is never `*`, because the API sets credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().gt_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Admin-Token"],
    )

    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(traces.router)
    app.include_router(admin.router)
    return app


app = create_app()
