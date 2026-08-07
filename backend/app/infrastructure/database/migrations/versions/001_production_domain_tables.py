"""Production domain tables — Phase 35.

Creates the 6 core production persistence tables that replace
in-memory dict and Redis storage for all domain objects.

Revision ID: 001_production_domain_tables
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# Alembic revision identifiers
revision: str = "001_production_domain_tables"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create all production domain tables."""

    # ── federated_rounds ──────────────────────────────────────────────
    op.create_table(
        "federated_rounds",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("consortium_id", sa.String(36), nullable=False),
        sa.Column("round_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="collecting_gradients"),
        sa.Column("submitted_bank_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("quorum_required", sa.Integer, nullable=False, server_default="2"),
        sa.Column("global_model_id", sa.String(36), nullable=True),
        sa.Column("aggregation_strategy", sa.String(30), server_default="fedavg"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_federated_rounds_consortium_id", "federated_rounds", ["consortium_id"])

    # ── global_models ─────────────────────────────────────────────────
    op.create_table(
        "global_models",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("consortium_id", sa.String(36), nullable=False),
        sa.Column("round_id", sa.String(36), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="candidate"),
        sa.Column("auc", sa.Float, nullable=True),
        sa.Column("f1_score", sa.Float, nullable=True),
        sa.Column("weights_path", sa.String(512), nullable=True),
        sa.Column("architecture_meta", sa.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_global_models_consortium_id", "global_models", ["consortium_id"])
    op.create_index("ix_global_models_round_id", "global_models", ["round_id"])

    # ── gradient_submissions ──────────────────────────────────────────
    op.create_table(
        "gradient_submissions",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("round_id", sa.String(36), nullable=False),
        sa.Column("bank_id", sa.String(36), nullable=False),
        sa.Column("gradient_hash", sa.String(64), nullable=False),
        sa.Column("dp_epsilon_used", sa.Float, nullable=False),
        sa.Column("participant_count", sa.Integer, nullable=False),
        sa.Column("validation_status", sa.String(30), server_default="accepted"),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_gradient_submissions_round_id", "gradient_submissions", ["round_id"])
    op.create_index("ix_gradient_submissions_bank_id", "gradient_submissions", ["bank_id"])

    # ── consortium_members ────────────────────────────────────────────
    op.create_table(
        "consortium_members",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("consortium_id", sa.String(36), nullable=False),
        sa.Column("bank_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("role", sa.String(20), server_default="member"),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_consortium_members_consortium_id", "consortium_members", ["consortium_id"])
    op.create_index("ix_consortium_members_bank_id", "consortium_members", ["bank_id"])

    # ── tenant_configs ────────────────────────────────────────────────
    op.create_table(
        "tenant_configs",
        sa.Column("bank_id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("jurisdiction", sa.String(10), nullable=False),
        sa.Column("contact_email", sa.String(255), nullable=False),
        sa.Column("data_residency_region", sa.String(30), nullable=False),
        sa.Column("status", sa.String(25), nullable=False, server_default="pending_verification"),
        sa.Column("cert_fingerprint", sa.String(64), nullable=True),
        sa.Column("cert_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vault_key_path", sa.String(255), nullable=True),
        sa.Column("schema_provisioned", sa.Integer, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── audit_events ──────────────────────────────────────────────────
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_bank_id", sa.String(36), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("chain_hash", sa.String(64), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_actor_bank_id", "audit_events", ["actor_bank_id"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])


def downgrade() -> None:
    """Drop all production domain tables in reverse dependency order."""
    op.drop_table("audit_events")
    op.drop_table("tenant_configs")
    op.drop_table("consortium_members")
    op.drop_table("gradient_submissions")
    op.drop_table("global_models")
    op.drop_table("federated_rounds")
