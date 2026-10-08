"""Enforce tenant-scoped persistence identity on alerts table.

Adds composite unique constraint (bank_id, transaction_id) to the alerts table.
This guarantees tenant-isolated uniqueness:
  - Rejects duplicate alerts for the same transaction within a single bank tenant.
  - Permits distinct bank tenants to process and alert on the same transaction_id.

Revision ID: 003_alerts_unique_constraint
Revises: 002_core_and_aml_tables
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import context, op  # type: ignore[attr-defined]

# Alembic revision identifiers
revision: str = "003_alerts_unique_constraint"
down_revision: str | None = "002_core_and_aml_tables"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _alerts_table_at_revision_002() -> sa.Table:
    """Schema of ``alerts`` exactly as created by revision 002.

    Used only in offline (--sql) mode, where SQLite batch operations cannot
    reflect the table from a live database.
    """
    return sa.Table(
        "alerts",
        sa.MetaData(),
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("bank_id", sa.String(36), nullable=False),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("risk_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("reason_codes", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("involved_entity_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("top_features", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("risk_factors", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("model_confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("historical_evidence", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("triage_priority", sa.String(20), nullable=False, server_default="p3_medium"),
        sa.Column("triage_action", sa.String(32), nullable=False, server_default="queue_standard"),
        sa.Column("sla_minutes", sa.Integer, nullable=False, server_default="1440"),
        sa.Column("triage_reasons", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("dedup_key", sa.String(64), nullable=True),
        sa.Column("dedup_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Index("ix_alerts_bank_id", "bank_id"),
    )


def _copy_from_table() -> sa.Table | None:
    if context.is_offline_mode():
        return _alerts_table_at_revision_002()
    return None


def upgrade() -> None:
    """Add uq_alerts_bank_transaction composite unique constraint."""
    with op.batch_alter_table("alerts", copy_from=_copy_from_table()) as batch_op:
        batch_op.create_unique_constraint(
            "uq_alerts_bank_transaction",
            ["bank_id", "transaction_id"],
        )


def downgrade() -> None:
    """Remove uq_alerts_bank_transaction composite unique constraint."""
    with op.batch_alter_table("alerts", copy_from=_copy_from_table()) as batch_op:
        batch_op.drop_constraint("uq_alerts_bank_transaction", type_="unique")
