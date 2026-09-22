"""Dev server entrypoint: `uv run python -m app.run`.

Exists for one reason: on Windows, uvicorn builds a `ProactorEventLoop`, and
async psycopg refuses to run on it --

    psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'
    to run in async mode.

Setting an event loop *policy* does not help: uvicorn passes an explicit
`loop_factory` to `asyncio.run`, which bypasses the policy entirely. The only
reliable fix is to own the call to `asyncio.run` ourselves, which is what this
module does.

In Docker (Linux) none of this applies and the image runs plain uvicorn. This
entrypoint is for running the backend natively on a Windows dev machine.
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from app.core.settings import get_settings


def main() -> None:
    settings = get_settings()
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level=settings.gt_log_level.lower(),
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        # SelectorEventLoop is what psycopg's async mode requires.
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        server.run()


if __name__ == "__main__":
    main()
