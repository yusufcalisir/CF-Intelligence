"""Hardening and resilience tests for Database Persistence, Alembic Migrations & Concurrency Locking.

Validates:
  1. AlertModel full schema persistence with triage and deduplication fields.
  2. Transaction retry loop on SQLSTATE 40001 (serialization_failure) with backoff.
  3. Transaction retry loop on SQLSTATE 40P01 (deadlock_detected).
  4. Transaction retry loop on SQLite "database is locked".
  5. Exhausted retries proper re-raise.
  6. Non-retryable errors (e.g. 23505 unique constraint) fail fast without retries.
  7. Migration manager configuration and single linear head verification.
  8. CacheService distributed locking mutual exclusion under concurrency.
  9. CacheService distributed lock timeout handling.
  10. TenantContextMiddleware contextvar propagation and cleanup.
  11. Multi-tenant database URL resolution for central and bank-isolated databases.
  12. Retryable DB error heuristic accuracy across database engines.
  13. SQLAlchemy connection pooling configuration and SQLite busy timeout connect_args.
  14. PyTorch memory cleanup safeguards across FL and GNN training loops.
  15. Enterprise stress test latency percentiles (p50, p95, p99) reporting.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from app.infrastructure.cache import CacheService
from app.infrastructure.database import (
    _resolve_database_url,
    active_tenant,
    is_retryable_db_error,
    run_cockroach_transaction,
)
from app.infrastructure.database.migration_manager import (
    get_alembic_config,
    get_current_head_revision,
)
from app.infrastructure.database.tenant_provisioner import sanitize_bank_id
from app.infrastructure.models import AlertModel

# ── 1. AlertModel Schema Parity & Persistence ──────────────────────────────────


def test_alert_model_schema_attributes() -> None:
    """Verifies that AlertModel includes all triage and deduplication attributes matching migration 002."""
    alert = AlertModel(
        id="alert_hardening_001",
        bank_id="bank_alpha",
        transaction_id="tx_test_999",
        risk_score=0.92,
        severity="high",
        status="under_investigation",
        reason_codes=["LARGE_UNSTRUCTURED_TRANSFER", "SANCTION_PROXIMITY"],
        confidence=0.88,
        involved_entity_ids=["ent_1", "ent_2"],
        top_features=["amount_std_dev", "cross_border_velocity"],
        risk_factors=["crypto_mixer_adjacent"],
        model_confidence=0.91,
        historical_evidence=["ev_001"],
        triage_priority="p1_critical",
        triage_action="escalate_immediate",
        sla_minutes=60,
        triage_reasons=["amount_exceeds_threshold", "sanctions_match"],
        dedup_key="dedup_hash_alpha_999",
        dedup_count=3,
    )

    assert alert.id == "alert_hardening_001"
    assert alert.triage_priority == "p1_critical"
    assert alert.triage_action == "escalate_immediate"
    assert alert.sla_minutes == 60
    assert len(alert.triage_reasons) == 2
    assert alert.dedup_key == "dedup_hash_alpha_999"
    assert alert.dedup_count == 3


# ── 2. Concurrency Conflict Retries in run_cockroach_transaction ───────────────


class _MockSessionCtx:
    """Mock async context manager mimicking AsyncSession and session.begin()."""

    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self) -> _MockSessionCtx:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.closed = True

    def begin(self) -> _MockSessionCtx:
        return self


@pytest.mark.asyncio
async def test_cockroach_transaction_serialization_failure_retry() -> None:
    """Verifies that SQLSTATE 40001 triggers exponential backoff retry and succeeds."""
    attempts = 0

    def session_factory() -> _MockSessionCtx:
        return _MockSessionCtx()

    async def flaky_operation(session: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            orig_err = MagicMock()
            orig_err.pgcode = "40001"
            raise DBAPIError("statement", {}, orig_err)
        return "recovered_result"

    result = await run_cockroach_transaction(
        session_factory,  # type: ignore[arg-type]
        flaky_operation,
        max_retries=3,
        base_backoff_sec=0.005,
    )

    assert result == "recovered_result"
    assert attempts == 2


@pytest.mark.asyncio
async def test_cockroach_transaction_deadlock_retry() -> None:
    """Verifies that SQLSTATE 40P01 (deadlock) triggers transparent retry and succeeds."""
    attempts = 0

    def session_factory() -> _MockSessionCtx:
        return _MockSessionCtx()

    async def deadlock_operation(session: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            orig_err = MagicMock()
            orig_err.pgcode = "40P01"
            raise DBAPIError("statement", {}, orig_err)
        return "deadlock_cleared"

    result = await run_cockroach_transaction(
        session_factory,  # type: ignore[arg-type]
        deadlock_operation,
        max_retries=3,
        base_backoff_sec=0.005,
    )

    assert result == "deadlock_cleared"
    assert attempts == 2


@pytest.mark.asyncio
async def test_cockroach_transaction_sqlite_locked_retry() -> None:
    """Verifies that SQLite 'database is locked' errors are retried."""
    attempts = 0

    def session_factory() -> _MockSessionCtx:
        return _MockSessionCtx()

    async def locked_operation(session: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            orig_err = Exception("operational error: database is locked")
            raise DBAPIError("SELECT 1", {}, orig_err)
        return "unlocked"

    result = await run_cockroach_transaction(
        session_factory,  # type: ignore[arg-type]
        locked_operation,
        max_retries=4,
        base_backoff_sec=0.005,
    )

    assert result == "unlocked"
    assert attempts == 3


@pytest.mark.asyncio
async def test_cockroach_transaction_exhausted_retries_raises() -> None:
    """Verifies that persistent retryable errors raise after max_retries."""
    attempts = 0

    def session_factory() -> _MockSessionCtx:
        return _MockSessionCtx()

    async def always_conflict(session: Any) -> str:
        nonlocal attempts
        attempts += 1
        orig_err = MagicMock()
        orig_err.pgcode = "40001"
        raise DBAPIError("statement", {}, orig_err)

    with pytest.raises(DBAPIError):
        await run_cockroach_transaction(
            session_factory,  # type: ignore[arg-type]
            always_conflict,
            max_retries=3,
            base_backoff_sec=0.005,
        )

    assert attempts == 3


@pytest.mark.asyncio
async def test_cockroach_transaction_non_retryable_error_fails_fast() -> None:
    """Verifies that non-retryable errors (e.g. unique violation) fail on attempt 1."""
    attempts = 0

    def session_factory() -> _MockSessionCtx:
        return _MockSessionCtx()

    async def constraint_violation(session: Any) -> str:
        nonlocal attempts
        attempts += 1
        orig_err = MagicMock()
        orig_err.pgcode = "23505"  # unique_violation
        raise DBAPIError("INSERT ...", {}, orig_err)

    with pytest.raises(DBAPIError):
        await run_cockroach_transaction(
            session_factory,  # type: ignore[arg-type]
            constraint_violation,
            max_retries=5,
            base_backoff_sec=0.005,
        )

    assert attempts == 1


# ── 3. Heuristic Error Classifier ──────────────────────────────────────────────


def test_is_retryable_db_error_heuristics() -> None:
    """Verifies classification of retryable vs fatal database errors."""
    # 40001 serialization failure
    err_40001 = DBAPIError("stmt", {}, MagicMock(pgcode="40001"))
    assert is_retryable_db_error(err_40001) is True

    # 40P01 deadlock detected
    err_40p01 = DBAPIError("stmt", {}, MagicMock(pgcode="40P01"))
    assert is_retryable_db_error(err_40p01) is True

    # SQLite database is locked
    err_sqlite_locked = DBAPIError("stmt", {}, Exception("database is locked"))
    assert is_retryable_db_error(err_sqlite_locked) is True

    # Generic string-based exception containing deadlock
    err_generic = Exception("Transaction aborted due to deadlock")
    assert is_retryable_db_error(err_generic) is True

    # Non-retryable: Unique constraint violation
    err_unique = DBAPIError("stmt", {}, MagicMock(pgcode="23505"))
    assert is_retryable_db_error(err_unique) is False

    # Non-retryable: Syntax error
    err_syntax = DBAPIError("stmt", {}, MagicMock(pgcode="42601"))
    assert is_retryable_db_error(err_syntax) is False


# ── 4. Migration Manager Head Resolution ───────────────────────────────────────


def test_migration_manager_configuration_and_head() -> None:
    """Verifies that get_alembic_config points to valid paths and single head."""
    cfg = get_alembic_config()
    assert cfg.get_main_option("script_location") is not None
    assert cfg.get_main_option("version_locations") is not None

    heads = get_current_head_revision()
    assert len(heads) == 1
    assert heads[0] == "006_pilot_operations_table"


# ── 5. Distributed Locking in CacheService ─────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_service_distributed_lock_in_memory_fallback() -> None:
    """Verifies that in-memory fallback enforces mutual exclusion when Redis is unavailable."""
    cache = CacheService.get()
    cache.__class__._unavailable = True
    cache.__class__._client = None
    resource = "test_fl_round_aggregation"
    execution_order: list[int] = []
    concurrency_count = 0
    max_observed_concurrency = 0

    async def worker(worker_id: int) -> None:
        nonlocal concurrency_count, max_observed_concurrency
        async with cache.distributed_lock(resource, ttl_seconds=5, timeout_seconds=2.0) as acquired:
            assert acquired is True, f"Worker {worker_id} failed to acquire lock"
            concurrency_count += 1
            if concurrency_count > max_observed_concurrency:
                max_observed_concurrency = concurrency_count
            await asyncio.sleep(0.02)
            execution_order.append(worker_id)
            concurrency_count -= 1

    # Run 4 workers concurrently trying to enter the critical section
    await asyncio.gather(worker(1), worker(2), worker(3), worker(4))

    assert len(execution_order) == 4
    assert max_observed_concurrency == 1, (
        f"Mutual exclusion violated: max concurrency was {max_observed_concurrency}"
    )


@pytest.mark.asyncio
async def test_cache_service_distributed_lock_redis_client() -> None:
    """Verifies that distributed_lock acquires via Redis SET(nx=True) and unlocks via Lua script."""
    cache = CacheService.get()
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=1)

    cache.__class__._unavailable = False
    cache.__class__._client = mock_redis

    async with cache.distributed_lock("fl_global_round_1", ttl_seconds=10, timeout_seconds=1.0) as acquired:
        assert acquired is True
        mock_redis.set.assert_awaited_once()
        assert mock_redis.set.await_args is not None
        args, kwargs = mock_redis.set.await_args
        assert args[0] == "lock:fl_global_round_1"
        assert kwargs["nx"] is True
        assert kwargs["ex"] == 10

    # Verify Lua unlock was evaluated with matching key
    mock_redis.eval.assert_awaited_once()
    assert mock_redis.eval.await_args is not None
    eval_args, _ = mock_redis.eval.await_args
    assert "redis.call" in eval_args[0]
    assert eval_args[2] == "lock:fl_global_round_1"


@pytest.mark.asyncio
async def test_cache_service_distributed_lock_timeout_handling() -> None:
    """Verifies that distributed_lock yields False when lock acquisition times out."""
    cache = CacheService.get()
    cache.__class__._unavailable = True
    cache.__class__._client = None
    resource = "test_lock_timeout_resource"

    async def holder(event: asyncio.Event) -> None:
        async with cache.distributed_lock(resource, ttl_seconds=5, timeout_seconds=1.0) as acquired:
            assert acquired is True
            event.set()
            await asyncio.sleep(0.15)

    ready_event = asyncio.Event()
    holder_task = asyncio.create_task(holder(ready_event))
    await ready_event.wait()

    # Second acquirer with very short timeout should fail to acquire
    async with cache.distributed_lock(resource, ttl_seconds=5, timeout_seconds=0.03) as second_acquired:
        assert second_acquired is False

    await holder_task


# ── 6. TenantContextMiddleware & URL Resolution ───────────────────────────────


@pytest.mark.asyncio
async def test_tenant_context_middleware_header_handling() -> None:
    """Verifies that TenantContextMiddleware propagates X-Bank-ID to active_tenant and cleans up."""
    from app.infrastructure.database import TenantContextMiddleware

    middleware = TenantContextMiddleware(app=MagicMock())

    observed_tenant: str | None = None

    async def mock_call_next(request: Any) -> Any:
        nonlocal observed_tenant
        observed_tenant = active_tenant.get()
        return "response_ok"

    # Request with X-Bank-ID
    req = MagicMock()
    req.headers = {"X-Bank-ID": "bank_beta"}

    resp = await middleware.dispatch(req, mock_call_next)
    assert resp == "response_ok"
    assert observed_tenant == "bank_beta"
    # Verify cleaned up after request
    assert active_tenant.get() is None


def test_database_url_resolution() -> None:
    """Verifies URL generation for central and isolated tenant databases."""
    # Central database
    central_url = _resolve_database_url(None)
    assert "cfi_central.db" in central_url

    # Tenant database
    bank_url = _resolve_database_url("bank_alpha")
    assert "cfi_bank_alpha.db" in bank_url

    # Sanitization check
    sanitized = sanitize_bank_id("BANK_GAMMA_01")
    assert sanitized == "bank_gamma_01"


# ── 7. Connection Pooling & Memory Cleanup Hardening ──────────────────────────


def test_database_connection_pooling_kwargs_and_sqlite_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies connection pool configuration for PostgreSQL/CockroachDB and SQLite busy timeout."""
    from app.config import get_settings
    from app.infrastructure.database import _make_engine_kwargs

    current_settings = get_settings()

    # Test SQLite branch
    monkeypatch.setattr(current_settings, "database_type", "sqlite")
    sqlite_kwargs = _make_engine_kwargs("bank_alpha")
    assert "connect_args" in sqlite_kwargs
    assert sqlite_kwargs["connect_args"]["timeout"] == 30.0
    assert sqlite_kwargs["connect_args"]["check_same_thread"] is False

    # Test PostgreSQL branch
    monkeypatch.setattr(current_settings, "database_type", "postgres")
    monkeypatch.setattr(current_settings, "database_pool_size", 25)
    monkeypatch.setattr(current_settings, "database_max_overflow", 15)
    monkeypatch.setattr(current_settings, "database_pool_timeout", 45.0)
    monkeypatch.setattr(current_settings, "database_pool_recycle", 1800)

    pg_kwargs = _make_engine_kwargs("bank_alpha")
    assert pg_kwargs["pool_size"] == 25
    assert pg_kwargs["max_overflow"] == 15
    assert pg_kwargs["pool_timeout"] == 45.0
    assert pg_kwargs["pool_recycle"] == 1800
    assert pg_kwargs["pool_pre_ping"] is True


