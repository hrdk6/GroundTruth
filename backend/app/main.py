"""FastAPI application factory.

Routers: health, query (+ feedback, experiments), traces, and admin ingestion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import admin, health, query, traces
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.gt_log_level, settings.gt_log_format)
    log.info(
        "app.startup",
        version=__version__,
        generation_model=settings.gt_generation_model,
        anthropic_key_configured=settings.has_anthropic_key,
    )
    yield
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
