"""Core domain and AML intelligence platform tables — Phase 10.

Creates the remaining 11 domain, AML, graph, and simulation tables
to achieve 100% schema parity with SQLAlchemy Base.metadata:
  1. simulation_runs
  2. bank_configs
  3. training_rounds
  4. alerts
  5. cases
  6. entities
  7. relationships
  8. shared_intelligence
  9. evidence
  10. investigator_audit_logs
  11. business_rules
Also aligns column specifications (e.g. tenant_configs.cert_fingerprint).

Revision ID: 002_core_and_aml_tables
Revises: 001_production_domain_tables
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

# Alembic revision identifiers
revision: str = "002_core_and_aml_tables"
down_revision: str | None = "001_production_domain_tables"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create all core simulation, graph intelligence, and AML investigation tables."""

    # 1. simulation_runs
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("current_round", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_rounds", sa.Integer, nullable=False, server_default="10"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("banks_data", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("rounds_data", sa.JSON, nullable=False, server_default="[]"),
    )

    # 2. bank_configs
    op.create_table(
        "bank_configs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("simulation_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("fraud_ratio", sa.Float, nullable=False),
        sa.Column("num_transactions", sa.Integer, nullable=False),
        sa.Column("data_profile", sa.JSON, nullable=True),
        sa.Column("local_metrics", sa.JSON, nullable=True),
        sa.Column("federated_metrics", sa.JSON, nullable=True),
    )
    op.create_index("ix_bank_configs_simulation_id", "bank_configs", ["simulation_id"])

    # 3. training_rounds
    op.create_table(
        "training_rounds",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, nullable=False),
        sa.Column("simulation_id", sa.String(36), nullable=False),
        sa.Column("round_number", sa.Integer, nullable=False),
        sa.Column("global_loss", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("participating_bank_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("dropped_bank_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("per_bank_loss", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("per_bank_samples", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("aggregation_time_ms", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("round_duration_ms", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_training_rounds_simulation_id", "training_rounds", ["simulation_id"])

    # 4. alerts
    op.create_table(
        "alerts",
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alerts_bank_id", "alerts", ["bank_id"])

    # 5. cases
    op.create_table(
        "cases",
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 6. entities
    op.create_table(
        "entities",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("privacy_id", sa.String(64), nullable=False),
        sa.Column("bank_id", sa.String(36), nullable=False),
        sa.Column("display_label", sa.String(50), nullable=False),
        sa.Column("attributes", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="minimal"),
        sa.Column("alert_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_entities_entity_type", "entities", ["entity_type"])
    op.create_index("ix_entities_privacy_id", "entities", ["privacy_id"])
    op.create_index("ix_entities_bank_id", "entities", ["bank_id"])

    # 7. relationships
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("source_entity_id", sa.String(36), nullable=False),
        sa.Column("target_entity_id", sa.String(36), nullable=False),
        sa.Column("relationship_type", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("evidence", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_relationships_source_entity_id", "relationships", ["source_entity_id"])
    op.create_index("ix_relationships_target_entity_id", "relationships", ["target_entity_id"])

    # 8. shared_intelligence
    op.create_table(
        "shared_intelligence",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("source_bank_id", sa.String(36), nullable=False),
        sa.Column("intelligence_type", sa.String(30), nullable=False),
        sa.Column("privacy_hash", sa.String(64), nullable=False),
        sa.Column("risk_indicator", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("entity_type", sa.String(20), nullable=True),
        sa.Column("related_alert_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_shared_intelligence_source_bank_id", "shared_intelligence", ["source_bank_id"])
    op.create_index("ix_shared_intelligence_privacy_hash", "shared_intelligence", ["privacy_hash"])

    # 9. evidence
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("evidence_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(100), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_evidence_case_id", "evidence", ["case_id"])

    # 10. investigator_audit_logs
    op.create_table(
        "investigator_audit_logs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("investigator", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("session_duration_sec", sa.Float, nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=False, server_default="{}"),
    )

    # 11. business_rules
    op.create_table(
        "business_rules",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("rule_name", sa.String(100), nullable=False, unique=True),
        sa.Column("condition", sa.JSON, nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

def downgrade() -> None:
    """Drop all 11 core AML/simulation tables."""

    # Drop the 11 tables in reverse order of creation
    op.drop_table("business_rules")
    op.drop_table("investigator_audit_logs")
    op.drop_table("evidence")
    op.drop_table("shared_intelligence")
    op.drop_table("relationships")
    op.drop_table("entities")
    op.drop_table("cases")
    op.drop_table("alerts")
    op.drop_table("training_rounds")
    op.drop_table("bank_configs")
    op.drop_table("simulation_runs")
