"""Unit tests for Phase 10: Alembic Database Migration Lifecycle & Schema Integrity.

Tests:
  1. test_alembic_migrations_upgrade_head_and_downgrade_base_cleanly
  2. test_alembic_schema_parity_zero_drift
  3. test_alembic_heads_is_single_linear_branch
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

import app.infrastructure.models  # noqa: F401
from alembic import command
from app.infrastructure.database import Base

if TYPE_CHECKING:
    from collections.abc import Generator

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI_PATH = str(BACKEND_DIR / "alembic.ini")
SCRIPT_LOCATION = str(BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations")


def _get_alembic_config(db_url: str | None = None) -> Config:
    cfg = Config(ALEMBIC_INI_PATH)
    cfg.set_main_option("script_location", SCRIPT_LOCATION)
    cfg.set_main_option("version_locations", str(BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations" / "versions"))
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def temp_alembic_db(tmp_path: os.PathLike) -> Generator[str, None, None]:
    """Provide an isolated SQLite database path for migration lifecycle testing."""
    db_file = os.path.join(str(tmp_path), "test_migration.db").replace("\\", "/")
    yield db_file
    try:
        if os.path.exists(db_file):
            os.remove(db_file)
    except OSError:
        pass


def test_alembic_heads_is_single_linear_branch() -> None:
    """Ensure there are no divergent branch heads in the migration directory."""
    cfg = _get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 migration head, found {len(heads)}: {heads}"
    assert heads[0] == "005_case_institutional_ownership"


def test_alembic_migrations_upgrade_head_and_downgrade_base_cleanly(temp_alembic_db: str) -> None:
    """Assert migrations apply cleanly forward, roll back to base, and re-apply without error."""
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")

    # 1. First forward upgrade to head
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables_after_upgrade = {r[0] for r in cur.fetchall()}
    conn.close()

    AUTHORITY_TABLES = {
        "cfi_scheduled_jobs",
        "cfi_started_jobs",
        "cfi_round_observations",
        "cfi_global_aggregations",
        "cfi_model_evaluations",
        "cfi_completed_jobs",
    }

    # Must contain alembic_version plus all domain and authority tables
    assert "alembic_version" in tables_after_upgrade
    for expected_table in Base.metadata.tables:
        assert expected_table in tables_after_upgrade, f"Missing table: {expected_table}"
    for auth_table in AUTHORITY_TABLES:
        assert auth_table in tables_after_upgrade, f"Missing authority table: {auth_table}"
    assert len(tables_after_upgrade) == len(Base.metadata.tables) + len(AUTHORITY_TABLES) + 1

    # 2. Rollback completely to base
    command.downgrade(cfg, "base")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables_after_downgrade = {r[0] for r in cur.fetchall()}
    conn.close()

    # All domain tables must be dropped cleanly, leaving only alembic_version
    remaining_domain_tables = tables_after_downgrade - {"alembic_version"}
    assert len(remaining_domain_tables) == 0, f"Leaked tables after downgrade: {remaining_domain_tables}"

    # 3. Re-apply upgrade to head
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables_after_reupgrade = {r[0] for r in cur.fetchall()}
    conn.close()

    assert len(tables_after_reupgrade) == len(Base.metadata.tables) + len(AUTHORITY_TABLES) + 1
    for expected_table in Base.metadata.tables:
        assert expected_table in tables_after_reupgrade
    for auth_table in AUTHORITY_TABLES:
        assert auth_table in tables_after_reupgrade


def get_authority_tables_metadata() -> sa.MetaData:
    """Authoritative SQLAlchemy MetaData matching migration 004_training_execution_authority_tables."""
    meta = sa.MetaData()
    # 1. cfi_scheduled_jobs
    sa.Table(
        "cfi_scheduled_jobs",
        meta,
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
    # 2. cfi_started_jobs
    sa.Table(
        "cfi_started_jobs",
        meta,
        sa.Column("job_id", sa.String(128), sa.ForeignKey("cfi_scheduled_jobs.job_id"), primary_key=True, nullable=False),
        sa.Column("training_run_id", sa.String(128), nullable=False),
        sa.Column("simulation_id", sa.String(128), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="STARTED"),
    )
    # 3. cfi_round_observations
    sa.Table(
        "cfi_round_observations",
        meta,
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
    # 4. cfi_global_aggregations
    sa.Table(
        "cfi_global_aggregations",
        meta,
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
    # 5. cfi_model_evaluations
    sa.Table(
        "cfi_model_evaluations",
        meta,
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
    # 6. cfi_completed_jobs
    sa.Table(
        "cfi_completed_jobs",
        meta,
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
    return meta


def verify_authority_tables_detailed_schema(engine: Any) -> list[str]:
    """Inspect database schema and compare each of the 6 cfi_* authority tables against its migration definition.

    Checks:
    - Table presence
    - Column presence, types, nullability, primary key constraints
    - Foreign key constraints referencing cfi_scheduled_jobs
    - Unique constraints and unique indexes
    """
    inspector = sa.inspect(engine)
    existing_tables = set(inspector.get_table_names())
    auth_meta = get_authority_tables_metadata()
    drift: list[str] = []

    for table_name, expected_table in auth_meta.tables.items():
        if table_name not in existing_tables:
            drift.append(f"Missing authority table: {table_name}")
            continue

        actual_columns = {col["name"]: col for col in inspector.get_columns(table_name)}
        expected_columns = {col.name: col for col in expected_table.columns}

        # Check for missing or extra columns
        missing_cols = set(expected_columns.keys()) - set(actual_columns.keys())
        extra_cols = set(actual_columns.keys()) - set(expected_columns.keys())
        if missing_cols:
            drift.append(f"{table_name} missing columns: {sorted(missing_cols)}")
        if extra_cols:
            drift.append(f"{table_name} extra unexpected columns: {sorted(extra_cols)}")

        for col_name, exp_col in expected_columns.items():
            if col_name not in actual_columns:
                continue
            act_col = actual_columns[col_name]

            # Nullability check
            if act_col["nullable"] != exp_col.nullable:
                drift.append(
                    f"{table_name}.{col_name} nullability mismatch: expected {exp_col.nullable}, found {act_col['nullable']}"
                )

            # Primary key check
            act_pk = bool(act_col.get("primary_key"))
            exp_pk = exp_col.primary_key
            if act_pk != exp_pk:
                drift.append(
                    f"{table_name}.{col_name} primary_key mismatch: expected {exp_pk}, found {act_pk}"
                )

        # Foreign keys check
        actual_fks = inspector.get_foreign_keys(table_name)
        exp_fks = expected_table.foreign_keys
        if exp_fks:
            for exp_fk in exp_fks:
                exp_target_table = exp_fk.column.table.name
                exp_target_col = exp_fk.column.name
                exp_source_col = exp_fk.parent.name
                matched = False
                for act_fk in actual_fks:
                    if (
                        act_fk.get("referred_table") == exp_target_table
                        and exp_source_col in act_fk.get("constrained_columns", [])
                        and exp_target_col in act_fk.get("referred_columns", [])
                    ):
                        matched = True
                        break
                if not matched:
                    drift.append(
                        f"{table_name}.{exp_source_col} missing foreign key referencing {exp_target_table}.{exp_target_col}"
                    )

        # Unique constraints / unique indexes check
        exp_unique_sets: list[set[str]] = []
        for uc in expected_table.constraints:
            if isinstance(uc, sa.UniqueConstraint):
                exp_unique_sets.append({c.name for c in uc.columns})
        for col in expected_table.columns:
            if col.unique:
                exp_unique_sets.append({col.name})

        actual_unique_sets: list[set[str]] = []
        for uc in inspector.get_unique_constraints(table_name):
            cols = set(uc.get("column_names", []))
            if cols:
                actual_unique_sets.append(cols)
        for idx in inspector.get_indexes(table_name):
            if idx.get("unique"):
                cols = set(idx.get("column_names", []))
                if cols:
                    actual_unique_sets.append(cols)

        for exp_set in exp_unique_sets:
            if exp_set not in actual_unique_sets:
                drift.append(
                    f"{table_name} missing unique constraint or index over columns {sorted(exp_set)}"
                )

    return drift


def test_alembic_schema_parity_zero_drift(temp_alembic_db: str) -> None:
    """Assert zero schema drift across all database tables without unverified exclusions:
    1. Base.metadata domain tables verified via autogenerate compare_metadata.
    2. Authority tables (cfi_*) verified via compare_metadata against authoritative authority metadata.
    3. Authority tables deep verified for columns, types, nullability, PKs, FKs, and unique constraints.
    """
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")
    command.upgrade(cfg, "head")

    sync_engine = create_engine(f"sqlite:///{temp_alembic_db}")
    try:
        with sync_engine.connect() as conn:
            def include_domain(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
                if type_ == "schema":
                    return True
                if type_ == "table":
                    return not (name and name.startswith("cfi_"))
                parent_table = parent_names.get("table_name")
                if parent_table:
                    return not parent_table.startswith("cfi_")
                return True

            def include_authority(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
                if type_ == "schema":
                    return True
                if type_ == "table":
                    return bool(name and name.startswith("cfi_"))
                parent_table = parent_names.get("table_name")
                if parent_table:
                    return parent_table.startswith("cfi_")
                return True

            # 1. Verify standard domain tables against Base.metadata
            ctx_domain = MigrationContext.configure(
                conn,
                opts={"include_name": include_domain},
            )
            diff_domain = compare_metadata(ctx_domain, Base.metadata)
            assert diff_domain == [], f"Detected schema drift between domain models and migrations: {diff_domain}"

            # 2. Verify authority tables against get_authority_tables_metadata() via compare_metadata
            auth_meta = get_authority_tables_metadata()
            ctx_auth = MigrationContext.configure(
                conn,
                opts={"include_name": include_authority},
            )
            diff_auth = compare_metadata(ctx_auth, auth_meta)
            assert diff_auth == [], f"Detected schema drift in authority tables compare_metadata: {diff_auth}"

        # 3. Explicit deep inspection of columns, types, nullability, PKs, FKs, and unique constraints
        drift = verify_authority_tables_detailed_schema(sync_engine)
        assert drift == [], f"Detected detailed schema drift in authority tables: {drift}"
    finally:
        sync_engine.dispose()


def test_alembic_authority_tables_detects_intentional_drift(temp_alembic_db: str) -> None:
    """Verify that intentional schema mutations in cfi_* authority tables are strictly detected."""
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")
    command.upgrade(cfg, "head")

    # 1. Baseline must be zero drift
    sync_engine = create_engine(f"sqlite:///{temp_alembic_db}")
    try:
        baseline_drift = verify_authority_tables_detailed_schema(sync_engine)
        assert baseline_drift == [], f"Baseline authority tables unexpectedly drifted: {baseline_drift}"

        # 2. Introduce column drift via sqlite ALTER TABLE
        with sync_engine.connect() as conn:
            conn.execute(sa.text("ALTER TABLE cfi_scheduled_jobs ADD COLUMN rogue_unauthorized_column VARCHAR(64)"))
            conn.commit()

        drift_after_extra_col = verify_authority_tables_detailed_schema(sync_engine)
        assert any(
            "cfi_scheduled_jobs extra unexpected columns" in d and "rogue_unauthorized_column" in d
            for d in drift_after_extra_col
        ), f"Expected rogue column detection, got: {drift_after_extra_col}"

        # 3. Introduce missing table drift
        with sync_engine.connect() as conn:
            conn.execute(sa.text("DROP TABLE cfi_completed_jobs"))
            conn.commit()

        drift_after_drop = verify_authority_tables_detailed_schema(sync_engine)
        assert any(
            "Missing authority table: cfi_completed_jobs" in d
            for d in drift_after_drop
        ), f"Expected missing table detection, got: {drift_after_drop}"
    finally:
        sync_engine.dispose()



def test_alembic_tenant_scoped_persistence_identity(temp_alembic_db: str) -> None:
    """Verify tenant-scoped persistence identity (INV-I / INV-03):
    1. Rejects duplicate (bank_id, transaction_id) within same tenant.
    2. Allows identical transaction_id across distinct tenants (bank_id).
    """
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    # 1. Insert alert for bank_alpha
    cur.execute(
        "INSERT INTO alerts (id, bank_id, transaction_id, risk_score, severity, status, reason_codes, confidence, involved_entity_ids, top_features, risk_factors, model_confidence, historical_evidence, triage_priority, triage_action, sla_minutes, triage_reasons, dedup_count) "
        "VALUES ('alt_01', 'bank_alpha', 'tx_shared_100', 850.0, 'high', 'new', '[]', 0.9, '[]', '[]', '[]', 0.9, '[]', 'p1_critical', 'queue_urgent', 60, '[]', 1);"
    )
    conn.commit()

    # 2. Cross-tenant coexistence: insert same transaction_id for bank_beta -> must succeed
    cur.execute(
        "INSERT INTO alerts (id, bank_id, transaction_id, risk_score, severity, status, reason_codes, confidence, involved_entity_ids, top_features, risk_factors, model_confidence, historical_evidence, triage_priority, triage_action, sla_minutes, triage_reasons, dedup_count) "
        "VALUES ('alt_02', 'bank_beta', 'tx_shared_100', 300.0, 'low', 'new', '[]', 0.8, '[]', '[]', '[]', 0.8, '[]', 'p3_medium', 'queue_standard', 1440, '[]', 1);"
    )
    conn.commit()

    # 3. Same-tenant duplicate rejection: insert duplicate (bank_alpha, tx_shared_100) -> must fail with IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            "INSERT INTO alerts (id, bank_id, transaction_id, risk_score, severity, status, reason_codes, confidence, involved_entity_ids, top_features, risk_factors, model_confidence, historical_evidence, triage_priority, triage_action, sla_minutes, triage_reasons, dedup_count) "
            "VALUES ('alt_03', 'bank_alpha', 'tx_shared_100', 900.0, 'critical', 'new', '[]', 0.95, '[]', '[]', '[]', 0.95, '[]', 'p1_critical', 'queue_urgent', 30, '[]', 1);"
        )
        conn.commit()

    conn.close()


def test_alembic_upgrade_from_pre_repair_002_revision(temp_alembic_db: str) -> None:
    """Verify that an existing database at pre-repair revision 002 upgrades cleanly
    to 003_alerts_unique_constraint and enforces the composite unique constraint:
    1. Upgrade to 002_core_and_aml_tables (simulating pre-repair state).
    2. Insert cross-bank alerts with shared transaction_id.
    3. Upgrade to head (003_alerts_unique_constraint).
    4. Verify existing records survive and duplicate rejection is enforced.
    5. Verify distinct-tenant insertion still succeeds.
    """
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")

    # 1. Simulate pre-repair existing database at 002_core_and_aml_tables
    command.upgrade(cfg, "002_core_and_aml_tables")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    # Insert initial valid data across two banks
    cur.execute(
        "INSERT INTO alerts (id, bank_id, transaction_id, risk_score, severity, status, reason_codes, confidence, involved_entity_ids, top_features, risk_factors, model_confidence, historical_evidence, triage_priority, triage_action, sla_minutes, triage_reasons, dedup_count) "
        "VALUES ('alt_01', 'bank_alpha', 'tx_shared_100', 850.0, 'high', 'new', '[]', 0.9, '[]', '[]', '[]', 0.9, '[]', 'p1_critical', 'queue_urgent', 60, '[]', 1);"
    )
    cur.execute(
        "INSERT INTO alerts (id, bank_id, transaction_id, risk_score, severity, status, reason_codes, confidence, involved_entity_ids, top_features, risk_factors, model_confidence, historical_evidence, triage_priority, triage_action, sla_minutes, triage_reasons, dedup_count) "
        "VALUES ('alt_02', 'bank_beta', 'tx_shared_100', 300.0, 'low', 'new', '[]', 0.8, '[]', '[]', '[]', 0.8, '[]', 'p3_medium', 'queue_standard', 1440, '[]', 1);"
    )
    conn.commit()
    conn.close()

    # 2. Forward upgrade to head (003_alerts_unique_constraint)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()

    # 3. Verify existing rows survived table alteration
    cur.execute("SELECT id, bank_id, transaction_id FROM alerts ORDER BY id;")
    rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0] == ("alt_01", "bank_alpha", "tx_shared_100")
    assert rows[1] == ("alt_02", "bank_beta", "tx_shared_100")

    # 4. Verify duplicate (bank_alpha, tx_shared_100) is rejected by newly added constraint
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute(
            "INSERT INTO alerts (id, bank_id, transaction_id, risk_score, severity, status, reason_codes, confidence, involved_entity_ids, top_features, risk_factors, model_confidence, historical_evidence, triage_priority, triage_action, sla_minutes, triage_reasons, dedup_count) "
            "VALUES ('alt_03', 'bank_alpha', 'tx_shared_100', 900.0, 'critical', 'new', '[]', 0.95, '[]', '[]', '[]', 0.95, '[]', 'p1_critical', 'queue_urgent', 30, '[]', 1);"
        )
        conn.commit()

    # 5. Verify distinct tenant (bank_gamma, tx_shared_100) is accepted
    cur.execute(
        "INSERT INTO alerts (id, bank_id, transaction_id, risk_score, severity, status, reason_codes, confidence, involved_entity_ids, top_features, risk_factors, model_confidence, historical_evidence, triage_priority, triage_action, sla_minutes, triage_reasons, dedup_count) "
        "VALUES ('alt_04', 'bank_gamma', 'tx_shared_100', 400.0, 'medium', 'new', '[]', 0.85, '[]', '[]', '[]', 0.85, '[]', 'p2_high', 'queue_standard', 720, '[]', 1);"
    )
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM alerts;")
    assert cur.fetchone()[0] == 3

    conn.close()


def test_alembic_upgrade_from_004_to_005_case_institutional_ownership(temp_alembic_db: str) -> None:
    """Verify that an existing database at revision 004 upgrades cleanly to 005:
    1. Upgrade to 004_training_authority_tables.
    2. Insert a legacy case without bank_id (since cases has no bank_id at 004).
    3. Upgrade to 005_case_institutional_ownership (head).
    4. Verify cases table has column 'bank_id' and index 'ix_cases_bank_id'.
    5. Verify existing legacy case survived with bank_id IS NULL.
    6. Verify inserting a new case with bank_id succeeds.
    7. Downgrade to 004_training_authority_tables: verify bank_id column is dropped cleanly and existing case survives.
    8. Re-upgrade to head: verify bank_id column is re-added cleanly.
    """
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")

    # 1. Upgrade to 004_training_authority_tables
    command.upgrade(cfg, "004_training_authority_tables")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()

    # Verify cases column list at 004 does NOT have bank_id
    cur.execute("PRAGMA table_info(cases);")
    cols_004 = {row[1] for row in cur.fetchall()}
    assert "bank_id" not in cols_004
    assert "title" in cols_004

    # 2. Insert a case into cases at 004
    cur.execute(
        "INSERT INTO cases (id, title, status, priority, alert_ids, evidence_ids, notes, timeline, total_risk_score) "
        "VALUES ('case_legacy_01', 'Legacy Case 01', 'open', 'p3_medium', '[]', '[]', '[]', '[]', 0.5);"
    )
    conn.commit()
    conn.close()

    # 3. Upgrade to head (005_case_institutional_ownership)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()

    # 4. Verify cases column list at 005 has bank_id
    cur.execute("PRAGMA table_info(cases);")
    cols_005 = {row[1] for row in cur.fetchall()}
    assert "bank_id" in cols_005

    # Verify index ix_cases_bank_id exists
    cur.execute("PRAGMA index_list(cases);")
    indexes_005 = {row[1] for row in cur.fetchall()}
    assert "ix_cases_bank_id" in indexes_005

    # 5. Verify existing case survived and bank_id is NULL
    cur.execute("SELECT id, title, bank_id FROM cases WHERE id = 'case_legacy_01';")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "case_legacy_01"
    assert row[1] == "Legacy Case 01"
    assert row[2] is None

    # 6. Verify inserting a new case with bank_id succeeds
    cur.execute(
        "INSERT INTO cases (id, bank_id, title, status, priority, alert_ids, evidence_ids, notes, timeline, total_risk_score) "
        "VALUES ('case_new_02', 'bank_alpha', 'Institutional Case', 'open', 'p2_high', '[]', '[]', '[]', '[]', 0.8);"
    )
    conn.commit()
    cur.execute("SELECT id, bank_id FROM cases WHERE id = 'case_new_02';")
    assert cur.fetchone() == ("case_new_02", "bank_alpha")
    conn.close()

    # 7. Downgrade to 004_training_authority_tables
    command.downgrade(cfg, "004_training_authority_tables")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(cases);")
    cols_downgrade = {row[1] for row in cur.fetchall()}
    assert "bank_id" not in cols_downgrade
    cur.execute("PRAGMA index_list(cases);")
    indexes_downgrade = {row[1] for row in cur.fetchall()}
    assert "ix_cases_bank_id" not in indexes_downgrade

    # Legacy record still intact
    cur.execute("SELECT id, title FROM cases WHERE id = 'case_legacy_01';")
    assert cur.fetchone() == ("case_legacy_01", "Legacy Case 01")
    conn.close()

    # 8. Re-upgrade to head
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(cases);")
    cols_reupgrade = {row[1] for row in cur.fetchall()}
    assert "bank_id" in cols_reupgrade
    conn.close()


def test_migration_manager_handles_legacy_unversioned_db_missing_bank_id(temp_alembic_db: str) -> None:
    """Verify that migration_manager._ensure_migrated_or_stamped detects an unversioned database
    lacking cases.bank_id, stamps 004_training_authority_tables, and upgrades to 005_case_institutional_ownership."""
    from app.infrastructure.database.migration_manager import upgrade_head

    # 1. Build database up to 004 using alembic, then DROP alembic_version to simulate an unversioned legacy database
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")
    command.upgrade(cfg, "004_training_authority_tables")

    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    cur.execute("DROP TABLE alembic_version;")
    conn.commit()

    # Confirm it has cases without bank_id and has cfi_scheduled_jobs
    cur.execute("PRAGMA table_info(cases);")
    assert "bank_id" not in {row[1] for row in cur.fetchall()}
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cfi_scheduled_jobs';")
    assert cur.fetchone() is not None
    conn.close()

    # 2. Invoke upgrade_head via migration manager
    upgrade_head(f"sqlite+aiosqlite:///{temp_alembic_db}")

    # 3. Verify alembic_version is at head (005_case_institutional_ownership) and cases has bank_id
    conn = sqlite3.connect(temp_alembic_db)
    cur = conn.cursor()
    cur.execute("SELECT version_num FROM alembic_version;")
    version_row = cur.fetchone()
    assert version_row is not None
    assert version_row[0] == "005_case_institutional_ownership"

    cur.execute("PRAGMA table_info(cases);")
    assert "bank_id" in {row[1] for row in cur.fetchall()}
    conn.close()


