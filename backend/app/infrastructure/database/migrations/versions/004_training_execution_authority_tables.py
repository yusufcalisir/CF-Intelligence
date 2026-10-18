"""Training Execution Authority Tables — Phase 45.

Creates the 6 core persistence tables for the Training Job Authority:
- cfi_scheduled_jobs
- cfi_started_jobs
- cfi_round_observations
- cfi_global_aggregations
- cfi_model_evaluations
- cfi_completed_jobs

Revision ID: 004_training_authority_tables
Revises: 003_alerts_unique_constraint
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

# Alembic revision identifiers
revision: str = "004_training_authority_tables"
down_revision: str | None = "003_alerts_unique_constraint"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create all training job authority tables."""
    # ── cfi_scheduled_jobs ─────────────────────────────────────────────
    op.create_table(
        "cfi_scheduled_jobs",
        sa.Column("job_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("training_run_id", sa.String(128), unique=True, nullable=False),
        sa.Column("simulation_id", sa.String(128), nullable=False),
        sa.Column("coordinator_id", sa.String(128), nullable=False),
        sa.Column("architecture", sa.String(128), nullable=False),
        sa.Column("feature_schema_version", sa.String(64), nullable=False),
        sa.Column("expected_rounds", sa.Integer, nullable=False),
        sa.Column("expected_epochs", sa.Integer, nullable=False),
        sa.Column("input_dim", sa.Integer, nullable=False),
        sa.Column("feature_names_json", sa.Text, nullable=False),
        sa.Column("permitted_workers_json", sa.Text, nullable=True),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("scheduled_at", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="SCHEDULED"),
    )

    # ── cfi_started_jobs ───────────────────────────────────────────────
    op.create_table(
        "cfi_started_jobs",
        sa.Column("job_id", sa.String(128), sa.ForeignKey("cfi_scheduled_jobs.job_id"), primary_key=True, nullable=False),
        sa.Column("training_run_id", sa.String(128), nullable=False),
        sa.Column("simulation_id", sa.String(128), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="STARTED"),
    )

    # ── cfi_round_observations ─────────────────────────────────────────
    op.create_table(
        "cfi_round_observations",
        sa.Column("event_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("job_id", sa.String(128), sa.ForeignKey("cfi_scheduled_jobs.job_id"), nullable=False),
        sa.Column("training_run_id", sa.String(128), nullable=False),
        sa.Column("simulation_id", sa.String(128), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("bank_id", sa.String(128), nullable=False),
        sa.Column("round_number", sa.Integer, nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("actual_sample_count", sa.Integer, nullable=False),
        sa.Column("actual_executed_epochs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("observed_loss", sa.Float, nullable=True),
        sa.Column("metrics_json", sa.Text, nullable=False),
        sa.Column("model_update_digest", sa.String(64), nullable=True),
        sa.Column("execution_timestamp", sa.String(64), nullable=False),
        sa.Column("execution_status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("job_id", "bank_id", "round_number", "attempt_number", name="uq_cfi_round_obs"),
    )

    # ── cfi_global_aggregations ────────────────────────────────────────
    op.create_table(
        "cfi_global_aggregations",
        sa.Column("event_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("job_id", sa.String(128), sa.ForeignKey("cfi_scheduled_jobs.job_id"), nullable=False),
        sa.Column("round_number", sa.Integer, nullable=False),
        sa.Column("aggregation_algorithm", sa.String(64), nullable=False),
        sa.Column("accepted_participants_json", sa.Text, nullable=False),
        sa.Column("accepted_digests_json", sa.Text, nullable=False),
        sa.Column("aggregation_config_json", sa.Text, nullable=False),
        sa.Column("aggregated_model_digest", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.String(64), nullable=False),
        sa.Column("aggregation_status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("job_id", "round_number", name="uq_cfi_global_agg"),
    )

    # ── cfi_model_evaluations ──────────────────────────────────────────
    op.create_table(
        "cfi_model_evaluations",
        sa.Column("event_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("job_id", sa.String(128), sa.ForeignKey("cfi_scheduled_jobs.job_id"), nullable=False),
        sa.Column("evaluation_run_id", sa.String(128), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Float, nullable=False),
        sa.Column("evaluated_artifact_digest", sa.String(64), nullable=False),
        sa.Column("evaluator_identity", sa.String(128), nullable=False),
        sa.Column("evaluation_timestamp", sa.String(64), nullable=False),
        sa.Column("dataset_manifest_id", sa.Text, nullable=True),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("job_id", "metric_name", "evaluated_artifact_digest", name="uq_cfi_model_eval"),
    )

    # ── cfi_completed_jobs ─────────────────────────────────────────────
    op.create_table(
        "cfi_completed_jobs",
        sa.Column("job_id", sa.String(128), sa.ForeignKey("cfi_scheduled_jobs.job_id"), primary_key=True, nullable=False),
        sa.Column("training_run_id", sa.String(128), unique=True, nullable=False),
        sa.Column("simulation_id", sa.String(128), nullable=False),
        sa.Column("completed_at", sa.String(64), nullable=False),
        sa.Column("executed_rounds", sa.Integer, nullable=False),
        sa.Column("executed_epochs", sa.Integer, nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column("observed_metrics_json", sa.Text, nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("architecture", sa.String(128), nullable=False),
        sa.Column("feature_schema_version", sa.String(64), nullable=False),
        sa.Column("input_dim", sa.Integer, nullable=False),
        sa.Column("feature_names_json", sa.Text, nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="COMPLETED"),
        sa.Column("git_commit_hash", sa.String(128), nullable=True),
        sa.Column("dataset_manifest_id", sa.Text, nullable=True),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    """Drop all training job authority tables."""
    op.drop_table("cfi_completed_jobs")
    op.drop_table("cfi_model_evaluations")
    op.drop_table("cfi_global_aggregations")
    op.drop_table("cfi_round_observations")
    op.drop_table("cfi_started_jobs")
    op.drop_table("cfi_scheduled_jobs")
