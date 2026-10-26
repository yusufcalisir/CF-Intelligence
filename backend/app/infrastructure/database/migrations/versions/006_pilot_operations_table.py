"""Add pilot_operations table for controlled two-bank pilot operations.

Revision ID: 006_pilot_operations_table
Revises: 005_case_institutional_ownership
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

# Alembic revision identifiers
revision: str = "006_pilot_operations_table"
down_revision: str | None = "005_case_institutional_ownership"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create pilot_operations table."""
    op.create_table(
        "pilot_operations",
        sa.Column("pilot_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consortium_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("queries_executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries_denied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_pilot_operations_consortium_id", "pilot_operations", ["consortium_id"])
    op.create_index("ix_pilot_operations_status", "pilot_operations", ["status"])


def downgrade() -> None:
    """Drop pilot_operations table."""
    op.drop_index("ix_pilot_operations_status", table_name="pilot_operations")
    op.drop_index("ix_pilot_operations_consortium_id", table_name="pilot_operations")
    op.drop_table("pilot_operations")
