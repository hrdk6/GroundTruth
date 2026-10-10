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

**Freshness is per chunk set, not per document.** Each chunk records the
content hash it was cut from, and a document is skipped only when *this*
chunker's chunks were cut from its *current* content. Checking the document's
hash alone was wrong as soon as two chunkers existed: ingesting chunker A after
an edit updated the document's hash, so chunker B then saw "unchanged" and kept
chunks cut from the old text -- indefinitely, since nothing would ever look
changed again.

Two properties of *how* the work is done, both learned the hard way:

* **Embedding is batched across documents.** A page yields a handful of
  chunks, and encoding them page by page leaves most of every batch empty.
  Pending chunks are pooled and encoded together, `FLUSH_CHUNKS` at a time.
* **Every flush is a checkpoint.** A full-corpus run takes hours on CPU. Run as
  one transaction, a failure at hour two rolled back everything; committing
  after each flush means a re-run resumes, because the committed documents are
  now unchanged and are skipped. A commit only ever lands *after* a batch's
  chunks are written, so a crash can never leave a document with a new content
  hash and no chunks.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.pipeline import PipelineConfig
from app.ingestion.chunk import TextChunk, build_chunker
from app.ingestion.embed import Embedder, get_embedder
from app.ingestion.fetch import DEFAULT_VERSIONS, fetch_versions, iter_markdown
from app.ingestion.parse import ParsedDocument, parse_file
from app.ingestion.tokenizer import get_tokenizer
from app.models import Chunk, Document, IngestionRun

log = get_logger(__name__)

# Chunks pooled before one encoder call and one commit. Large enough that the
# encoder's length-sorted batches stay full, small enough that a crash loses
# only a few seconds of work.
FLUSH_CHUNKS = 512