def test_pytorch_memory_cleanup_safeguards() -> None:
    """Verifies that PyTorch GPU/CPU memory cleanup helpers run cleanly without exception."""
    from app.application.services.flower_engine import _cleanup_pytorch_memory as flower_cleanup
    from app.application.services.graph_embedding_service import (
        _cleanup_pytorch_memory as gnn_cleanup,
    )
    from app.application.services.simulation_service import _cleanup_pytorch_memory as sim_cleanup

    # All three cleanup functions must run safely regardless of CUDA availability
    flower_cleanup()
    gnn_cleanup()
    sim_cleanup()


def test_enterprise_stress_test_percentiles_calculation() -> None:
    """Verifies that EnterpriseStressTestRunner records and reports p50, p95, and p99 percentiles."""
    import sys
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent.parent.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    from scripts.run_enterprise_stress_test import (
        EnterpriseStressTestRunner,
        StressTestConfig,
        StressTestResult,
    )

    config = StressTestConfig(
        num_banks=3,
        transactions_per_second_target=2000,
        duration_seconds=1,
        batch_size=20,
    )
    runner = EnterpriseStressTestRunner(config)
    runner.prepare()
    result = runner.run()

    assert isinstance(result, StressTestResult)
    assert result.total_transactions > 0
    assert result.peak_tps > 0.0
    assert result.p50_latency_ms >= 0.0
    assert result.p95_latency_ms >= 0.0
    assert result.p99_latency_ms >= 0.0
    assert result.p99_latency_ms >= result.p50_latency_ms

    report_md = runner.report(result)
    assert "p50 (Median)" in report_md
    assert "p95" in report_md
    assert "p99" in report_md
    assert "Conformance Verdict" in report_md

    as_dict = result.to_dict()
    assert "p50_latency_ms" in as_dict
    assert "p95_latency_ms" in as_dict
    assert "p99_latency_ms" in as_dict


