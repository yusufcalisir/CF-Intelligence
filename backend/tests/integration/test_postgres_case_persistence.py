"""Integration test for PostgreSQL Case Persistence and Institutional Ownership.

Verifies:
1. Alembic forward migration from 004_training_authority_tables to 005_case_institutional_ownership.
2. PostgreSQL information_schema confirmation of cases.bank_id column and ix_cases_bank_id index.
3. Legacy case preservation during upgrade (bank_id IS NULL).
4. CaseRepository CRUD with institutional ownership against live PostgreSQL.
5. Durable bank_id binding when linking alert to unbound case.
6. Fresh AsyncSession verification against PostgreSQL engine.
7. Cross-tenant link rejection (ValueError) with immutable database state.
8. Downgrade to 004 and re-upgrade to head on PostgreSQL.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infrastructure.database.migration_manager import (
    ALEMBIC_INI_PATH,
    BACKEND_DIR,
)
from app.infrastructure.repositories.case_repository import CaseRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# PostgreSQL connection parameters for the isolated test container
PG_PORT = os.getenv("TEST_PG_PORT", "54329")
PG_HOST = os.getenv("TEST_PG_HOST", "localhost")
PG_USER = os.getenv("TEST_PG_USER", "postgres")
PG_PASSWORD = os.getenv("TEST_PG_PASSWORD", "testsecret")
PG_DB = os.getenv("TEST_PG_DB", "cfi_test_db")

SYNC_PG_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
ASYNC_PG_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"


def _is_postgres_available() -> bool:
    try:
        engine = sa.create_engine(SYNC_PG_URL, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _is_postgres_available(),
    reason=f"PostgreSQL test database not accessible on {PG_HOST}:{PG_PORT}",
)


def _get_pg_alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI_PATH))
    migrations_dir = str(BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations")
    versions_dir = str(BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations" / "versions")
    cfg.set_main_option("script_location", migrations_dir)
    cfg.set_main_option("version_locations", versions_dir)
    cfg.set_main_option("sqlalchemy.url", ASYNC_PG_URL)
    return cfg


def _reset_pg_schema() -> None:
    """Drop and recreate public schema to ensure a completely clean test environment."""
    engine = sa.create_engine(SYNC_PG_URL)
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        conn.commit()
    engine.dispose()


@pytest.fixture
def clean_pg_db() -> None:
    _reset_pg_schema()
    yield
    _reset_pg_schema()


@pytest.fixture
def pg_migrated_head() -> None:
    _reset_pg_schema()
    cfg = _get_pg_alembic_config()
    command.upgrade(cfg, "head")
    yield
    _reset_pg_schema()


@pytest_asyncio.fixture
async def pg_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(ASYNC_PG_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def test_postgresql_migration_upgrade_downgrade_and_schema_inspection(clean_pg_db: None) -> None:
    """Verify Alembic migration 004 -> 005 on live PostgreSQL:
    1. Upgrade to 004_training_authority_tables.
    2. Check cases table: bank_id column does NOT exist.
    3. Insert a legacy case at revision 004.
    4. Upgrade to head (005_case_institutional_ownership).
    5. Inspect information_schema: bank_id exists, nullable, varchar(36).
    6. Inspect pg_indexes: ix_cases_bank_id exists.
    7. Legacy case survived with bank_id NULL.
    8. Downgrade to 004 drops column cleanly.
    9. Re-upgrade to head restores column.
    """
    cfg = _get_pg_alembic_config()

    # 1. Upgrade to 004
    command.upgrade(cfg, "004_training_authority_tables")

    sync_engine = sa.create_engine(SYNC_PG_URL)
    with sync_engine.connect() as conn:
        # 2. Verify bank_id column does not exist at 004
        cols = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'cases';"
            )
        ).scalars().all()
        assert "bank_id" not in cols
        assert "title" in cols

        # 3. Insert legacy case at 004
        conn.execute(
            text(
                "INSERT INTO cases (id, title, status, priority, alert_ids, evidence_ids, notes, timeline, total_risk_score) "
                "VALUES ('pg_case_legacy_01', 'Postgres Legacy Case', 'open', 'p3_medium', '[]'::json, '[]'::json, '[]'::json, '[]'::json, 0.4);"
            )
        )
        conn.commit()

    # 4. Upgrade to head (005)
    command.upgrade(cfg, "head")

    with sync_engine.connect() as conn:
        # 5. Verify bank_id column in information_schema
        col_info = conn.execute(
            text(
                "SELECT column_name, is_nullable, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = 'cases' AND column_name = 'bank_id';"
            )
        ).fetchone()
        assert col_info is not None
        assert col_info[0] == "bank_id"
        assert col_info[1] == "YES"
        assert col_info[2] == 36

        # 6. Verify ix_cases_bank_id index
        indexes = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'cases' AND indexname = 'ix_cases_bank_id';"
            )
        ).scalars().all()
        assert "ix_cases_bank_id" in indexes

        # 7. Verify legacy case survived with NULL bank_id
        legacy_row = conn.execute(
            text("SELECT id, title, bank_id FROM cases WHERE id = 'pg_case_legacy_01';")
        ).fetchone()
        assert legacy_row is not None
        assert legacy_row[0] == "pg_case_legacy_01"
        assert legacy_row[1] == "Postgres Legacy Case"
        assert legacy_row[2] is None

    # 8. Downgrade to 004
    command.downgrade(cfg, "004_training_authority_tables")

    with sync_engine.connect() as conn:
        cols_after_down = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'cases';"
            )
        ).scalars().all()
        assert "bank_id" not in cols_after_down

        # Legacy row still accessible
        row_after_down = conn.execute(
            text("SELECT id, title FROM cases WHERE id = 'pg_case_legacy_01';")
        ).fetchone()
        assert row_after_down is not None
        assert row_after_down[0] == "pg_case_legacy_01"

    # 9. Re-upgrade to head
    command.upgrade(cfg, "head")

    with sync_engine.connect() as conn:
        cols_after_reup = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'cases';"
            )
        ).scalars().all()
        assert "bank_id" in cols_after_reup

    sync_engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_case_repository_ownership_persistence(
    pg_migrated_head: None, pg_session: AsyncSession
) -> None:
    """Verify CaseRepository operations against real PostgreSQL database at head revision."""
    repo = CaseRepository(pg_session)

    # 1. Standalone unbound case
    unbound = await repo.create(title="Unbound Postgres Case")
    assert unbound.bank_id is None

    # 2. Case with authorized institutional owner
    alpha_case = await repo.create(
        title="Alpha Owned Case",
        bank_id="bank_alpha",
        priority="p1_critical",
        alert_ids=["alt-alpha-001"],
    )
    assert alpha_case.bank_id == "bank_alpha"

    # 3. Fresh session verification against PostgreSQL
    engine = create_async_engine(ASYNC_PG_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        reloaded_unbound = await fresh_repo.get_by_id(unbound.id)
        assert reloaded_unbound is not None
        assert reloaded_unbound.bank_id is None

        reloaded_alpha = await fresh_repo.get_by_id(alpha_case.id)
        assert reloaded_alpha is not None
        assert reloaded_alpha.bank_id == "bank_alpha"
        assert reloaded_alpha.alert_ids == ["alt-alpha-001"]

    # 4. Link alert to unbound case durably binds bank_id
    linked = await repo.link_alert(unbound.id, "alt-beta-001", bank_id="bank_beta")
    assert linked is not None
    assert linked.bank_id == "bank_beta"
    assert "alt-beta-001" in linked.alert_ids

    # Verify durability across fresh session
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        reloaded_linked = await fresh_repo.get_by_id(unbound.id)
        assert reloaded_linked is not None
        assert reloaded_linked.bank_id == "bank_beta"
        assert reloaded_linked.alert_ids == ["alt-beta-001"]

    # 5. Idempotent repeat link
    repeated = await repo.link_alert(unbound.id, "alt-beta-001", bank_id="bank_beta")
    assert repeated is not None
    assert repeated.alert_ids == ["alt-beta-001"]
    assert repeated.bank_id == "bank_beta"

    # 6. Cross-tenant link rejection
    with pytest.raises(ValueError, match="Cross-tenant alert linking rejected"):
        await repo.link_alert(alpha_case.id, "alt-gamma-999", bank_id="bank_gamma")

    # Verify stored state remains unchanged
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        reloaded_alpha = await fresh_repo.get_by_id(alpha_case.id)
        assert reloaded_alpha is not None
        assert reloaded_alpha.bank_id == "bank_alpha"
        assert reloaded_alpha.alert_ids == ["alt-alpha-001"]
        assert "alt-gamma-999" not in reloaded_alpha.alert_ids

    # 7. list_by_bank
    alpha_cases = await repo.list_by_bank("bank_alpha")
    assert len(alpha_cases) == 1
    assert alpha_cases[0].id == alpha_case.id

    beta_cases = await repo.list_by_bank("bank_beta")
    assert len(beta_cases) == 1
    assert beta_cases[0].id == unbound.id

    gamma_cases = await repo.list_by_bank("bank_gamma")
    assert len(gamma_cases) == 0

    await engine.dispose()
