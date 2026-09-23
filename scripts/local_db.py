#!/usr/bin/env python
"""Start a local Postgres with pgvector, without Docker.

`pgserver` ships a real PostgreSQL 16 build with pgvector compiled in, as a
wheel. It runs as an ordinary user process with no container and no
virtualization, which makes it the escape hatch on machines where Docker cannot
start — including any Windows box with virtualization disabled in firmware.

Docker Compose remains the supported path (it is what CI and a fresh clone use).
This is for local development when that path is unavailable.

    python scripts/local_db.py start     # start it and write DATABASE_URL to .env
    python scripts/local_db.py uri       # print the connection URI
    python scripts/local_db.py stop      # shut it down
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "pgdata"
ENV_FILE = REPO_ROOT / ".env"


def _server(keep_running: bool = True):
    """Handle to the server for `data/pgdata`, starting it if needed.

    `cleanup_mode=None` is the important part: pgserver's default is to stop
    the server when the last handle is closed, which would shut the database
    down the moment this script exits. `stop` is an explicit action here.
    """
    try:
        import pgserver
    except ImportError:
        print(
            "pgserver is not installed. Run:  cd backend && uv sync --all-extras",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return pgserver.get_server(DATA_DIR, cleanup_mode=None if keep_running else "stop")


def to_sqlalchemy_url(uri: str) -> str:
    """pgserver hands back `postgresql://...`; SQLAlchemy needs the driver."""
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)


def write_env(url: str) -> None:
    """Set DATABASE_URL in .env, replacing any existing value."""
    line = f"DATABASE_URL={url}"
    if not ENV_FILE.exists():
        ENV_FILE.write_text(line + "\n", encoding="utf-8")
        return

    text = ENV_FILE.read_text(encoding="utf-8")
    if re.search(r"^DATABASE_URL=.*$", text, re.MULTILINE):
        text = re.sub(r"^DATABASE_URL=.*$", line, text, flags=re.MULTILINE)
    elif re.search(r"^#\s*DATABASE_URL=.*$", text, re.MULTILINE):
        text = re.sub(r"^#\s*DATABASE_URL=.*$", line, text, count=1, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "uri", "stop", "psql"], default="start", nargs="?")
    parser.add_argument("--sql", default="SELECT version();", help="SQL for the psql action")
    args = parser.parse_args(argv)

    if args.action == "stop":
        try:
            import pgserver

            pgserver.get_server(DATA_DIR, cleanup_mode="stop").cleanup()
            print("stopped")
        except Exception as exc:  # noqa: BLE001 - stopping an absent server is fine
            print(f"nothing to stop ({type(exc).__name__})")
        return 0

    server = _server()
    url = to_sqlalchemy_url(server.get_uri())

    if args.action == "uri":
        print(url)
        return 0

    if args.action == "psql":
        print(server.psql(args.sql))
        return 0

    # `start`
    server.psql("CREATE EXTENSION IF NOT EXISTS vector;")
    server.psql("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    write_env(url)
    print(f"Postgres is up.\n  {url}\n  DATABASE_URL written to .env")
    print("\nNext:  cd backend && uv run python -m alembic upgrade head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
