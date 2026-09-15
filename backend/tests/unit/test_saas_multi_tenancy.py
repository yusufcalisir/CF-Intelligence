# ruff: noqa: E402
"""Automated Unit Test Suite for SaaS Multi-Tenancy & Tenant Lifecycle Isolation."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.tenant_management import TenantRegistry, TenantStatus
from app.infrastructure.database import VALID_TENANTS, active_tenant
from app.infrastructure.tenant_provisioner import TenantProvisioner

settings = get_settings()


@pytest.fixture(autouse=True)
def force_sqlite_database_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces sqlite database mode for unit testing when postgres server is offline."""
    monkeypatch.setattr(settings, "database_type", "sqlite")


@pytest.mark.asyncio
async def test_tenant_registry_lifecycle_state_machine() -> None:
    """Test tenant registry creation, state transitions, and querying."""
    registry = TenantRegistry()

    # 1. Register new tenant
    tenant = registry.register_tenant("bank_delta", "Delta Regional Bank")
    assert tenant.tenant_id == "bank_delta"
    assert tenant.status == TenantStatus.PROVISIONING
    assert tenant.db_schema == "tenant_bank_delta"

    # 2. Transition to ACTIVE
    registry.set_status("bank_delta", TenantStatus.ACTIVE)
    assert registry.is_tenant_active("bank_delta") is True
    assert len(registry.list_active_tenants()) == 1

    # 3. Transition to SUSPENDED
    registry.set_status("bank_delta", TenantStatus.SUSPENDED)
    assert registry.is_tenant_active("bank_delta") is False


@pytest.mark.asyncio
async def test_tenant_provisioner_automated_schema_creation() -> None:
    """Test automated tenant provisioning and DDL database creation."""
    registry = TenantRegistry()
    provisioner = TenantProvisioner(registry=registry)

    # Provision new tenant bank_omega
    tenant = await provisioner.provision_tenant("bank_omega", "Omega National Bank")

    assert tenant.status == TenantStatus.ACTIVE
    assert "bank_omega" in VALID_TENANTS
    assert registry.is_tenant_active("bank_omega") is True

    # Test suspension & deletion
    await provisioner.suspend_tenant("bank_omega")
    suspended_tenant = registry.get_tenant("bank_omega")
    assert suspended_tenant is not None
    assert suspended_tenant.status == TenantStatus.SUSPENDED

    deleted = await provisioner.delete_tenant("bank_omega", purge_database=True)
    assert deleted is True
    assert "bank_omega" not in VALID_TENANTS


@pytest.mark.asyncio
async def test_tenant_context_isolation() -> None:
    """Test ContextVar tenant isolation routing."""
    token1 = active_tenant.set("bank_a")
    assert active_tenant.get() == "bank_a"

    active_tenant.reset(token1)
    assert active_tenant.get() is None
