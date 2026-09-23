"""API surface tests.

`/health` must answer even when Postgres is down -- that is the whole point of
a health endpoint -- so the database failure path is tested explicitly.
"""

from __future__ import annotations

from pathlib import Path
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


# --- config selection -----------------------------------------------------
@pytest.mark.parametrize("name", ["../.env", "C:/Windows/win.ini", "/etc/passwd", "nope"])
def test_query_only_accepts_config_names_from_the_listing(client: TestClient, name: str) -> None:
    """`load_config` accepts filesystem paths; an HTTP client must not."""
    response = client.post("/query", json={"question": "What is a Pod?", "config_name": name})
    assert response.status_code == 404
    assert "Available" in response.json()["detail"]


def test_default_config_is_the_shipping_pipeline() -> None:
    from app.core.settings import Settings

    assert Settings(_env_file=None).gt_default_config == "full"  # type: ignore[call-arg]


# --- experiment comparison ------------------------------------------------
def _write_record(directory, name: str, scores: list[float], split: str = "dev") -> None:  # type: ignore[no-untyped-def]
    import json

    items = [
        {"item_id": f"q{i}", "gold_total": 1, "recall@5": s, "mrr": s} for i, s in enumerate(scores)
    ]
    record = {
        "config": {"name": name},
        "dataset_version": "golden_v1",
        "split": split,
        "mode": "retrieval",
        "timestamp": "2026-09-23T00:00:00+00:00",
        "metrics": {},
        "items": items,
    }
    (directory / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")


def test_compare_pairs_two_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.api.query.EXPERIMENTS_DIR", tmp_path)
    _write_record(tmp_path, "a", [0.0, 1.0, 1.0, 0.0])
    _write_record(tmp_path, "b", [1.0, 1.0, 1.0, 0.0])

    response = client.get("/experiments/compare", params={"a": "a", "b": "b"})
    assert response.status_code == 200
    recall = next(m for m in response.json()["metrics"] if m["metric"] == "recall@5")
    assert recall["delta"] == pytest.approx(0.25)
    assert recall["wins"] == 1 and recall["losses"] == 0
    assert recall["distinguishable"] is False


def test_compare_refuses_runs_over_different_items(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.api.query.EXPERIMENTS_DIR", tmp_path)
    _write_record(tmp_path, "a", [1.0], split="dev")
    _write_record(tmp_path, "b", [1.0], split="test")

    assert client.get("/experiments/compare", params={"a": "a", "b": "b"}).status_code == 409


def test_experiment_ids_cannot_escape_the_directory(client: TestClient) -> None:
    assert client.get("/experiments/..%2F..%2F.env").status_code == 404


# --- admin ------------------------------------------------------------------
def test_admin_endpoints_are_off_without_a_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.settings import Settings

    monkeypatch.delenv("GT_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr("app.api.admin.get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    assert client.post("/ingest", json={}).status_code == 404


def test_admin_endpoints_reject_a_wrong_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.settings import Settings

    monkeypatch.setattr(
        "app.api.admin.get_settings",
        lambda: Settings(_env_file=None, gt_admin_token="s3cret"),  # type: ignore[call-arg]
    )
    assert client.post("/ingest", json={}).status_code == 401
    assert client.post("/ingest", json={}, headers={"X-Admin-Token": "guess"}).status_code == 401


def test_admin_rejects_unknown_versions_before_starting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.settings import Settings

    monkeypatch.setattr(
        "app.api.admin.get_settings",
        lambda: Settings(_env_file=None, gt_admin_token="s3cret"),  # type: ignore[call-arg]
    )
    response = client.post(
        "/ingest", json={"versions": ["9.99"]}, headers={"X-Admin-Token": "s3cret"}
    )
    assert response.status_code == 422


def test_superseded_runs_are_listed_only_on_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.api.query.EXPERIMENTS_DIR", tmp_path)
    (tmp_path / "superseded").mkdir()
    _write_record(tmp_path, "current", [1.0])
    _write_record(tmp_path / "superseded", "old", [0.0])

    default = client.get("/experiments").json()
    assert [run["id"] for run in default] == ["current"]

    everything = client.get("/experiments", params={"include_superseded": True}).json()
    assert {run["id"]: run["superseded"] for run in everything} == {
        "current": False,
        "superseded/old": True,
    }
    assert client.get("/experiments/superseded/old").status_code == 200
    compared = client.get("/experiments/compare", params={"a": "superseded/old", "b": "current"})
    assert compared.status_code == 200
