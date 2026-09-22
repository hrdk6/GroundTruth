"""FastAPI application factory.

Routers are added per phase; `/health` is the only one in Phase 0.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
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

    # The Next.js dev server is the only browser client; tighten before any
    # deployment that is reachable from outside localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    return app


app = create_app()
