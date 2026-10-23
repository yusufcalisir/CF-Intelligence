"""Add institutional tenant ownership to cases table.

Adds nullable bank_id column and index to the cases table.
This enables:
  - Persistent institutional tenant ownership on investigation cases.
  - Multi-tenant case filtering and tenant-isolated case management.
  - Standalone cases without institutional owner (bank_id IS NULL).

Revision ID: 005_case_institutional_ownership
Revises: 004_training_authority_tables
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import context, op  # type: ignore[attr-defined]

# Alembic revision identifiers
revision: str = "005_case_institutional_ownership"
down_revision: str | None = "004_training_authority_tables"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _cases_table_at_revision_004() -> sa.Table:
    """Schema of ``cases`` as created prior to revision 005.

    Used only in offline (--sql) mode, where SQLite batch operations cannot
    reflect the table from a live database.
    """
    return sa.Table(
        "cases",
        sa.MetaData(),
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="p3_medium"),
        sa.Column("assigned_to", sa.String(100), nullable=True),
        sa.Column("alert_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("evidence_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("notes", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("timeline", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("total_risk_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def _copy_from_table() -> sa.Table | None:
    if context.is_offline_mode():
        return _cases_table_at_revision_004()
    return None


def upgrade() -> None:
    """Add bank_id column and ix_cases_bank_id index to cases table."""
    with op.batch_alter_table("cases", copy_from=_copy_from_table()) as batch_op:
        batch_op.add_column(sa.Column("bank_id", sa.String(36), nullable=True))
        batch_op.create_index("ix_cases_bank_id", ["bank_id"])


def downgrade() -> None:
    """Remove ix_cases_bank_id index and bank_id column from cases table."""
    with op.batch_alter_table("cases", copy_from=_copy_from_table()) as batch_op:
        batch_op.drop_index("ix_cases_bank_id")
        batch_op.drop_column("bank_id")
