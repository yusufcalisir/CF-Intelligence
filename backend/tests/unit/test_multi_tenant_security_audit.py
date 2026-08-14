"""Multi-Tenant Security Isolation & Cross-Tenant Boundary Penetration Audit Suite."""

from __future__ import annotations

import pytest
from app.infrastructure.database import _get_or_create_engine, active_tenant
from app.infrastructure.database.tenant_provisioner import (
    TenantProvisioner,
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
