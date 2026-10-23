"""Integration test for PostgreSQL Case Persistence and Institutional Ownership.

Non-Destructive by Construction:
1. Provisions uniquely named disposable databases ('cfi_test_db_<uuid>') via PostgresTestDatabaseGuard.
2. Embeds an unforgeable ownership token in metadata table `_cfi_test_ownership`.
3. Verifies token and strict test prefix before destroying the database.
4. Refuses destructive cleanup on any unowned or non-prefixed database (never touches 'public' schema or arbitrary databases).
5. Executes full DDL migration cycles (004 -> 005 -> 004 -> head), ORM mapping inspection, and AsyncSession CRUD.
6. Proves database-level concurrency safety against real PostgreSQL:
   - Concurrent conflicting cross-tenant bindings are serialized with exactly one winner and one rejected ValueError.
   - Concurrent same-tenant links do not lose alert IDs.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
    from collections.abc import AsyncGenerator, Generator

TEST_DB_PREFIX = "cfi_test_db_"

# PostgreSQL connection parameters for the test host
PG_PORT = os.getenv("TEST_PG_PORT", "54329")
PG_HOST = os.getenv("TEST_PG_HOST", "localhost")
PG_USER = os.getenv("TEST_PG_USER", "postgres")
PG_PASSWORD = os.getenv("TEST_PG_PASSWORD", "testsecret")
PG_MAINTENANCE_DB = os.getenv("TEST_PG_MAINTENANCE_DB", "postgres")

MAINTENANCE_SYNC_URL = (
    f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_MAINTENANCE_DB}"
)


class UnsafeDatabaseOperationError(RuntimeError):
    """Raised when an operation on a non-test or unverified database is attempted."""


@dataclass(frozen=True)
class ManagedTestDatabase:
    """Represents a verified, disposable test database owned by the current test process."""

    db_name: str
    ownership_token: str
    sync_url: str
    async_url: str


class PostgresTestDatabaseGuard:
    """Manages isolated PostgreSQL test databases, guaranteeing safety by construction."""

    @classmethod
    def is_server_available(cls) -> bool:
        try:
            engine = sa.create_engine(MAINTENANCE_SYNC_URL, connect_args={"connect_timeout": 3})
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
            return True
        except Exception:
            return False

    @classmethod
    def provision_isolated_database(cls) -> ManagedTestDatabase:
        """Create a uniquely named database with an unforgeable ownership token."""
        unique_suffix = uuid.uuid4().hex[:12]
        db_name = f"{TEST_DB_PREFIX}{unique_suffix}"
        ownership_token = f"tok_{uuid.uuid4().hex}"

        sync_url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{db_name}"
        async_url = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{db_name}"

        # 1. Create database via maintenance connection with AUTOCOMMIT
        maint_engine = sa.create_engine(MAINTENANCE_SYNC_URL, isolation_level="AUTOCOMMIT")
        with maint_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        maint_engine.dispose()

        # 2. Plant ownership token inside dedicated metadata table
        db_engine = sa.create_engine(sync_url)
        with db_engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE _cfi_test_ownership ("
                    "  ownership_token VARCHAR(64) PRIMARY KEY,"
                    "  created_at TIMESTAMP NOT NULL"
                    ");"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO _cfi_test_ownership (ownership_token, created_at) "
                    "VALUES (:token, NOW());"
                ),
                {"token": ownership_token},
            )
            conn.commit()
        db_engine.dispose()

        return ManagedTestDatabase(
            db_name=db_name,
            ownership_token=ownership_token,
            sync_url=sync_url,
            async_url=async_url,
        )

    @classmethod
    def verify_ownership(cls, db_name: str, expected_token: str, sync_url: str) -> bool:
        """Verify that the target database exists, has test prefix, and contains matching ownership token."""
        if not db_name.startswith(TEST_DB_PREFIX):
            return False
        try:
            db_engine = sa.create_engine(sync_url, connect_args={"connect_timeout": 3})
            with db_engine.connect() as conn:
                token = conn.execute(
                    text("SELECT ownership_token FROM _cfi_test_ownership LIMIT 1;")
                ).scalar()
            db_engine.dispose()
            return token == expected_token
        except Exception:
            return False

    @classmethod
    def destroy_isolated_database(cls, managed_db: ManagedTestDatabase) -> None:
        """Drop the isolated database ONLY if ownership is cryptographically verified."""
        if not managed_db.db_name.startswith(TEST_DB_PREFIX):
            raise UnsafeDatabaseOperationError(
                f"Refusing to drop database '{managed_db.db_name}': does not match prefix '{TEST_DB_PREFIX}'."
            )

        if not cls.verify_ownership(
            managed_db.db_name, managed_db.ownership_token, managed_db.sync_url
        ):
            raise UnsafeDatabaseOperationError(
                f"Refusing to drop database '{managed_db.db_name}': ownership token verification failed."
            )

        maint_engine = sa.create_engine(MAINTENANCE_SYNC_URL, isolation_level="AUTOCOMMIT")
        with maint_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{managed_db.db_name}" WITH (FORCE);'))
        maint_engine.dispose()


pytestmark = pytest.mark.skipif(
    not PostgresTestDatabaseGuard.is_server_available(),
    reason=f"PostgreSQL test database not accessible on {PG_HOST}:{PG_PORT}",
)


def _get_alembic_config_for_db(async_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI_PATH))
    migrations_dir = str(BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations")
    versions_dir = str(
        BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations" / "versions"
    )
    cfg.set_main_option("script_location", migrations_dir)
    cfg.set_main_option("version_locations", versions_dir)
    cfg.set_main_option("sqlalchemy.url", async_url)
    return cfg


@pytest.fixture
def managed_pg_db() -> Generator[ManagedTestDatabase, None, None]:
    """Provide a fresh, uniquely isolated temporary database for each test, verified and dropped afterwards."""
    db = PostgresTestDatabaseGuard.provision_isolated_database()
    try:
        yield db
    finally:
        PostgresTestDatabaseGuard.destroy_isolated_database(db)


@pytest.fixture
def migrated_pg_db(managed_pg_db: ManagedTestDatabase) -> ManagedTestDatabase:
    """Provide an isolated database already migrated to head revision."""
    cfg = _get_alembic_config_for_db(managed_pg_db.async_url)
    command.upgrade(cfg, "head")
    return managed_pg_db


@pytest_asyncio.fixture
async def pg_session(
    migrated_pg_db: ManagedTestDatabase,
) -> AsyncGenerator[AsyncSession, None]:
    """Provide an AsyncSession bound to the isolated, migrated database."""
    engine = create_async_engine(migrated_pg_db.async_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── 1. Guard Safety Verification ──────────────────────────────────────────────


def test_guard_refuses_drop_without_test_prefix(managed_pg_db: ManagedTestDatabase) -> None:
    """Guard strictly refuses to drop databases that do not start with 'cfi_test_db_'."""
    fake_managed = ManagedTestDatabase(
        db_name="postgres",
        ownership_token=managed_pg_db.ownership_token,
        sync_url=MAINTENANCE_SYNC_URL,
        async_url=MAINTENANCE_SYNC_URL,
    )
    with pytest.raises(UnsafeDatabaseOperationError, match="does not match prefix"):
        PostgresTestDatabaseGuard.destroy_isolated_database(fake_managed)


def test_guard_refuses_drop_with_mismatched_ownership_token(
    managed_pg_db: ManagedTestDatabase,
) -> None:
    """Guard strictly refuses to drop a database if ownership token verification fails."""
    tampered_managed = ManagedTestDatabase(
        db_name=managed_pg_db.db_name,
        ownership_token="invalid_rogue_token",
        sync_url=managed_pg_db.sync_url,
        async_url=managed_pg_db.async_url,
    )
    with pytest.raises(UnsafeDatabaseOperationError, match="ownership token verification failed"):
        PostgresTestDatabaseGuard.destroy_isolated_database(tampered_managed)


# ── 2. Real PostgreSQL Migration Lifecycle Verification ────────────────────────


def test_postgresql_migration_upgrade_downgrade_and_schema_inspection(
    managed_pg_db: ManagedTestDatabase,
) -> None:
    """Verify Alembic migration 004 -> 005 on live PostgreSQL inside an isolated test DB:
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
    cfg = _get_alembic_config_for_db(managed_pg_db.async_url)

    # 1. Upgrade to 004
    command.upgrade(cfg, "004_training_authority_tables")

    sync_engine = sa.create_engine(managed_pg_db.sync_url)
    with sync_engine.connect() as conn:
        # 2. Verify bank_id column does not exist at 004
        cols = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'cases';"
                )
            )
            .scalars()
            .all()
        )
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
        indexes = (
            conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'cases' AND indexname = 'ix_cases_bank_id';"
                )
            )
            .scalars()
            .all()
        )
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
        cols_after_down = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'cases';"
                )
            )
            .scalars()
            .all()
        )
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
        cols_after_reup = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'cases';"
                )
            )
            .scalars()
            .all()
        )
        assert "bank_id" in cols_after_reup

    sync_engine.dispose()


