"""Tests for config loading and the reproducibility guarantees it provides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.core.pipeline import PipelineConfig, list_configs, load_config
from app.core.settings import Settings


# --- loading --------------------------------------------------------------
def test_baseline_config_loads() -> None:
    config = load_config("baseline")
    assert config.name == "baseline"
    assert config.chunking.chunker == "fixed"
    assert config.retrieval.dense.enabled
    assert not config.retrieval.rerank.enabled


def test_name_and_path_forms_are_equivalent() -> None:
    assert load_config("baseline") == load_config("configs/baseline.yaml")


def test_missing_config_lists_what_is_available() -> None:
    with pytest.raises(FileNotFoundError, match="baseline"):
        load_config("does_not_exist")


def test_list_configs_finds_baseline() -> None:
    assert "baseline" in list_configs()


# --- reproducibility ------------------------------------------------------
def test_hash_is_stable_across_loads() -> None:
    assert load_config("baseline").config_hash == load_config("baseline").config_hash


def test_hash_ignores_description_but_tracks_values(tmp_path: Path) -> None:
    """Reformatting prose must not invent a new config; changing k must."""
    base = load_config("baseline")

    reworded = base.model_copy(update={"description": "totally different prose"})
    assert reworded.config_hash == base.config_hash

    retrieval = base.retrieval.model_copy(update={"k_final": 10})
    changed = base.model_copy(update={"retrieval": retrieval})
    assert changed.config_hash != base.config_hash


def test_config_is_frozen() -> None:
    config = load_config("baseline")
    with pytest.raises(ValidationError):
        config.retrieval.k_final = 99  # type: ignore[misc]


def test_chunker_name_includes_parameters() -> None:
    """Two chunkings that differ only in size must not share a DB identity."""
    a = PipelineConfig(name="a", chunking={"chunker": "fixed", "max_tokens": 512})  # type: ignore[arg-type]
    b = PipelineConfig(name="b", chunking={"chunker": "fixed", "max_tokens": 256})  # type: ignore[arg-type]
    assert a.chunker_name != b.chunker_name
    assert a.chunker_name == "fixed-512-64"


def test_to_record_carries_hash_and_values() -> None:
    record = load_config("baseline").to_record()
    assert record["config_hash"]
    assert record["values"]["retrieval"]["k_final"] == 5


# --- validation -----------------------------------------------------------
def test_overlap_must_be_smaller_than_chunk() -> None:
    with pytest.raises(ValidationError, match="overlap_tokens"):
        PipelineConfig(name="bad", chunking={"max_tokens": 128, "overlap_tokens": 128})  # type: ignore[arg-type]


def test_retrieval_needs_at_least_one_leg() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        PipelineConfig(
            name="bad",
            retrieval={"dense": {"enabled": False}, "lexical": {"enabled": False}},  # type: ignore[arg-type]
        )


def test_fusion_requires_both_legs() -> None:
    with pytest.raises(ValidationError, match="fusion requires"):
        PipelineConfig(
            name="bad",
            retrieval={  # type: ignore[arg-type]
                "dense": {"enabled": True},
                "lexical": {"enabled": False},
                "fusion": {"enabled": True},
            },
        )


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    """A typo in YAML must fail loudly, not silently run the wrong pipeline."""
    path = tmp_path / "typo.yaml"
    path.write_text(
        yaml.safe_dump({"name": "typo", "retrieval": {"k_finall": 5}}), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_config(path)


# --- settings -------------------------------------------------------------
def test_database_url_built_from_parts() -> None:
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        postgres_user="u",
        postgres_password="p",
        postgres_host="h",
        postgres_port=5433,
        postgres_db="d",
    )
    assert s.database_url == "postgresql+psycopg://u:p@h:5433/d"


def test_explicit_database_url_wins() -> None:
    s = Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://x:y@z:1/db")  # type: ignore[call-arg]
    assert s.database_url == "postgresql+psycopg://x:y@z:1/db"


def test_api_key_is_not_printed_in_repr() -> None:
    s = Settings(_env_file=None, anthropic_api_key="sk-ant-secret-value")  # type: ignore[call-arg]
    assert "sk-ant-secret-value" not in repr(s)
    assert s.has_anthropic_key
