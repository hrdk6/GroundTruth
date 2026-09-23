"""Ingestion orchestration: fetch -> parse -> chunk -> embed -> store.

The property that matters here is **incrementality**. Embedding the full K8s
docs across three versions takes minutes; re-embedding them because a run was
repeated would make iterating unaffordable. So each document carries a SHA-256
of its normalized content, and a document whose hash is unchanged is skipped
before it is ever chunked or embedded.

PROJECT_SPEC.md S12 makes this the Phase 1 acceptance criterion: a second run
over an unchanged corpus must report `chunks_embedded == 0`.

Chunks are keyed by `(document, chunker_name, index)`, so ingesting the same
corpus under a second chunking strategy adds rows rather than replacing them,
and both strategies stay queryable for the Phase 3 comparison.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.pipeline import PipelineConfig
from app.ingestion.chunk import Chunker, build_chunker
from app.ingestion.embed import Embedder, get_embedder
from app.ingestion.fetch import DEFAULT_VERSIONS, fetch_versions, iter_markdown, latest_version
from app.ingestion.parse import ParsedDocument, parse_file
from app.ingestion.tokenizer import get_tokenizer
from app.models import Chunk, Document, IngestionRun

log = get_logger(__name__)


@dataclass
class IngestionStats:
    documents_added: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    documents_deleted: int = 0
    chunks_written: int = 0
    chunks_embedded: int = 0
    duration_seconds: float = 0.0
    versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _existing_hashes(session: Session, version: str) -> dict[str, tuple[int, str, bool]]:
    """`source_path -> (document id, content hash, is_tombstoned)` for one version."""
    rows = session.execute(
        select(
            Document.id,
            Document.source_path,
            Document.content_hash,
            Document.deleted_at,
        ).where(Document.version == version)
    ).all()
    return {row.source_path: (row.id, row.content_hash, row.deleted_at is not None) for row in rows}


def _upsert_document(session: Session, parsed: ParsedDocument, document_id: int | None) -> Document:
    if document_id is None:
        document = Document(
            source_path=parsed.source_path,
            version=parsed.version,
            title=parsed.title,
            url=parsed.url,
            content_hash=parsed.content_hash,
            is_latest_for_path=False,
            meta=parsed.meta,
        )
        session.add(document)
        session.flush()
        return document

    existing = session.get(Document, document_id)
    if existing is None:  # the row vanished between the scan and here
        raise RuntimeError(f"Document {document_id} disappeared mid-ingestion")
    existing.title = parsed.title
    existing.url = parsed.url
    existing.content_hash = parsed.content_hash
    existing.meta = parsed.meta
    existing.deleted_at = None
    session.flush()
    return existing


def _write_chunks(
    session: Session,
    document: Document,
    text: str,
    config: PipelineConfig,
    chunker: Chunker,
    embedder: Embedder,
) -> tuple[int, int]:
    """Replace this document's chunks for the active chunker. Returns (written, embedded)."""
    chunker_name = config.chunker_name

    # Delete and re-insert rather than diffing: chunk boundaries shift when a
    # document changes, so a positional diff would be wrong more often than right.
    session.execute(
        delete(Chunk).where(Chunk.document_id == document.id, Chunk.chunker_name == chunker_name)
    )

    pieces = chunker.chunk(text, title=document.title)
    if not pieces:
        return 0, 0

    texts = [p.embed_text(prepend_heading=config.chunking.prepend_heading_path) for p in pieces]
    vectors = embedder.embed_documents(texts)

    for piece, vector in zip(pieces, vectors, strict=True):
        session.add(
            Chunk(
                document_id=document.id,
                version=document.version,
                chunker_name=chunker_name,
                chunk_index=piece.index,
                text=piece.text,
                heading_path=piece.heading_path,
                chunk_type=piece.chunk_type,
                token_count=piece.token_count,
                embedding=vector,
                meta={"embedding_model": embedder.model_name, **piece.meta},
            )
        )

    return len(pieces), len(vectors)


def _mark_latest(session: Session, versions: list[str]) -> None:
    """Flag the newest indexed version of each page.

    Lets "answer from the latest docs" be an indexed lookup instead of a sort
    over every version of the path at query time.
    """
    newest = latest_version(versions)
    session.execute(update(Document).values(is_latest_for_path=False))
    session.execute(
        update(Document)
        .where(Document.version == newest, Document.deleted_at.is_(None))
        .values(is_latest_for_path=True)
    )
    # A page that exists in an older branch but was removed later is still the
    # latest copy *of that page*, so it stays answerable.
    stale = session.execute(
        select(Document.source_path).where(
            Document.version == newest, Document.deleted_at.is_(None)
        )
    ).scalars()
    covered = set(stale)
    others = session.execute(
        select(Document).where(Document.version != newest, Document.deleted_at.is_(None))
    ).scalars()
    best: dict[str, Document] = {}
    for doc in others:
        if doc.source_path in covered:
            continue
        current = best.get(doc.source_path)
        if current is None or latest_version([current.version, doc.version]) == doc.version:
            best[doc.source_path] = doc
    for doc in best.values():
        doc.is_latest_for_path = True


