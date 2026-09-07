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
    assert heads[0] == "002_core_and_aml_tables"


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
