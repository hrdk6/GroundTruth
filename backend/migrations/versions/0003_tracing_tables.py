"""Create traces, spans, and feedback.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("config_name", sa.String(length=128), nullable=False),
        sa.Column("config_hash", sa.String(length=32), nullable=False),
        sa.Column("version_used", sa.String(length=16), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_traces"),
    )
    op.create_index("ix_traces_started_at", "traces", ["started_at"])

    op.create_table(
        "spans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("parent_span_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["traces.id"], name="fk_spans_trace_id_traces", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_spans"),
    )
    op.create_index("ix_spans_trace", "spans", ["trace_id", "sequence"])
    op.create_index("ix_spans_name", "spans", ["name"])

    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
    )
    op.create_index("ix_feedback_trace", "feedback", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_trace", table_name="feedback")
    op.drop_table("feedback")
    op.drop_index("ix_spans_name", table_name="spans")
    op.drop_index("ix_spans_trace", table_name="spans")
    op.drop_table("spans")
    op.drop_index("ix_traces_started_at", table_name="traces")
    op.drop_table("traces")
