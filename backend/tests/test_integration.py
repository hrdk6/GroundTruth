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

    # The seccomp tutorial's "Create a Pod with a seccomp profile" section was
    # rewritten between 1.26 and 1.28 -- a genuine change, not a chunking echo.
    vector = get_embedder(config.embedding).embed_query("create a pod with a seccomp profile")
    candidates = dense_search(db_session, vector, config, version="1.28")

    conflicts = detect_conflicts(
        db_session,
        candidates,
        chunker_name=config.chunker_name,
        answer_version="1.28",
        all_versions=FIXTURE_VERSIONS,
        max_sections=10,
    )
    # The fixture pages were chosen because they differ across the two
    # versions, so a query about them must surface at least one conflict. (This
    # used to assert only `isinstance(conflicts, list)`, which cannot fail.)
    assert conflicts, "expected at least one section that differs between 1.26 and 1.28"
    assert any("seccomp" in c.source_path for c in conflicts)
    for conflict in conflicts:
        assert conflict.latest_version == "1.28"
        assert conflict.other_version == "1.26"
        assert conflict.similarity < 0.92


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


def test_a_restored_page_whose_content_changed_is_re_chunked(
    ingested, db_session, config, tmp_path
) -> None:
    """Regression: resurrection skipped straight past re-chunking.

    A tombstoned page that came back *with new content* was un-deleted and
    then skipped, so it kept its old chunks under its old hash, and retrieval
    served text the page no longer contained.
    """
    import shutil

    partial = tmp_path / "partial"
    shutil.copytree(FIXTURE_CORPUS, partial)
    victim = sorted((partial / "1.28").rglob("*.md"))[1]
    victim_path = victim.relative_to(partial / "1.28").as_posix()
    original = victim.read_text(encoding="utf-8")
    victim.unlink()
    ingest(db_session, config, versions=["1.28"], root=partial, fetch=False)
    db_session.commit()

    changed = tmp_path / "changed"
    shutil.copytree(FIXTURE_CORPUS, changed)
    marker = "Zyzzyva-marker-sentence for the resurrection test."
    (changed / "1.28" / victim_path).write_text(
        original + f"\n\n## Resurrected\n\n{marker}\n", encoding="utf-8"
    )
    stats = ingest(db_session, config, versions=["1.28"], root=changed, fetch=False)
    db_session.commit()

    assert stats.documents_restored == 1
    assert stats.chunks_embedded > 0, "changed content must be re-chunked and re-embedded"
    texts = db_session.execute(
        select(Chunk.text)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Document.source_path == victim_path,
            Document.version == "1.28",
            Chunk.chunker_name == config.chunker_name,
        )
    ).scalars()
    assert any(marker in t for t in texts)

    # Leave the corpus as the other tests expect it.
    ingest(db_session, config, versions=["1.28"], root=FIXTURE_CORPUS, fetch=False)
    db_session.commit()


def test_ingesting_an_older_version_alone_keeps_the_newest_as_latest(
    ingested, db_session, config
) -> None:
    """Regression: `latest` was computed from the run's versions, not the index's.

    Ingesting `--versions 1.26` into a database that also held 1.28 flagged
    the 1.26 copies as the latest ones.
    """
    ingest(db_session, config, versions=["1.26"], root=FIXTURE_CORPUS, fetch=False)
    db_session.commit()

    flagged = set(
        db_session.execute(
            select(Document.version).where(Document.is_latest_for_path.is_(True))
        ).scalars()
    )
    shared = db_session.execute(
        select(Document.source_path)
        .where(Document.deleted_at.is_(None))
        .group_by(Document.source_path)
        .having(func.count() > 1)
    ).scalars()
    for source_path in shared:
        latest = (
            db_session.execute(
                select(Document.version).where(
                    Document.source_path == source_path, Document.is_latest_for_path.is_(True)
                )
            )
            .scalars()
            .all()
        )
        assert latest == ["1.28"], source_path
    assert "1.28" in flagged


