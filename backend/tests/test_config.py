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
    assert a.chunker_name.startswith("fixed-512-64-r2-")


@pytest.mark.parametrize(
    "change",
    [
        {"chunking": {"chunker": "structure_aware", "min_tokens": 16}},
        {"chunking": {"chunker": "structure_aware", "prepend_heading_path": False}},
        {"embedding": {"model": "sentence-transformers/all-MiniLM-L6-v2"}},
        {"embedding": {"document_prefix": "passage: "}},
    ],
)
def test_chunk_identity_covers_everything_that_changes_a_vector(change: dict) -> None:
    """Regression: the name used to be `strategy-size-overlap` only.

    Two configs differing only in `min_tokens`, heading prepending, or the
    embedding model shared rows, so ingesting the second one skipped every
    document ("chunks already exist") and its experiment measured the first
    one's index.
    """
    base = PipelineConfig(name="a", chunking={"chunker": "structure_aware"})  # type: ignore[arg-type]
    merged = {
        "chunking": {**base.chunking.model_dump(), **change.get("chunking", {})},
        "embedding": {**base.embedding.model_dump(), **change.get("embedding", {})},
    }
    other = PipelineConfig(name="b", **merged)
    assert other.chunker_name != base.chunker_name


def test_chunk_identity_ignores_retrieval_settings() -> None:
    """Retrieval experiments must reuse the same chunks, or none could share an index."""
    hybrid = load_config("hybrid")
    rerank = load_config("hybrid_rerank")
    assert hybrid.config_hash != rerank.config_hash
    assert hybrid.chunker_name == rerank.chunker_name


def test_chunk_identity_includes_the_implementation_revision() -> None:
    from app.core.pipeline import CHUNKER_REVISIONS

    config = load_config("structure_aware")
    assert f"-r{CHUNKER_REVISIONS['structure_aware']}-" in config.chunker_name


def test_lexical_config_is_pinned_to_the_indexed_language() -> None:
    """The tsv column is generated as `english`; any other query config silently mismatches."""
    with pytest.raises(ValidationError):
        PipelineConfig(name="x", retrieval={"lexical": {"text_search_config": "simple"}})  # type: ignore[arg-type]


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


def test_dense_plus_lexical_without_fusion_is_rejected() -> None:
    """Merging by raw score would compare cosine similarity against ts_rank_cd."""
    with pytest.raises(ValidationError, match="fusion"):
        PipelineConfig(
            name="x",
            retrieval={"lexical": {"enabled": True}, "fusion": {"enabled": False}},  # type: ignore[arg-type]
        )


def test_hybrid_configs_match_any_term_and_the_ablation_keeps_all() -> None:
    for name in ("hybrid", "hybrid_rerank", "full"):
        assert load_config(name).retrieval.lexical.match == "any", name
    ablation = load_config("hybrid_all_terms")
    assert ablation.retrieval.lexical.match == "all"
    # One variable: everything else is hybrid's.
    hybrid = load_config("hybrid").model_dump()
    other = ablation.model_dump()
    hybrid["retrieval"]["lexical"].pop("match")
    other["retrieval"]["lexical"].pop("match")
    for key in ("name", "description"):
        hybrid.pop(key)
        other.pop(key)
    assert hybrid == other
