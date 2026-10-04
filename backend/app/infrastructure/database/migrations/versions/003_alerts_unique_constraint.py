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
from alembic import op  # type: ignore[attr-defined]

# Alembic revision identifiers
revision: str = "003_alerts_unique_constraint"
down_revision: str | None = "002_core_and_aml_tables"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add uq_alerts_bank_transaction composite unique constraint."""
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.create_unique_constraint(
            "uq_alerts_bank_transaction",
            ["bank_id", "transaction_id"],
        )


def downgrade() -> None:
    """Remove uq_alerts_bank_transaction composite unique constraint."""
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_constraint("uq_alerts_bank_transaction", type_="unique")
