"""API surface tests.

`/health` must answer even when Postgres is down -- that is the whole point of
a health endpoint -- so the database failure path is tested explicitly.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_health_reports_ok_when_database_is_healthy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_check() -> dict[str, Any]:
        return {"server_version": "16.4", "pgvector": True}

    monkeypatch.setattr("app.api.health.check_connection", fake_check)

    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["connected"] is True
    assert body["database"]["pgvector"] is True
    assert body["version"]


def test_health_degrades_when_pgvector_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database that is up but missing pgvector fails confusingly later."""

    async def fake_check() -> dict[str, Any]:
        return {"server_version": "16.4", "pgvector": False}

    monkeypatch.setattr("app.api.health.check_connection", fake_check)

    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["database"]["pgvector"] is False


def test_health_answers_200_when_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_check() -> dict[str, Any]:
        raise ConnectionRefusedError("no database")

    monkeypatch.setattr("app.api.health.check_connection", fake_check)

    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["connected"] is False
    assert body["database"]["error"] == "ConnectionRefusedError"


def test_openapi_schema_builds(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "GroundTruth"
    assert "/health" in schema["paths"]