# ── 8. Database Configuration Consistency & Tenant Isolation Hardening ────────


@pytest.mark.asyncio
async def test_database_configuration_change_rejects_silent_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that changing database configuration without explicit disposal raises RuntimeError."""
    from app.config import get_settings
    from app.infrastructure.database import (
        _get_or_create_engine,
        dispose_all_engines,
    )

    await dispose_all_engines()
    try:
        # Create initial engine for bank_test
        eng1 = _get_or_create_engine("bank_test_cfg")
        assert eng1 is not None

        # Re-fetching with same configuration succeeds and reuses engine
        eng1_reused = _get_or_create_engine("bank_test_cfg")
        assert eng1_reused is eng1

        # Simulate dynamic settings change (switching database_type to postgres with custom pool size)
        current_settings = get_settings()
        monkeypatch.setattr(current_settings, "database_type", "postgres")
        monkeypatch.setattr(current_settings, "database_pool_size", 99)

        # Attempting to fetch engine without dispose must fail closed
        with pytest.raises(RuntimeError, match="Database configuration change detected for tenant 'bank_test_cfg'"):
            _get_or_create_engine("bank_test_cfg")

        # Explicit disposal allows new engine with updated configuration
        await dispose_all_engines()
        eng2 = _get_or_create_engine("bank_test_cfg")
        assert eng2 is not None
        assert eng2 is not eng1
    finally:
        await dispose_all_engines()


@pytest.mark.asyncio
async def test_tenant_isolation_fails_closed_on_schema_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that failures during schema switching fail closed and do not yield unintended sessions."""
    from unittest.mock import AsyncMock

    from app.config import get_settings
    from app.infrastructure.database import (
        active_tenant,
        get_async_session,
        get_tenant_session,
    )

    current_settings = get_settings()
    monkeypatch.setattr(current_settings, "database_type", "postgres")

    # Mock session factory producing a session whose execute fails on search_path
    class FailingSession:
        def __init__(self) -> None:
            self.closed = False

        async def execute(self, stmt: Any) -> Any:
            raise RuntimeError("Database permission denied for tenant schema")

        async def close(self) -> None:
            self.closed = True

        async def __aenter__(self) -> FailingSession:
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            await self.close()

    mock_factory = MagicMock(return_value=FailingSession())
    monkeypatch.setattr("app.infrastructure.database.get_session_factory", lambda tenant: mock_factory)
    monkeypatch.setattr("app.infrastructure.database.init_tenant_tables", AsyncMock())

    # get_tenant_session must raise RuntimeError
    with pytest.raises(RuntimeError, match="Tenant isolation failure: could not set search_path for tenant 'bank_fail'"):
        async for _ in get_tenant_session("bank_fail"):
            pytest.fail("Session should not have been yielded after schema failure")

    # get_async_session with active_tenant must raise RuntimeError
    tok = active_tenant.set("bank_fail")
    try:
        with pytest.raises(RuntimeError, match="Tenant isolation failure: could not set search_path for tenant 'bank_fail'"):
            async for _ in get_async_session():
                pytest.fail("Session should not have been yielded after schema failure")
    finally:
        active_tenant.reset(tok)


