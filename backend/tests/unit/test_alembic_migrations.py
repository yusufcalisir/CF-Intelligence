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
from typing import TYPE_CHECKING

import pytest
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
    assert heads[0] == "003_alerts_unique_constraint"


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

    # Must contain alembic_version plus all 17 domain tables
    assert "alembic_version" in tables_after_upgrade
    for expected_table in Base.metadata.tables:
        assert expected_table in tables_after_upgrade, f"Missing table: {expected_table}"
    assert len(tables_after_upgrade) == 18  # 17 domain tables + 1 alembic_version

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

    assert len(tables_after_reupgrade) == 18
    for expected_table in Base.metadata.tables:
        assert expected_table in tables_after_reupgrade


def test_alembic_schema_parity_zero_drift(temp_alembic_db: str) -> None:
    """Assert autogenerate comparison between migrated schema and Base.metadata is completely empty."""
    cfg = _get_alembic_config(f"sqlite+aiosqlite:///{temp_alembic_db}")
    command.upgrade(cfg, "head")

    sync_engine = create_engine(f"sqlite:///{temp_alembic_db}")
    try:
        with sync_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)
            assert diff == [], f"Detected schema drift between models and migrations: {diff}"
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

