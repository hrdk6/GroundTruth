"""Enable the Postgres extensions the retrieval stack depends on.

`vector` powers dense retrieval (pgvector, HNSW index, cosine distance).
`pg_trgm` is not used by the default lexical path -- Postgres full-text search
covers that -- but it makes fuzzy `source_path` lookups cheap during ingestion
debugging, and enabling an extension later means another migration.

Revision ID: 0001
Revises:
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # Dropping `vector` would cascade into every embedding column. Intentionally
    # left in place: removing it is an operator decision, not a migration.
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
