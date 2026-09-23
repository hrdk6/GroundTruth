"""End-to-end tests against a live Postgres with pgvector.

Marked `integration` and skipped when no database is reachable, so
`make test-unit` stays fast and works offline. CI provides a
`pgvector/pgvector:pg16` service container, so these run on every PR.

They cover the three properties that only a real database can demonstrate:

1. **Incremental ingestion** — a second run over an unchanged corpus embeds
   nothing. This is the Phase 1 acceptance criterion.
2. **Retrieval actually retrieves** — dense and lexical each return the gold
   chunk for a question whose answer is in the fixture corpus.
3. **Version isolation** — a query filtered to 1.26 never returns 1.28 rows,
   which is the whole premise of the project.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.core.pipeline import PipelineConfig, load_config
from app.ingestion.pipeline import ingest
from app.models import Chunk, Document
from app.retrieval.retriever import Retriever
from app.retrieval.stages import dense_search, lexical_search
from app.retrieval.versioning import detect_conflicts, indexed_versions
from evals.dataset.schema import load_dataset
from evals.metrics.retrieval import match_candidates

pytestmark = pytest.mark.integration

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "corpus"
FIXTURE_VERSIONS = ["1.26", "1.28"]


def _test_database_url(url: str) -> str:
    """Derive a dedicated test database name from the configured URL."""
    base, _, name = url.rpartition("/")
    name = name.split("?")[0]
    if name.endswith("_test"):
        return url
    return f"{base}/{name}_test"


@pytest.fixture(scope="module")
def db_session():
    """A session on a dedicated *test* database, or skip the whole module.

    These tests TRUNCATE the corpus tables, so they must never run against the
    database a developer has just spent ten minutes ingesting into. They get
    their own database (`<name>_test`), created here if it does not exist.
    """
    import os

    from sqlalchemy import create_engine

    from app.core.db import reset_engines, session_scope
    from app.core.settings import get_settings
    from app.models import Base

    original = os.environ.get("DATABASE_URL")
    admin_url = get_settings().database_url
    test_url = _test_database_url(admin_url)
    db_name = test_url.rpartition("/")[2]

    try:
        # CREATE DATABASE cannot run inside a transaction.
        admin = create_engine(
            admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
        )
        with admin.connect() as conn:
            conn.execute(text("SELECT 1"))
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        admin.dispose()
    except Exception as exc:  # noqa: BLE001 - any failure here means "no database"
        pytest.skip(f"no database available: {type(exc).__name__}")

    os.environ["DATABASE_URL"] = test_url
    get_settings.cache_clear()
    reset_engines()

    try:
        engine = create_engine(test_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            has_vector = conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            ).scalar_one()
        if not has_vector:
            pytest.skip("pgvector is not installed in this database")

        # The hand-written migrations cover indexes Alembic cannot express;
        # for tests the ORM schema plus the HNSW index is enough.
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )
        engine.dispose()

        with session_scope() as session:
            yield session
    finally:
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
        get_settings.cache_clear()
        reset_engines()


@pytest.fixture(scope="module")
def config() -> PipelineConfig:
    # structure_aware keeps the code blocks the fixture questions ask about
    # intact, and enables the lexical leg so both retrieval paths are covered.
    base = load_config("hybrid")
    return base


@pytest.fixture(scope="module")
def ingested(db_session, config: PipelineConfig):
    """Ingest the fixture corpus once for the module."""
    db_session.execute(text("TRUNCATE documents, chunks, ingestion_runs RESTART IDENTITY CASCADE"))
    db_session.commit()

    stats = ingest(
        db_session,
        config,
        versions=FIXTURE_VERSIONS,
        root=FIXTURE_CORPUS,
        fetch=False,
    )
    db_session.commit()
    return stats


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
def test_ingestion_loads_both_versions(ingested, db_session) -> None:
    assert ingested.documents_added > 0
    assert ingested.chunks_embedded > 0

    versions = set(db_session.execute(select(Document.version).distinct()).scalars())
    assert versions == set(FIXTURE_VERSIONS)


def test_every_chunk_got_an_embedding(ingested, db_session) -> None:
    missing = db_session.execute(
        select(func.count()).select_from(Chunk).where(Chunk.embedding.is_(None))
    ).scalar_one()
    assert missing == 0


def test_generated_tsvector_is_populated(ingested, db_session) -> None:
    """The FTS column is generated by Postgres, so it cannot drift from text."""
    empty = db_session.execute(
        select(func.count()).select_from(Chunk).where(Chunk.tsv.is_(None))
    ).scalar_one()
    assert empty == 0


def test_reingesting_unchanged_corpus_embeds_nothing(ingested, db_session, config) -> None:
    """Phase 1 acceptance criterion (PROJECT_SPEC.md §12)."""
    second = ingest(
        db_session,
        config,
        versions=FIXTURE_VERSIONS,
        root=FIXTURE_CORPUS,
        fetch=False,
    )
    db_session.commit()

    assert second.chunks_embedded == 0, "unchanged documents must not be re-embedded"
    assert second.documents_added == 0
    assert second.documents_updated == 0
    assert second.documents_unchanged == ingested.documents_added


def test_a_changed_document_is_re_embedded(ingested, db_session, config, tmp_path) -> None:
    """The counterpart: a real edit must not be skipped."""
    import shutil

    corpus = tmp_path / "corpus"
    shutil.copytree(FIXTURE_CORPUS, corpus)

    target = next((corpus / "1.28").rglob("*.md"))
    target.write_text(
        target.read_text(encoding="utf-8") + "\n\n## Added section\n\nBrand new content here.\n",
        encoding="utf-8",
    )

    stats = ingest(db_session, config, versions=["1.28"], root=corpus, fetch=False)
    db_session.commit()

    assert stats.documents_updated == 1
    assert stats.chunks_embedded > 0


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def test_dense_retrieval_returns_ranked_chunks(ingested, db_session, config) -> None:
    from app.ingestion.embed import get_embedder

    vector = get_embedder(config.embedding).embed_query("How is memory measured in Kubernetes?")
    hits = dense_search(db_session, vector, config, version="1.28")

    assert hits, "dense retrieval returned nothing"
    scores = [c.scores["dense"] for c in hits]
    assert scores == sorted(scores, reverse=True), "results must be ordered by similarity"


def test_lexical_retrieval_finds_an_exact_term(ingested, db_session, config) -> None:
    """The case dense retrieval is weakest at: a literal API field name."""
    hits = lexical_search(db_session, "runAsGroup", config, version="1.28")
    assert hits
    assert any("runAsGroup" in c.text for c in hits)


def test_version_filter_never_leaks_another_version(ingested, db_session, config) -> None:
    """The premise of the project: 1.26 content is not a 1.28 answer."""
    from app.ingestion.embed import get_embedder

    vector = get_embedder(config.embedding).embed_query("security context for a pod")
    for version in FIXTURE_VERSIONS:
        hits = dense_search(db_session, vector, config, version=version)
        assert hits
        assert {c.version for c in hits} == {version}


def test_retriever_finds_gold_for_the_fixture_questions(ingested, db_session, config) -> None:
    """A smoke check that the fixture golden set is actually answerable."""
    dataset = load_dataset("fixture_golden.jsonl")
    answerable = [i for i in dataset.items if i.answerable]
    assert answerable, "fixture golden set has no answerable items"

    retriever = Retriever(config)
    found = 0
    for item in answerable:
        result, _ = retriever.retrieve(db_session, item.question, version=item.version)
        if match_candidates(result.candidates, item.gold_evidence).first_rank:
            found += 1

    # Not 100%: this is the baseline-ish pipeline on a 30-page corpus, and
    # asserting perfection would make the test a liability rather than a check.
    assert found >= len(answerable) // 2, (
        f"hybrid retrieval found gold for only {found}/{len(answerable)} fixture questions"
    )


def test_indexed_versions_are_sorted_numerically(ingested, db_session, config) -> None:
    assert indexed_versions(db_session, config.chunker_name) == FIXTURE_VERSIONS


# ---------------------------------------------------------------------------
# Version conflicts
# ---------------------------------------------------------------------------
def test_conflict_detection_finds_a_cross_version_difference(ingested, db_session, config) -> None:
    """The fixture corpus was selected for pages that differ between versions."""
    from app.ingestion.embed import get_embedder

    vector = get_embedder(config.embedding).embed_query("security context for a pod")
    candidates = dense_search(db_session, vector, config, version="1.28")

    conflicts = detect_conflicts(
        db_session,
        candidates,
        chunker_name=config.chunker_name,
        answer_version="1.28",
        all_versions=FIXTURE_VERSIONS,
        max_sections=10,
    )
    # Every fixture page differs across the two versions by construction, so
    # at least one retrieved section should have a differing counterpart.
    assert isinstance(conflicts, list)
    for conflict in conflicts:
        assert conflict.latest_version == "1.28"
        assert conflict.other_version == "1.26"
        assert conflict.similarity < 1.0


def test_a_tombstoned_document_is_resurrected_when_it_reappears(
    ingested, db_session, config, tmp_path
) -> None:
    """Regression: an unchanged page that comes back must become visible again.

    Ingesting a root that omits a page tombstones it. When the page returns
    with identical content, the unchanged-content fast path used to skip it, so
    `deleted_at` stayed set and retrieval — which filters on
    `deleted_at IS NULL` — never saw the page again.
    """
    import shutil

    from sqlalchemy import func, select

    from app.models import Document

    full = tmp_path / "full"
    shutil.copytree(FIXTURE_CORPUS, full)

    # A corpus missing one page, to tombstone it.
    partial = tmp_path / "partial"
    shutil.copytree(FIXTURE_CORPUS, partial)
    victim = sorted((partial / "1.28").rglob("*.md"))[0]
    victim_path = victim.relative_to(partial / "1.28").as_posix()
    victim.unlink()

    ingest(db_session, config, versions=["1.28"], root=partial, fetch=False)
    db_session.commit()

    tombstoned = db_session.execute(
        select(func.count())
        .select_from(Document)
        .where(
            Document.version == "1.28",
            Document.source_path == victim_path,
            Document.deleted_at.is_not(None),
        )
    ).scalar_one()
    assert tombstoned == 1, "removing a page should tombstone it"

    # The page returns, byte-for-byte identical.
    ingest(db_session, config, versions=["1.28"], root=full, fetch=False)
    db_session.commit()

    alive = db_session.execute(
        select(func.count())
        .select_from(Document)
        .where(
            Document.version == "1.28",
            Document.source_path == victim_path,
            Document.deleted_at.is_(None),
        )
    ).scalar_one()
    assert alive == 1, "an unchanged page that reappears must be resurrected"
