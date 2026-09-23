"""Admin endpoints: trigger an ingestion run and watch it (PROJECT_SPEC.md S8.5).

Guarded because re-embedding the corpus is minutes of CPU and rewrites the
index every query reads from:

* **Off unless configured.** With no `GT_ADMIN_TOKEN` the routes answer 404, so
  a default deployment exposes nothing.
* **Token in a header, compared in constant time.** `hmac.compare_digest`, so
  response timing does not leak how much of a guess was right.
* **One run at a time.** Two concurrent ingests of the same chunker would race
  on the delete-and-reinsert of each document's chunks; the second request
  gets 409 instead.

The run happens on a background thread and the request returns 202
immediately: an HTTP request that blocks for ten minutes is a timeout, not an
API. `GET /ingest` reports progress and the most recent recorded runs.
"""

from __future__ import annotations

import hmac
import threading
import time
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.api.query import resolve_config
from app.core.db import session_scope
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.ingestion.fetch import VERSION_BRANCHES
from app.ingestion.pipeline import ingest
from app.models import IngestionRun

router = APIRouter(tags=["admin"], prefix="/ingest")
log = get_logger(__name__)


class IngestRequest(BaseModel):
    config_name: str | None = Field(default=None, description="Pipeline config from configs/")
    versions: list[str] | None = Field(default=None, description="Default: every known version")
    include: list[str] | None = Field(
        default=None, description="Only paths under these prefixes, e.g. ['concepts', 'tasks']"
    )
    fetch: bool = Field(default=False, description="Download missing branch tarballs first")


class _RunState:
    """What the one permitted background run is doing."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.started_at: str | None = None
        self.request: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "request": self.request,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }


_state = _RunState()


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    configured = get_settings().gt_admin_token
    secret = configured.get_secret_value() if configured else ""
    if not secret:
        raise HTTPException(404, "Admin endpoints are disabled. Set GT_ADMIN_TOKEN to enable them.")
    if not x_admin_token or not hmac.compare_digest(x_admin_token.encode(), secret.encode()):
        raise HTTPException(401, "Missing or wrong X-Admin-Token header.")


def _run(request: IngestRequest, config_name: str) -> None:
    started = time.perf_counter()
    try:
        from app.core.pipeline import load_config

        config = load_config(config_name)
        with session_scope() as session:
            stats = ingest(
                session,
                config,
                versions=request.versions,
                fetch=request.fetch,
                include=request.include,
            )
        _state.last_result = {**stats.to_dict(), "config": config.name}
        _state.last_error = None
        log.info("admin.ingest_complete", seconds=round(time.perf_counter() - started, 1))
    except Exception as exc:
        _state.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
        log.exception("admin.ingest_failed")
    finally:
        with _state.lock:
            _state.running = False


@router.post("", status_code=202, dependencies=[Depends(require_admin)], summary="Start ingestion")
async def start_ingest(request: IngestRequest) -> dict[str, Any]:
    config = resolve_config(request.config_name)
    unknown = sorted(set(request.versions or []) - set(VERSION_BRANCHES))
    if unknown:
        raise HTTPException(422, f"Unknown versions {unknown}; known: {sorted(VERSION_BRANCHES)}")

    with _state.lock:
        if _state.running:
            raise HTTPException(409, "An ingestion run is already in progress.")
        _state.running = True
        _state.started_at = datetime.now(UTC).isoformat()
        _state.request = {**request.model_dump(), "config_name": config.name}

    thread = threading.Thread(target=_run, args=(request, config.name), daemon=True)
    thread.start()
    return {"status": "started", **_state.snapshot()}


@router.get("", dependencies=[Depends(require_admin)], summary="Ingestion status and history")
async def ingest_status(limit: int = 10) -> dict[str, Any]:
    def recent() -> list[dict[str, Any]]:
        with session_scope() as session:
            rows = session.execute(
                select(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(limit)
            ).scalars()
            return [
                {
                    "id": run.id,
                    "config_name": run.config_name,
                    "chunker_name": run.chunker_name,
                    "versions": run.versions,
                    "added": run.documents_added,
                    "updated": run.documents_updated,
                    "unchanged": run.documents_unchanged,
                    "deleted": run.documents_deleted,
                    "chunks_embedded": run.chunks_embedded,
                    "duration_seconds": round(run.duration_seconds, 1),
                    "started_at": run.started_at.isoformat(),
                    "meta": run.meta,
                }
                for run in rows
            ]

    return {**_state.snapshot(), "recent_runs": await anyio.to_thread.run_sync(recent)}