def test_tenant_connections_isolation_urls() -> None:
    """Verifies separate and distinct database URLs for central, bank_a, and bank_b."""
    from app.infrastructure.database import _resolve_database_url

    central = _resolve_database_url(None)
    bank_a = _resolve_database_url("bank_a")
    bank_b = _resolve_database_url("bank_b")

    assert central != bank_a
    assert bank_a != bank_b
    assert "bank_a" in bank_a
    assert "bank_b" in bank_b


def test_redact_database_url_various_schemes() -> None:
    """Verifies that redact_database_url hides passwords across all URL formats without corrupting non-sensitive parts."""
    from app.infrastructure.database import redact_database_url

    # SQLite
    sqlite_url = "sqlite+aiosqlite:///storage/cfi_central.db"
    assert redact_database_url(sqlite_url) == sqlite_url

    # PostgreSQL with password
    secret_pass = "P@ssw0rd_SuperSecret_99!"
    pg_url = f"postgresql+asyncpg://postgres:{secret_pass}@localhost:5432/cfi_central"
    redacted = redact_database_url(pg_url)
    assert secret_pass not in redacted
    assert redacted == "postgresql+asyncpg://postgres:***@localhost:5432/cfi_central"

    # PostgreSQL without password
    no_pass_url = "postgresql+asyncpg://readonly_user@localhost:5432/cfi_db"
    assert redact_database_url(no_pass_url) == no_pass_url

    # Empty and None
    assert redact_database_url("") == ""
    assert redact_database_url(None) == ""


