"""Ingestion CLI: `python -m app.ingestion.run --config ../configs/baseline.yaml`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.core.pipeline import load_config
from app.core.settings import get_settings
from app.ingestion.fetch import DEFAULT_VERSIONS, fetch_versions
from app.ingestion.pipeline import ingest

log = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/baseline.yaml", help="Pipeline config")
    parser.add_argument(
        "--versions",
        nargs="*",
        default=list(DEFAULT_VERSIONS),
        help=f"Corpus versions to ingest (default: {' '.join(DEFAULT_VERSIONS)})",
    )
    parser.add_argument("--root", type=Path, default=None, help="Corpus root (skips downloading)")
    parser.add_argument("--no-fetch", action="store_true", help="Use already-extracted files")
    parser.add_argument(
        "--include",
        nargs="*",
        default=None,
        help="Only ingest paths starting with these prefixes, e.g. concepts tasks",
    )
    parser.add_argument("--fetch-only", action="store_true", help="Download and extract, then stop")
    parser.add_argument("--force-fetch", action="store_true", help="Re-download the tarballs")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.gt_log_level, settings.gt_log_format)

    if args.fetch_only:
        results = fetch_versions(args.versions, force=args.force_fetch)
        for result in results:
            print(f"{result.version}: {result.file_count} files -> {result.root}")
        return 0

    config = load_config(args.config)
    print(f"config={config.name} hash={config.config_hash} chunker={config.chunker_name}")

    with session_scope() as session:
        stats = ingest(
            session,
            config,
            versions=args.versions,
            root=args.root,
            fetch=not args.no_fetch,
            include=args.include,
        )

    print(
        f"\ndocuments: +{stats.documents_added} added, ~{stats.documents_updated} updated, "
        f"={stats.documents_unchanged} unchanged, -{stats.documents_deleted} deleted"
    )
    print(f"chunks:    {stats.chunks_written} written, {stats.chunks_embedded} embedded")
    print(f"duration:  {stats.duration_seconds:.1f}s")

    if stats.documents_unchanged and not stats.chunks_embedded:
        print("\nNothing changed: no re-embedding was needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
