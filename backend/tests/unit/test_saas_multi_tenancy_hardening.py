"""Comprehensive Hardening & Security Audit Test Suite for SaaS Multi-Tenancy.

Verifies:
1. Domain entity invariant enforcement (path traversal, SQL injection, naming rules)
2. Thread-safe registry concurrency under simultaneous worker threads
3. Temporal rollover resets (daily inferences & monthly FL rounds)
4. Fail-closed quota boundaries and non-positive input rejections
5. Storage usage tracking and billing summary telemetry
6. Isolated Redis cache namespace enclosures
7. Database URL resolution sanitization
8. Unified TenantProvisioner lifecycle facade
"""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime, timedelta

import pytest

from app.application.services.tenant_metering import (
    TenantMeteringService,
    TenantQuotaLimits,
)
from app.domain.tenant_management import TenantRecord, TenantRegistry, TenantStatus
from app.infrastructure.cache import CacheService
from app.infrastructure.database import _resolve_database_url
from app.infrastructure.tenant_provisioner import TenantProvisioner, sanitize_bank_id

# ── 1. Domain Entity Invariants & Sanitization ───────────────────


def test_tenant_record_invariant_validation() -> None:
    """Verifies that TenantRecord enforces strict identifier constraints."""
    # Empty or non-string
    with pytest.raises(ValueError, match="Tenant ID must be a non-empty string"):
        TenantRecord(tenant_id="", name="Empty Bank")

    # Exceeding 48-char maximum limit
    with pytest.raises(ValueError, match="exceeds 48-character maximum"):
        TenantRecord(tenant_id="a" * 49, name="Long Bank")

    # Path traversal characters
    with pytest.raises(ValueError, match="Invalid tenant_id format"):
        TenantRecord(tenant_id="../../etc/passwd", name="Traversal Bank")

    # SQL injection characters
    with pytest.raises(ValueError, match="Invalid tenant_id format"):
        TenantRecord(tenant_id="bank_a' OR '1'='1", name="Injection Bank")

    # Must not start with a digit
    with pytest.raises(ValueError, match="must not start with a digit"):
        TenantRecord(tenant_id="1_bank", name="Digit Bank")

    # Valid tenant normalized to lowercase
    record = TenantRecord(tenant_id="BANK_EPSILON", name="Epsilon Bank")
    assert record.tenant_id == "bank_epsilon"
    assert record.db_schema == "tenant_bank_epsilon"
    assert record.kms_key_path == "storage/bank_epsilon/kms/"


# ── 2. Registry Concurrency & Lifecycle ─────────────────────────


def test_tenant_registry_thread_safety_and_concurrency() -> None:
    """Verifies that TenantRegistry safely handles simultaneous multi-threaded operations."""
    registry = TenantRegistry()

    def worker(idx: int) -> None:
        tenant_id = f"bank_worker_{idx}"
        name = f"Worker Bank {idx}"
        reg = registry.register_tenant(tenant_id, name)
        assert reg.status == TenantStatus.PROVISIONING
        registry.set_status(tenant_id, TenantStatus.ACTIVE)
        assert registry.is_tenant_active(tenant_id) is True

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(20)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    active_tenants = registry.list_active_tenants()
    assert len(active_tenants) == 20


def test_tenant_registry_deletion_and_reactivation() -> None:
    """Verifies deletion lifecycle and re-registration of previously deleted tenants."""
    registry = TenantRegistry()
    tenant_id = "bank_zeta"
    registry.register_tenant(tenant_id, "Zeta Bank")
    registry.set_status(tenant_id, TenantStatus.ACTIVE)

    # Delete tenant
    assert registry.delete_tenant(tenant_id) is True
    assert registry.is_tenant_active(tenant_id) is False

    # List filtering
    assert len(registry.list_all_tenants(include_deleted=False)) == 0
    assert len(registry.list_all_tenants(include_deleted=True)) == 1

    # Re-register previously deleted tenant -> re-activates to PROVISIONING
    reactivated = registry.register_tenant(tenant_id, "Zeta Bank New")
    assert reactivated.status == TenantStatus.PROVISIONING
    assert reactivated.name == "Zeta Bank New"

    # Purge completely
    assert registry.purge_tenant(tenant_id) is True
    assert registry.get_tenant(tenant_id) is None


# ── 3. Tenant Metering Temporal Rollover Resets ───────────────────


def test_tenant_metering_temporal_rollover_daily_and_monthly() -> None:
    """Verifies that daily inferences reset daily and monthly FL rounds reset monthly."""
    metering = TenantMeteringService()
    tenant = "bank_temporal"

    # Set base date
    base_time = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
    metering.acquire_quota(tenant, "INFERENCE", count=50, now=base_time)
    metering.acquire_quota(tenant, "FL_ROUND", count=5, now=base_time)

    usage_same_day = metering.get_usage(tenant, now=base_time)
    assert usage_same_day.daily_inferences == 50
    assert usage_same_day.monthly_fl_rounds == 5

    # Advance 1 day (next day in same month)
    next_day = base_time + timedelta(days=1)
    usage_next_day = metering.get_usage(tenant, now=next_day)
    # Daily inferences should reset to 0
    assert usage_next_day.daily_inferences == 0
    # Monthly FL rounds should persist across days in the same month
    assert usage_next_day.monthly_fl_rounds == 5

    # Advance to next month (e.g., 2026-10-01)
    next_month = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    usage_next_month = metering.get_usage(tenant, now=next_month)
    # Both should reset in a new month
    assert usage_next_month.daily_inferences == 0
    assert usage_next_month.monthly_fl_rounds == 0


# ── 4. Fail-Closed Quota Validation & Count Invariants ────────────