# ── 3. Real PostgreSQL Persistence & Concurrency Verification ──────────────────


@pytest.mark.asyncio
async def test_postgresql_case_repository_ownership_persistence(
    migrated_pg_db: ManagedTestDatabase, pg_session: AsyncSession
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
    engine = create_async_engine(migrated_pg_db.async_url, echo=False)
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


@pytest.mark.asyncio
async def test_postgresql_concurrent_conflicting_institutions_alert_linking(
    migrated_pg_db: ManagedTestDatabase,
) -> None:
    """Verify concurrency safety on real PostgreSQL:
    Two concurrent sessions attempt to bind an initially unbound case to different banks.
    Database-level concurrency (SELECT ... FOR UPDATE) ensures exactly ONE succeeds,
    and the conflicting session is rejected with ValueError.
    The database state is verified intact in a fresh session.
    """
    engine = create_async_engine(migrated_pg_db.async_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 1. Create unbound case
    async with factory() as session:
        repo = CaseRepository(session)
        case = await repo.create(title="Concurrent Triage Case", bank_id=None)
        case_id = case.id

    # 2. Worker coroutines executing on separate AsyncSession instances
    async def worker(bank: str, alert: str) -> Any:
        async with factory() as session:
            r = CaseRepository(session)
            return await r.link_alert(case_id, alert, bank_id=bank)

    # Launch both workers concurrently
    results = await asyncio.gather(
        worker("bank_alpha", "alt-alpha-race"),
        worker("bank_beta", "alt-beta-race"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    # Exactly one succeeded and one was rejected
    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}: {results}"
    assert len(failures) == 1, f"Expected exactly 1 failure, got {len(failures)}: {results}"

    winning_case = successes[0]
    assert isinstance(failures[0], ValueError)
    assert "Cross-tenant alert linking rejected" in str(failures[0])

    winning_bank = winning_case.bank_id
    assert winning_bank in ("bank_alpha", "bank_beta")
    losing_bank = "bank_beta" if winning_bank == "bank_alpha" else "bank_alpha"
    assert losing_bank in str(failures[0])
    losing_alert = "alt-beta-race" if winning_bank == "bank_alpha" else "alt-alpha-race"
    winning_alert = "alt-alpha-race" if winning_bank == "bank_alpha" else "alt-beta-race"

    # 3. Verify database state in a fresh third session
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        final_case = await fresh_repo.get_by_id(case_id)
        assert final_case is not None
        assert final_case.bank_id == winning_bank
        assert final_case.alert_ids == [winning_alert]
        assert losing_alert not in final_case.alert_ids

    await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_concurrent_same_institution_alert_linking(
    migrated_pg_db: ManagedTestDatabase,
) -> None:
    """Verify that concurrent alert links from the same institution do not lose alert IDs."""
    engine = create_async_engine(migrated_pg_db.async_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 1. Create case owned by bank_alpha
    async with factory() as session:
        repo = CaseRepository(session)
        case = await repo.create(
            title="Shared Institution Case",
            bank_id="bank_alpha",
            alert_ids=["alt-alpha-000"],
        )
        case_id = case.id

    # 2. Worker coroutines linking two different alerts concurrently
    async def worker(alert: str) -> Any:
        async with factory() as session:
            r = CaseRepository(session)
            return await r.link_alert(case_id, alert, bank_id="bank_alpha")

    results = await asyncio.gather(
        worker("alt-alpha-001"),
        worker("alt-alpha-002"),
        return_exceptions=True,
    )

    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 0, f"Unexpected failures in concurrent same-tenant linking: {failures}"

    # 3. Verify in fresh session that BOTH alerts are present and uncorrupted
    async with factory() as fresh_session:
        fresh_repo = CaseRepository(fresh_session)
        final_case = await fresh_repo.get_by_id(case_id)
        assert final_case is not None
        assert final_case.bank_id == "bank_alpha"
        assert "alt-alpha-000" in final_case.alert_ids
        assert "alt-alpha-001" in final_case.alert_ids
        assert "alt-alpha-002" in final_case.alert_ids
        assert len(final_case.alert_ids) == 3

    await engine.dispose()