@dataclass
class IngestionStats:
    documents_added: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    documents_deleted: int = 0
    documents_restored: int = 0
    # Files `parse_file` rejected: unreadable, or no text once markup is gone.
    documents_unparseable: int = 0
    chunks_written: int = 0
    chunks_embedded: int = 0
    duration_seconds: float = 0.0
    versions: list[str] = field(default_factory=list)
    # Seconds per stage: fetch, parse, chunk, embed, write, tombstone,
    # mark_latest. Embedding was assumed to dominate a full-corpus run; this
    # is what says so, per run.
    stage_seconds: dict[str, float] = field(default_factory=dict)

    def add_time(self, stage: str, seconds: float) -> None:
        self.stage_seconds[stage] = round(self.stage_seconds.get(stage, 0.0) + seconds, 3)

    @contextmanager
    def timed(self, stage: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add_time(stage, time.perf_counter() - started)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def version_key(version: str) -> tuple[int, ...]:
    """Numeric ordering, so '1.9' sorts before '1.10'."""
    return tuple(int(part) for part in version.split("."))


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


class _ChunkWriter:
    """Pools chunks across documents, then embeds, writes, and commits them."""

    def __init__(
        self, session: Session, config: PipelineConfig, embedder: Embedder, stats: IngestionStats
    ) -> None:
        self.session = session
        self.config = config
        self.embedder = embedder
        self.stats = stats
        self._pending: list[tuple[Document, list[TextChunk]]] = []
        self._pending_chunks = 0

    def replace(self, document: Document, pieces: list[TextChunk]) -> None:
        """Queue `pieces` as this document's chunk set for the active chunker."""
        # Delete and re-insert rather than diffing: chunk boundaries shift when
        # a document changes, so a positional diff would be wrong more often
        # than right. The delete shares the flush's transaction, so it is
        # never committed without its replacement.
        with self.stats.timed("write"):
            self.session.execute(
                delete(Chunk).where(
                    Chunk.document_id == document.id,
                    Chunk.chunker_name == self.config.chunker_name,
                )
            )
        if not pieces:
            return
        self._pending.append((document, pieces))
        self._pending_chunks += len(pieces)
        if self._pending_chunks >= FLUSH_CHUNKS:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return

        prepend = self.config.chunking.prepend_heading_path
        texts = [
            p.embed_text(prepend_heading=prepend) for _, pieces in self._pending for p in pieces
        ]
        embed_started = time.perf_counter()
        vectors = iter(self.embedder.embed_documents(texts))
        embed_seconds = time.perf_counter() - embed_started
        self.stats.add_time("embed", embed_seconds)

        write_started = time.perf_counter()
        for document, pieces in self._pending:
            for piece in pieces:
                self.session.add(
                    Chunk(
                        document_id=document.id,
                        version=document.version,
                        chunker_name=self.config.chunker_name,
                        chunk_index=piece.index,
                        text=piece.text,
                        heading_path=piece.heading_path,
                        chunk_type=piece.chunk_type,
                        token_count=piece.token_count,
                        embedding=next(vectors),
                        meta={
                            "embedding_model": self.embedder.model_name,
                            # What this chunk was cut from; see the module docstring.
                            "content_hash": document.content_hash,
                            **piece.meta,
                        },
                    )
                )

        self.stats.chunks_written += len(texts)
        self.stats.chunks_embedded += len(texts)
        self.session.flush()
        self.session.commit()  # checkpoint: see the module docstring
        self.stats.add_time("write", time.perf_counter() - write_started)
        log.info(
            "ingest.checkpoint",
            chunks=len(texts),
            documents=len(self._pending),
            embed_seconds=round(embed_seconds, 2),
            chunks_per_second=round(len(texts) / embed_seconds, 1) if embed_seconds else None,
        )
        self._pending, self._pending_chunks = [], 0


def _chunks_are_current(
    session: Session, document_id: int, config: PipelineConfig, content_hash: str
) -> bool:
    """Whether this chunker's chunks for the document were cut from `content_hash`."""
    return (
        session.scalar(
            select(Chunk.id)
            .where(
                Chunk.document_id == document_id,
                Chunk.chunker_name == config.chunker_name,
                Chunk.meta["content_hash"].astext == content_hash,
            )
            .limit(1)
        )
        is not None
    )


def _mark_latest(session: Session) -> None:
    """Flag the newest live copy of each page, across **every indexed version**.

    Lets "answer from the latest docs" be an indexed lookup instead of a sort
    over every version of the path at query time.

    This used to consider only the versions of the current run, so ingesting
    `--versions 1.26` into a database that also held 1.30 marked the 1.26
    copies as the latest ones. The newest copy of a page is a property of the
    whole index, not of whichever run happened last.

    A page that exists in an older branch but was removed later is still the
    latest copy *of that page*, so it stays flagged and answerable.
    """
    rows = session.execute(
        select(Document.id, Document.source_path, Document.version).where(
            Document.deleted_at.is_(None)
        )
    ).all()

    best: dict[str, tuple[tuple[int, ...], int]] = {}
    for row in rows:
        key = version_key(row.version)
        current = best.get(row.source_path)
        if current is None or key > current[0]:
            best[row.source_path] = (key, row.id)

    latest_ids = [document_id for _, document_id in best.values()]
    session.execute(update(Document).values(is_latest_for_path=False))
    for start in range(0, len(latest_ids), 1000):
        session.execute(
            update(Document)
            .where(Document.id.in_(latest_ids[start : start + 1000]))
            .values(is_latest_for_path=True)
        )


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
    writer = _ChunkWriter(session, config, embedder, stats)

    if fetch and root is None:
        with stats.timed("fetch"):
            fetch_versions(target_versions)

    for version in target_versions:
        version_root = (root / version) if root else (_default_root() / version)
        if not version_root.exists():
            log.warning("ingest.missing_version_root", version=version, path=str(version_root))
            continue

        existing = _existing_hashes(session, version)
        seen: set[str] = set()

        for path in iter_markdown(version_root):
            with stats.timed("parse"):
                parsed = parse_file(path, root=version_root, version=version)
            if parsed is None:
                stats.documents_unparseable += 1
                continue
            if include and not any(parsed.source_path.startswith(p) for p in include):
                continue
            seen.add(parsed.source_path)

            record = existing.get(parsed.source_path)
            # A tombstoned document that reappears must be resurrected even
            # when its content is unchanged. Without this the fast path below
            # skips it forever and retrieval, which filters on
            # `deleted_at IS NULL`, never sees the page again.
            restored = bool(record and record[2])
            if restored:
                assert record is not None
                session.execute(
                    update(Document).where(Document.id == record[0]).values(deleted_at=None)
                )
                stats.documents_restored += 1

            if record and _chunks_are_current(session, record[0], config, parsed.content_hash):
                if not restored:
                    stats.documents_unchanged += 1
                continue

            # New, changed, or this chunker's chunks were cut from other
            # content -- including a page another chunker already refreshed,
            # and a restored page whose content also changed.
            with stats.timed("write"):
                document = _upsert_document(session, parsed, record[0] if record else None)
            with stats.timed("chunk"):
                pieces = chunker.chunk(parsed.text, title=document.title)
            writer.replace(document, pieces)
            if record:
                stats.documents_updated += 1
            else:
                stats.documents_added += 1

        writer.flush()

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
            with stats.timed("tombstone"):
                result: Any = session.execute(
                    update(Document)
                    .where(Document.version == version, Document.source_path.in_(missing))
                    .where(Document.deleted_at.is_(None))
                    .values(deleted_at=func.now())
                )
            stats.documents_deleted += result.rowcount or 0

        session.flush()

    with stats.timed("mark_latest"):
        _mark_latest(session)
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
            meta={
                "embedding_model": config.embedding.model,
                "include": include or [],
                "documents_restored": stats.documents_restored,
                "documents_unparseable": stats.documents_unparseable,
                "stage_seconds": stats.stage_seconds,
            },
        )
    )

    log.info("ingest.complete", **stats.to_dict())
    return stats


def _default_root() -> Path:
    from app.ingestion.fetch import RAW_DIR

    return RAW_DIR
