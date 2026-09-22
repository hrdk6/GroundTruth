"""Alembic environment.

The connection URL comes from `app.core.settings`, not `alembic.ini`, so the
app and the migrations can never disagree about which database they mean.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# `alembic` may be invoked from the repo root or from backend/; make `app`
# importable either way.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.settings import get_settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # pgvector and the FTS generated column are created by hand-written
            # migrations; autogenerate should not try to "helpfully" drop them.
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    # Alembic cannot reflect pgvector's HNSW indexes faithfully; skip them so a
    # stray autogenerate never proposes dropping an index we rely on.
    is_vector_index = type_ == "index" and name is not None and name.endswith("_hnsw")
    return not is_vector_index


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
