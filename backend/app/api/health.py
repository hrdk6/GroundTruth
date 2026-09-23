"""Health endpoint.

Reports *degraded* rather than failing when Postgres is unreachable: an
operator wants to see which dependency is broken, and returning 503 from the
one endpoint that explains the problem is unhelpful. Liveness is the HTTP
status; readiness is the `status` field.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.core.db import check_connection
from app.core.llm import get_llm_client
from app.core.logging import get_logger
from app.core.settings import get_settings

router = APIRouter(tags=["system"])
log = get_logger(__name__)


class DatabaseHealth(BaseModel):
    connected: bool
    server_version: str | None = None
    pgvector: bool = False
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: DatabaseHealth
    anthropic_key_configured: bool
    llm_cache: dict[str, Any]


@router.get("/health", response_model=HealthResponse, summary="Service and dependency health")
async def health() -> HealthResponse:
    settings = get_settings()

    try:
        info = await check_connection()
        db = DatabaseHealth(
            connected=True,
            server_version=str(info["server_version"]),
            pgvector=bool(info["pgvector"]),
        )
    except Exception as exc:  # noqa: BLE001 - any failure here means "database down"
        log.warning("health.database_unreachable", error=str(exc))
        db = DatabaseHealth(connected=False, error=type(exc).__name__)

    return HealthResponse(
        status="ok" if db.connected and db.pgvector else "degraded",
        version=__version__,
        database=db,
        anthropic_key_configured=settings.has_anthropic_key,
        llm_cache=dict(get_llm_client().cache.stats()),
    )
