"""Tenant Metering & Resource Quota Enforcement Service."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_QUOTA_FEATURES: frozenset[str] = frozenset({"INFERENCE", "FL_ROUND", "STORAGE"})


@dataclass
class TenantQuotaLimits:
    """Configured resource quota limits for a tenant institution."""

    max_daily_inferences: int = 10000
    max_monthly_fl_rounds: int = 50
    max_storage_mb: float = 1000.0


@dataclass
class TenantUsageMetrics:
    """Real-time usage counter metrics for billing and quota enforcement."""

    daily_inferences: int = 0
    monthly_fl_rounds: int = 0
    storage_used_mb: float = 0.0
    last_reset_date: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d"))
    last_reset_month: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m"))


class TenantMeteringService:
    """Tracks real-time usage metrics and enforces quota boundaries."""

    _MAX_METERED_TENANTS: int = 10000

    def __init__(self) -> None:
        self._quotas: dict[str, TenantQuotaLimits] = {}
        self._usage: dict[str, TenantUsageMetrics] = {}
        self._lock = threading.RLock()

    def _check_and_reset(self, usage: TenantUsageMetrics, now: datetime | None = None) -> None:
        """Evaluates temporal boundaries and resets daily and monthly quota counters."""
        current_time = now or datetime.now(UTC)
        today = current_time.strftime("%Y-%m-%d")
        this_month = current_time.strftime("%Y-%m")

        if usage.last_reset_date != today:
            usage.daily_inferences = 0
            usage.last_reset_date = today

        if usage.last_reset_month != this_month:
            usage.monthly_fl_rounds = 0
            usage.last_reset_month = this_month

    def get_quota_limits(self, tenant_id: str) -> TenantQuotaLimits:
        """Retrieves or creates default quota limits for a tenant."""
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            if clean_tenant not in self._quotas:
                self._quotas[clean_tenant] = TenantQuotaLimits()
            return self._quotas[clean_tenant]

    def set_quota_limits(self, tenant_id: str, limits: TenantQuotaLimits) -> None:
        """Sets custom resource quota limits for a tenant."""
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            self._quotas[clean_tenant] = limits

    def get_usage(self, tenant_id: str, now: datetime | None = None) -> TenantUsageMetrics:
        """Retrieves or initializes usage metrics for a tenant with temporal reset validation."""
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            today = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
            this_month = (now or datetime.now(UTC)).strftime("%Y-%m")

            if clean_tenant not in self._usage:
                if len(self._usage) >= self._MAX_METERED_TENANTS:
                    # Bounded size eviction of oldest entry
                    oldest_key = next(iter(self._usage))
                    del self._usage[oldest_key]
                self._usage[clean_tenant] = TenantUsageMetrics(
                    last_reset_date=today, last_reset_month=this_month
                )

            usage = self._usage[clean_tenant]
            self._check_and_reset(usage, now=now)
            return usage

    def record_inference(self, tenant_id: str, count: int = 1) -> None:
        """Records inference request executions."""
        if count <= 0:
            raise ValueError("Inference count must be strictly positive (> 0)")
        with self._lock:
            usage = self.get_usage(tenant_id)
            usage.daily_inferences += count

    def record_fl_round(self, tenant_id: str, count: int = 1) -> None:
        """Records participation in a federated learning training round."""
        if count <= 0:
            raise ValueError("FL round count must be strictly positive (> 0)")
        with self._lock:
            usage = self.get_usage(tenant_id)
            usage.monthly_fl_rounds += count

    def acquire_quota(
        self,
        tenant_id: str,
        feature: str = "INFERENCE",
        count: int = 1,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Atomically validates and reserves quota capacity in a single operation.

        Eliminates check-then-act race conditions under concurrent burst load.
        """
        if count <= 0:
            raise ValueError("Quota acquisition count must be strictly positive (> 0)")

        feature_upper = feature.upper().strip()
        if feature_upper not in SUPPORTED_QUOTA_FEATURES:
            return False, f"Unsupported quota feature: '{feature}'"

        with self._lock:
            limits = self.get_quota_limits(tenant_id)
            usage = self.get_usage(tenant_id, now=now)

            if feature_upper == "INFERENCE":
                if usage.daily_inferences + count > limits.max_daily_inferences:
                    return (
                        False,
                        f"Daily inference quota exceeded ({usage.daily_inferences}/{limits.max_daily_inferences})",
                    )
                usage.daily_inferences += count
                return True, "OK"

            if feature_upper == "FL_ROUND":
                if usage.monthly_fl_rounds + count > limits.max_monthly_fl_rounds:
                    return (
                        False,
                        f"Monthly FL round quota exceeded ({usage.monthly_fl_rounds}/{limits.max_monthly_fl_rounds})",
                    )
                usage.monthly_fl_rounds += count
                return True, "OK"

            if feature_upper == "STORAGE":
                if usage.storage_used_mb + count > limits.max_storage_mb:
                    return (
                        False,
                        f"Storage quota exceeded ({usage.storage_used_mb:.1f}MB/{limits.max_storage_mb:.1f}MB)",
                    )
                usage.storage_used_mb += float(count)
                return True, "OK"

            return False, f"Unsupported quota feature: '{feature}'"

    def release_quota(
        self, tenant_id: str, feature: str = "INFERENCE", count: int = 1
    ) -> None:
        """Releases previously acquired quota on request execution failure."""
        if count <= 0:
            raise ValueError("Quota release count must be strictly positive (> 0)")

        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            if clean_tenant not in self._usage:
                return
            usage = self._usage[clean_tenant]
            feature_upper = feature.upper().strip()
            if feature_upper == "INFERENCE":
                usage.daily_inferences = max(0, usage.daily_inferences - count)
            elif feature_upper == "FL_ROUND":
                usage.monthly_fl_rounds = max(0, usage.monthly_fl_rounds - count)
            elif feature_upper == "STORAGE":
                usage.storage_used_mb = max(0.0, usage.storage_used_mb - float(count))

    def check_quota(
        self, tenant_id: str, feature: str = "INFERENCE", now: datetime | None = None
    ) -> tuple[bool, str]:
        """Validates if tenant action is within quota limits without consuming. Returns (allowed, reason)."""
        feature_upper = feature.upper().strip()
        if feature_upper not in SUPPORTED_QUOTA_FEATURES:
            return False, f"Unsupported quota feature: '{feature}'"

        with self._lock:
            limits = self.get_quota_limits(tenant_id)
            usage = self.get_usage(tenant_id, now=now)

            if feature_upper == "INFERENCE" and usage.daily_inferences >= limits.max_daily_inferences:
                return (
                    False,
                    f"Daily inference quota exceeded ({usage.daily_inferences}/{limits.max_daily_inferences})",
                )
            if feature_upper == "FL_ROUND" and usage.monthly_fl_rounds >= limits.max_monthly_fl_rounds:
                return (
                    False,
                    f"Monthly FL round quota exceeded ({usage.monthly_fl_rounds}/{limits.max_monthly_fl_rounds})",
                )
            if feature_upper == "STORAGE" and usage.storage_used_mb >= limits.max_storage_mb:
                return (
                    False,
                    f"Storage quota exceeded ({usage.storage_used_mb:.1f}MB/{limits.max_storage_mb:.1f}MB)",
                )

            return True, "OK"

    def update_storage_usage(self, tenant_id: str, storage_mb: float) -> tuple[bool, str]:
        """Directly updates the measured storage footprint and checks against limits."""
        if storage_mb < 0:
            raise ValueError("Storage usage cannot be negative")

        with self._lock:
            limits = self.get_quota_limits(tenant_id)
            usage = self.get_usage(tenant_id)
            usage.storage_used_mb = float(storage_mb)
            if usage.storage_used_mb > limits.max_storage_mb:
                return (
                    False,
                    f"Storage quota exceeded ({usage.storage_used_mb:.1f}MB/{limits.max_storage_mb:.1f}MB)",
                )
            return True, "OK"

    def get_billing_summary(self, tenant_id: str) -> dict[str, Any]:
        """Generates billing summary metrics for dashboard and invoicing."""
        with self._lock:
            usage = self.get_usage(tenant_id)
            limits = self.get_quota_limits(tenant_id)

            # Base tier billing estimation ($0.001 per inference, $10 per FL round)
            estimated_cost_usd = (usage.daily_inferences * 0.001) + (usage.monthly_fl_rounds * 10.0)

            return {
                "tenant_id": tenant_id.lower().strip(),
                "daily_inferences": usage.daily_inferences,
                "max_daily_inferences": limits.max_daily_inferences,
                "monthly_fl_rounds": usage.monthly_fl_rounds,
                "max_monthly_fl_rounds": limits.max_monthly_fl_rounds,
                "storage_used_mb": round(usage.storage_used_mb, 2),
                "max_storage_mb": limits.max_storage_mb,
                "estimated_cost_usd": round(estimated_cost_usd, 2),
            }

    def reset_tenant(self, tenant_id: str) -> None:
        """Resets usage counters for a specific tenant."""
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            if clean_tenant in self._usage:
                del self._usage[clean_tenant]

    def reset_all(self) -> None:
        """Clears all usage and quota tracking in memory."""
        with self._lock:
            self._usage.clear()
            self._quotas.clear()


_shared_tenant_metering_service = TenantMeteringService()


def get_tenant_metering_service() -> TenantMeteringService:
    """Returns the singleton TenantMeteringService instance."""
    return _shared_tenant_metering_service
