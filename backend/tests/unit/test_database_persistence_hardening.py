"""Hardening and resilience tests for Database Persistence, Alembic Migrations & Concurrency Locking (Phase 67).

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
"""

from __future__ import annotations

import asyncio
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
    assert heads[0] == "002_core_and_aml_tables"


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
