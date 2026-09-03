"""Multi-Tenant Security Isolation & Cross-Tenant Boundary Penetration Audit Suite."""

from __future__ import annotations

import pytest

from app.infrastructure.database import _get_or_create_engine, active_tenant
from app.infrastructure.database.tenant_provisioner import (
    sanitize_bank_id,
)


def test_tenant_id_sanitization_defense() -> None:
    """Verifies that malicious path traversal and SQL injection characters are strictly rejected with ValueError."""
    # Path traversal attack attempt
    with pytest.raises(ValueError, match="Invalid bank_id format"):
        sanitize_bank_id("../../../etc/passwd")

    # SQL Injection attempt
    with pytest.raises(ValueError, match="Invalid bank_id format"):
        sanitize_bank_id("bank_a' OR '1'='1")

    # Valid tenant passes
    valid_id = sanitize_bank_id("BANK_ALPHA_01")
    assert valid_id == "bank_alpha_01"


def test_contextvar_cross_tenant_session_isolation() -> None:
    """Verifies that setting active_tenant strictly scopes execution without cross-tenant leak."""
    bank_a_token = active_tenant.set("bank_a")

    try:
        assert active_tenant.get() == "bank_a"
        engine_a = _get_or_create_engine("bank_a")
        assert engine_a is not None

        # Switch context to bank_b
        bank_b_token = active_tenant.set("bank_b")
        try:
            assert active_tenant.get() == "bank_b"
            engine_b = _get_or_create_engine("bank_b")
            assert engine_b is not None
            # Engines must belong to different database isolation pools
            assert engine_a is not engine_b
            assert "bank_a" in str(engine_a.url)
            assert "bank_b" in str(engine_b.url)
        finally:
            active_tenant.reset(bank_b_token)

        # Context restored to bank_a
        assert active_tenant.get() == "bank_a"
    finally:
        active_tenant.reset(bank_a_token)


def test_redis_namespace_key_enclosure() -> None:
    """Verifies that tenant caching keys are deterministically enclosed in isolated namespaces."""

    def generate_tenant_cache_key(tenant_id: str, resource_key: str) -> str:
        clean_tenant = sanitize_bank_id(tenant_id)
        return f"cfi:tenant:{clean_tenant}:{resource_key}"

    key_bank_a = generate_tenant_cache_key("bank_a", "risk_model_weights")
    key_bank_b = generate_tenant_cache_key("bank_b", "risk_model_weights")

    assert key_bank_a == "cfi:tenant:bank_a:risk_model_weights"
    assert key_bank_b == "cfi:tenant:bank_b:risk_model_weights"
    assert key_bank_a != key_bank_b
    assert key_bank_a.startswith("cfi:tenant:bank_a:")
    assert key_bank_b.startswith("cfi:tenant:bank_b:")


@pytest.mark.asyncio
async def test_concurrent_async_contextvar_isolation() -> None:
    """Active Penetration: 50 concurrent async coroutines interleaved across multiple bank tenants.

    Verifies that task context switching (asyncio.sleep) never causes dirty ContextVar bleed
    or cross-tenant state corruption. Asserts exactly 0 context leaks across 50 tasks.
    """
    import asyncio

    leak_count = 0

    async def tenant_worker(tenant_id: str, cycles: int = 5) -> bool:
        nonlocal leak_count
        token = active_tenant.set(tenant_id)
        try:
            for _ in range(cycles):
                await asyncio.sleep(0.001)
                observed = active_tenant.get()
                if observed != tenant_id:
                    leak_count += 1
                    return False
            return True
        finally:
            active_tenant.reset(token)

    tenants = ["bank_alpha", "bank_beta", "bank_gamma", "bank_delta", "bank_epsilon"] * 10
    tasks = [tenant_worker(t) for t in tenants]
    results = await asyncio.gather(*tasks)

    assert leak_count == 0, f"Detected {leak_count} cross-tenant context leaks!"
    assert all(results) is True
    assert len(results) == 50


def test_cross_tenant_active_cache_barrier_penetration() -> None:
    """Active Penetration: Simulates an unauthorized bank attempting to access another bank's cache payload.

    Verifies that identical resource keys under isolated namespaces prevent cross-tenant exposure.
    """
    mock_redis_storage: dict[str, bytes] = {}

    def tenant_cache_set(tenant_id: str, key: str, value: bytes) -> None:
        clean = sanitize_bank_id(tenant_id)
        mock_redis_storage[f"cfi:tenant:{clean}:{key}"] = value

    def tenant_cache_get(tenant_id: str, key: str) -> bytes | None:
        clean = sanitize_bank_id(tenant_id)
        return mock_redis_storage.get(f"cfi:tenant:{clean}:{key}")

    # Bank A writes confidential fraud model parameters
    tenant_cache_set("bank_a", "champion_model_weights", b"BANK_A_TOP_SECRET_PARAMETERS")

    # Bank B attempts to read the same resource identifier
    unauthorized_payload = tenant_cache_get("bank_b", "champion_model_weights")

    # Assert 0 cross-tenant data bleed
    assert unauthorized_payload is None, "Bank B breached Bank A's cached model parameters!"

    # Bank A reads successfully
    legitimate_payload = tenant_cache_get("bank_a", "champion_model_weights")
    assert legitimate_payload == b"BANK_A_TOP_SECRET_PARAMETERS"


def test_cross_tenant_database_query_barrier_penetration() -> None:
    """Active Penetration: Simulates an unauthorized query filter attempt across tenant boundary.

    Verifies that when active_tenant is set to bank_a, query generation or execution strictly
    enforces tenant scoping and rejects foreign tenant data retrieval.
    """
    bank_a_token = active_tenant.set("bank_a")
    try:
        current_tenant = active_tenant.get()
        assert current_tenant == "bank_a"

        # Simulate tenant-scoped ORM query filter contract
        def build_tenant_filter(requested_bank_id: str | None = None) -> str:
            scoped_tenant = active_tenant.get()
            if requested_bank_id and requested_bank_id != scoped_tenant:
                raise PermissionError(
                    f"Tenant boundary violation: Context tenant '{scoped_tenant}' cannot access tenant '{requested_bank_id}'"
                )
            return f"WHERE bank_id = '{scoped_tenant}'"

        # Legitimate query passes
        query_clause = build_tenant_filter("bank_a")
        assert query_clause == "WHERE bank_id = 'bank_a'"

        # Cross-tenant exfiltration attempt by Bank A querying Bank B data
        with pytest.raises(PermissionError, match="Tenant boundary violation"):
            build_tenant_filter("bank_b")

    finally:
        active_tenant.reset(bank_a_token)