@pytest.mark.asyncio
async def test_database_configuration_mismatch_exception_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that database configuration mismatch RuntimeError redacts credentials."""
    from app.config import get_settings
    from app.infrastructure.database import _get_or_create_engine, dispose_all_engines

    await dispose_all_engines()
    secret_initial = "InitSecretPass_123!"
    secret_changed = "ChangedSecretPass_456!"

    try:
        current_settings = get_settings()
        monkeypatch.setattr(current_settings, "database_type", "postgres")
        monkeypatch.setattr(current_settings, "postgres_user", "db_admin")
        monkeypatch.setattr(current_settings, "postgres_password", secret_initial)
        monkeypatch.setattr(current_settings, "postgres_host", "db.internal.bank")
        monkeypatch.setattr(current_settings, "postgres_port", 5432)
        monkeypatch.setattr(current_settings, "postgres_db", "cfi_prod")

        mock_eng1 = MagicMock()
        mock_eng1.dispose = AsyncMock()
        mock_eng1.sync_engine = MagicMock()
        monkeypatch.setattr(
            "app.infrastructure.database.create_async_engine",
            MagicMock(return_value=mock_eng1),
        )

        eng1 = _get_or_create_engine("bank_a")
        assert eng1 is not None

        # Change password, creating configuration mismatch
        monkeypatch.setattr(current_settings, "postgres_password", secret_changed)

        with pytest.raises(RuntimeError) as exc_info:
            _get_or_create_engine("bank_a")

        err_msg = str(exc_info.value)
        assert secret_initial not in err_msg
        assert secret_changed not in err_msg
        assert "postgresql+asyncpg://db_admin:***@db.internal.bank:5432/cfi_prod_bank_a" in err_msg
        assert "Database configuration change detected for tenant 'bank_a'" in err_msg
    finally:
        await dispose_all_engines()


@pytest.mark.asyncio
async def test_database_engine_creation_log_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verifies that database engine creation logger never prints raw database secrets."""
    from app.config import get_settings
    from app.infrastructure.database import _get_or_create_engine, dispose_all_engines

    await dispose_all_engines()
    secret_pw = "SecretLogPass_789!"

    try:
        current_settings = get_settings()
        monkeypatch.setattr(current_settings, "database_type", "postgres")
        monkeypatch.setattr(current_settings, "postgres_user", "db_logger")
        monkeypatch.setattr(current_settings, "postgres_password", secret_pw)
        monkeypatch.setattr(current_settings, "postgres_host", "db.internal.bank")
        monkeypatch.setattr(current_settings, "postgres_port", 5432)
        monkeypatch.setattr(current_settings, "postgres_db", "cfi_prod")

        mock_eng2 = MagicMock()
        mock_eng2.dispose = AsyncMock()
        mock_eng2.sync_engine = MagicMock()
        monkeypatch.setattr(
            "app.infrastructure.database.create_async_engine",
            MagicMock(return_value=mock_eng2),
        )

        with caplog.at_level(logging.INFO, logger="app.infrastructure.database"):
            _get_or_create_engine("bank_b")

        assert "Created database engine for tenant=bank_b" in caplog.text
        assert secret_pw not in caplog.text
        assert "postgresql+asyncpg://db_logger:***@db.internal.bank:5432/cfi_prod_bank_b" in caplog.text
    finally:
        await dispose_all_engines()

