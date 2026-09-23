"""Prune chunk sets no config produces: `python -m app.ingestion.prune [--yes]`.

A chunk set's identity (`chunks.chunker_name`) changes whenever a chunker's
revision, sizes, or embedding model change, and ingestion writes the new set
beside the old one rather than over it. That is deliberate -- two chunkings
must coexist for an experiment to compare them -- but it means superseded sets
accumulate, and they are not harmless: every set shares one HNSW index, and
pgvector filters by `chunker_name` only *after* the index scan, so dead rows
crowd live ones out of each query's candidate list.

Without `--yes` this only reports. With it, sets no file in `configs/`
produces are deleted, and the table is vacuumed so the index drops them too.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, func, select, text

from app.core.db import get_engine, session_scope
from app.core.logging import configure_logging
from app.core.pipeline import list_configs, load_config
from app.core.settings import get_settings
from app.models import Chunk


def live_chunk_sets() -> dict[str, list[str]]:
    """`chunker_name -> [config names that produce it]` for every config."""
    live: dict[str, list[str]] = {}
    for name in list_configs():
        live.setdefault(load_config(name).chunker_name, []).append(name)
    return live


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Delete the orphaned sets")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.gt_log_level, settings.gt_log_format)
    live = live_chunk_sets()

    with session_scope() as session:
        counts = session.execute(
            select(Chunk.chunker_name, func.count()).group_by(Chunk.chunker_name)
        ).all()

        orphaned = []
        for chunker_name, count in sorted(counts):
            users = live.get(chunker_name)
            status = f"used by {', '.join(users)}" if users else "ORPHANED"
            print(f"  {chunker_name:<44} {count:>7} chunks  {status}")
            if not users:
                orphaned.append(chunker_name)

        if not orphaned:
            print("\nNothing to prune.")
            return 0
        if not args.yes:
            print(f"\n{len(orphaned)} orphaned set(s). Re-run with --yes to delete them.")
            return 0

        result = session.execute(delete(Chunk).where(Chunk.chunker_name.in_(orphaned)))
        print(f"\nDeleted {result.rowcount} chunks from {len(orphaned)} set(s).")  # type: ignore[attr-defined]

    # VACUUM cannot run inside a transaction.
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM ANALYZE chunks"))
    print("Vacuumed `chunks`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