def test_every_fixture_gold_quote_is_matchable_by_a_chunk(ingested, db_session, config) -> None:
    """The index can express every gold hit: recall is not capped by chunking.

    The document-level check (`test_fixture_quotes_exist_in_the_fixture_corpus`)
    passed the whole time the fixed chunker was lowercasing its text. This is
    the same check one layer down, where it matters.
    """
    from evals.integrity import audit_gold_evidence

    for name in ("hybrid", "baseline"):
        chunker_config = load_config(name)
        if chunker_config.chunker_name != config.chunker_name:
            ingest(
                db_session,
                chunker_config,
                versions=FIXTURE_VERSIONS,
                root=FIXTURE_CORPUS,
                fetch=False,
            )
            db_session.commit()
        report = audit_gold_evidence(
            db_session, chunker_config, load_dataset("fixture_golden.jsonl")
        )
        assert report.ok, [issue.to_dict() for issue in report.issues]
        assert report.recall_ceiling == 1.0


def test_ranked_list_runs_deeper_than_the_context(ingested, db_session, config) -> None:
    result, _ = Retriever(config).retrieve(db_session, "How is memory measured?", version="1.28")
    assert len(result.candidates) == config.retrieval.k_final
    assert len(result.ranked) > len(result.candidates), "ranking metrics need the full list"
    assert result.ranked[: len(result.candidates)] == result.candidates


# ---------------------------------------------------------------------------
# API against the database
# ---------------------------------------------------------------------------
def test_feedback_for_an_unknown_trace_is_rejected(ingested, db_session) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.post("/feedback", json={"trace_id": "no-such-trace", "helpful": True})
    assert response.status_code == 404


def test_a_failed_query_still_leaves_a_trace(ingested, db_session, monkeypatch) -> None:
    """The failed query is the one most worth a trace, and it used to get none:
    the request's transaction rolled back and took the trace with it."""
    from fastapi.testclient import TestClient

    from app.generation.answer import AnswerService
    from app.main import create_app

    def explode(self, session, question, *, version=None, tracer=None):  # type: ignore[no-untyped-def]
        assert tracer is not None
        with tracer.span("dense"):
            raise RuntimeError("the index is on fire")

    monkeypatch.setattr(AnswerService, "answer", explode)
    client = TestClient(create_app())
    response = client.post("/query", json={"question": "What is a Pod?", "config_name": "hybrid"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "RuntimeError" in detail and "trace " in detail

    trace_id = detail.rsplit("trace ", 1)[1].rstrip(").")
    trace = client.get(f"/traces/{trace_id}").json()
    assert trace["status"] == "error"
    assert trace["meta"]["error"] == "RuntimeError"
    assert [s["status"] for s in trace["spans"]] == ["error"]


def test_dense_search_returns_k_even_when_filters_reject_most_of_the_index(
    ingested, db_session, config
) -> None:
    """Regression: HNSW filters *after* the index scan in pgvector < 0.8.

    With ef_search at its default of 40 and several chunk sets and versions in
    one index, a filtered query came back with a fraction of the k it asked
    for. The baseline's dense leg returned 16 of 20 on the real corpus.
    """
    from app.ingestion.embed import get_embedder

    baseline = load_config("baseline")  # a second chunk set in the same index
    if baseline.chunker_name != config.chunker_name:
        ingest(db_session, baseline, versions=FIXTURE_VERSIONS, root=FIXTURE_CORPUS, fetch=False)
        db_session.commit()

    matching = db_session.execute(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.chunker_name == config.chunker_name, Chunk.version == "1.26")
    ).scalar_one()
    k = min(60, matching)
    vector = get_embedder(config.embedding).embed_query("resource limits for containers")
    hits = dense_search(db_session, vector, config, version="1.26", k=k)

    assert len(hits) == k
    assert {c.version for c in hits} == {"1.26"}