def ingest(
    session: Session,
    config: PipelineConfig,
    *,
    versions: list[str] | None = None,
    root: Path | None = None,
    fetch: bool = True,
    offline_tokenizer: bool = False,
    include: list[str] | None = None,
) -> IngestionStats:
    """Run one ingestion pass.

    `root` overrides the corpus location, which is how tests and CI ingest the
    committed fixture corpus instead of downloading the real one.

    `include` restricts ingestion to documents whose `source_path` starts with
    one of the given prefixes (e.g. `["concepts", "tasks"]`). Embedding the
    whole corpus on CPU takes hours, and a scoped-but-real subset keeps the
    evaluation loop usable. Whatever is ingested is recorded on the run, so a
    result can never quietly claim more coverage than it had.
    """
    started = time.perf_counter()
    target_versions = versions or list(DEFAULT_VERSIONS)
    stats = IngestionStats(versions=target_versions)

    tokenizer = get_tokenizer(config.embedding.model, offline=offline_tokenizer)
    chunker = build_chunker(config.chunking, tokenizer)
    embedder = get_embedder(config.embedding)

    if fetch and root is None:
        fetch_versions(target_versions)

    for version in target_versions:
        version_root = (root / version) if root else (_default_root() / version)
        if not version_root.exists():
            log.warning("ingest.missing_version_root", version=version, path=str(version_root))
            continue

        existing = _existing_hashes(session, version)
        seen: set[str] = set()

        for path in iter_markdown(version_root):
            parsed = parse_file(path, root=version_root, version=version)
            if parsed is None:
                continue
            if include and not any(parsed.source_path.startswith(p) for p in include):
                continue
            seen.add(parsed.source_path)

            record = existing.get(parsed.source_path)
            # A tombstoned document that reappears must be resurrected even
            # when its content is unchanged. Without this the fast path below
            # skips it forever and retrieval, which filters on
            # `deleted_at IS NULL`, never sees the page again.
            if record and record[2]:
                session.execute(
                    update(Document).where(Document.id == record[0]).values(deleted_at=None)
                )
                stats.documents_updated += 1
                continue

            if record and record[1] == parsed.content_hash:
                # Unchanged content, but the requested chunker may never have
                # run over it. Only skip when this chunking already exists.
                has_chunks = session.scalar(
                    select(Chunk.id)
                    .where(
                        Chunk.document_id == record[0],
                        Chunk.chunker_name == config.chunker_name,
                    )
                    .limit(1)
                )
                if has_chunks is not None:
                    stats.documents_unchanged += 1
                    continue

            document = _upsert_document(session, parsed, record[0] if record else None)
            written, embedded = _write_chunks(
                session, document, parsed.text, config, chunker, embedder
            )
            stats.chunks_written += written
            stats.chunks_embedded += embedded
            if record:
                stats.documents_updated += 1
            else:
                stats.documents_added += 1

        # Pages that vanished from the branch are tombstoned, not deleted, so
        # citations in older traces still resolve. With `include` in play, a
        # path outside the filter was never looked at and must not be treated
        # as deleted.
        candidates = (
            {p for p in existing if any(p.startswith(prefix) for prefix in include)}
            if include
            else set(existing)
        )
        missing = candidates - seen
        if missing:
            result: Any = session.execute(
                update(Document)
                .where(Document.version == version, Document.source_path.in_(missing))
                .where(Document.deleted_at.is_(None))
                .values(deleted_at=func.now())
            )
            stats.documents_deleted += result.rowcount or 0

        session.flush()

    _mark_latest(session, target_versions)
    stats.duration_seconds = time.perf_counter() - started

    session.add(
        IngestionRun(
            config_name=config.name,
            config_hash=config.config_hash,
            chunker_name=config.chunker_name,
            versions=target_versions,
            documents_added=stats.documents_added,
            documents_updated=stats.documents_updated,
            documents_unchanged=stats.documents_unchanged,
            documents_deleted=stats.documents_deleted,
            chunks_written=stats.chunks_written,
            chunks_embedded=stats.chunks_embedded,
            duration_seconds=stats.duration_seconds,
            meta={"embedding_model": config.embedding.model, "include": include or []},
        )
    )

    log.info("ingest.complete", **stats.to_dict())
    return stats


def _default_root() -> Path:
    from app.ingestion.fetch import RAW_DIR

    return RAW_DIR