def test_tenant_metering_fail_closed_and_invalid_inputs() -> None:
    """Verifies fail-closed behavior for unsupported features and non-positive counts."""
    metering = TenantMeteringService()
    tenant = "bank_failclosed"

    # Non-positive count validation
    with pytest.raises(ValueError, match="Quota acquisition count must be strictly positive"):
        metering.acquire_quota(tenant, "INFERENCE", count=0)

    with pytest.raises(ValueError, match="Quota acquisition count must be strictly positive"):
        metering.acquire_quota(tenant, "INFERENCE", count=-5)

    with pytest.raises(ValueError, match="Quota release count must be strictly positive"):
        metering.release_quota(tenant, "INFERENCE", count=0)

    with pytest.raises(ValueError, match="Inference count must be strictly positive"):
        metering.record_inference(tenant, count=-1)

    with pytest.raises(ValueError, match="FL round count must be strictly positive"):
        metering.record_fl_round(tenant, count=0)

    # Unsupported feature fails closed (returns False, not True)
    allowed, reason = metering.acquire_quota(tenant, "UNSUPPORTED_GPU_FEATURE", count=1)
    assert allowed is False
    assert "Unsupported quota feature" in reason

    allowed_chk, reason_chk = metering.check_quota(tenant, "INVALID_FEATURE")
    assert allowed_chk is False
    assert "Unsupported quota feature" in reason_chk


# ── 5. Storage Lifecycle & Telemetry Billing Summary ──────────────


def test_tenant_metering_storage_and_billing_telemetry() -> None:
    """Verifies storage quota enforcement, direct updates, and billing summary metrics."""
    metering = TenantMeteringService()
    tenant = "bank_storage"

    metering.set_quota_limits(
        tenant,
        TenantQuotaLimits(
            max_daily_inferences=1000,
            max_monthly_fl_rounds=10,
            max_storage_mb=500.0,
        ),
    )

    # Valid storage update
    ok, reason = metering.update_storage_usage(tenant, 250.5)
    assert ok is True
    assert reason == "OK"

    # Exceeding storage limit
    exceeded_ok, exceeded_reason = metering.update_storage_usage(tenant, 600.0)
    assert exceeded_ok is False
    assert "Storage quota exceeded" in exceeded_reason

    # Negative storage rejection
    with pytest.raises(ValueError, match="Storage usage cannot be negative"):
        metering.update_storage_usage(tenant, -10.0)

    # Inferences and FL rounds for billing calculation
    metering.acquire_quota(tenant, "INFERENCE", count=100)
    metering.acquire_quota(tenant, "FL_ROUND", count=2)

    summary = metering.get_billing_summary(tenant)
    assert summary["tenant_id"] == "bank_storage"
    assert summary["daily_inferences"] == 100
    assert summary["monthly_fl_rounds"] == 2
    assert summary["storage_used_mb"] == 600.0
    assert summary["max_storage_mb"] == 500.0
    # Expected cost: (100 * 0.001) + (2 * 10.0) = 0.10 + 20.0 = 20.10
    assert summary["estimated_cost_usd"] == 20.10


# ── 6. Isolated Redis Cache Namespace Enclosures ──────────────────


def test_redis_tenant_namespace_isolation() -> None:
    """Verifies deterministic Redis tenant namespace key construction and attack rejection."""
    cache = CacheService.get()

    # Deterministic lowercase namespacing
    key_alpha = cache.get_tenant_key("BANK_ALPHA", "risk_model")
    assert key_alpha == "cfi:tenant:bank_alpha:risk_model"

    key_beta = cache.get_tenant_key("bank_beta", "risk_model")
    assert key_beta == "cfi:tenant:bank_beta:risk_model"
    assert key_alpha != key_beta

    # Path traversal / SQL injection in tenant ID strictly rejected
    with pytest.raises(ValueError, match="Invalid bank_id format"):
        cache.get_tenant_key("../../etc", "key")

    with pytest.raises(ValueError, match="Invalid bank_id format"):
        cache.get_tenant_key("bank_a' OR '1'='1", "key")


# ── 7. Database URL Resolution Sanitization ───────────────────────


def test_database_url_resolution_security() -> None:
    """Verifies that _resolve_database_url rejects path traversal and SQL injection in tenant parameter."""
    # Malicious path traversal
    with pytest.raises(ValueError, match="Invalid bank_id format"):
        _resolve_database_url("../../malicious")

    # SQL injection attempt
    with pytest.raises(ValueError, match="Invalid bank_id format"):
        _resolve_database_url("bank_a' OR '1'='1")

    # Valid tenant resolves cleanly
    url = _resolve_database_url("bank_gamma")
    assert "bank_gamma" in url


# ── 8. Unified TenantProvisioner Facade & Lifecycle ───────────────


@pytest.mark.asyncio
async def test_tenant_provisioner_facade_and_lifecycle() -> None:
    """Verifies that app.infrastructure.tenant_provisioner re-exports the canonical hardened provisioner."""
    registry = TenantRegistry()
    provisioner = TenantProvisioner(registry=registry)

    # Sanitize bank id re-export
    clean = sanitize_bank_id("BANK_THETA_01")
    assert clean == "bank_theta_01"

    # Provision tenant
    tenant = await provisioner.provision_tenant("bank_theta_01", "Theta National Bank")
    assert tenant.status == TenantStatus.ACTIVE
    assert registry.is_tenant_active("bank_theta_01") is True

    # Suspend tenant
    await provisioner.suspend_tenant("bank_theta_01")
    assert registry.get_tenant("bank_theta_01").status == TenantStatus.SUSPENDED

    # Delete tenant
    deleted = await provisioner.delete_tenant("bank_theta_01", purge_database=True)
    assert deleted is True
    assert registry.get_tenant("bank_theta_01").status == TenantStatus.DELETED
