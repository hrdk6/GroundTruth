"""Trace retention: `python -m app.tracing.retention --days 30`.

Every query writes a trace and a dozen or more spans, each with JSONB inputs
and outputs of up to 4,000 characters per field. On a public deployment that
grows without bound, and nothing ever read a month-old span. So traces older
than `GT_TRACE_RETENTION_DAYS` are deleted -- by the API every few hours, or by
hand with this module. Spans go with their trace (the foreign key cascades).

Feedback is kept: it is deliberately not a foreign key, so a vote outlives the
trace it was cast on, and the helpful/unhelpful record stays complete.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.models import Trace

log = get_logger(__name__)


def prune_traces(session: Session, *, older_than_days: int) -> int:
    """Delete traces that started more than `older_than_days` ago. Returns the count."""
    if older_than_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    result = session.execute(delete(Trace).where(Trace.started_at < cutoff))
    deleted = int(getattr(result, "rowcount", 0) or 0)
    if deleted:
        log.info("traces.pruned", deleted=deleted, older_than_days=older_than_days)
    return deleted


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=settings.gt_trace_retention_days,
        help="Delete traces older than this (default: GT_TRACE_RETENTION_DAYS)",
    )
    args = parser.parse_args(argv)
    configure_logging(settings.gt_log_level, settings.gt_log_format)

    if args.days <= 0:
        print("Retention is disabled (days <= 0); nothing deleted.")
        return 0
    with session_scope() as session:
        deleted = prune_traces(session, older_than_days=args.days)
    print(f"Deleted {deleted} trace(s) older than {args.days} day(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
