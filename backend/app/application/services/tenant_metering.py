"""Tenant Metering & Resource Quota Enforcement Service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import threading

logger = logging.getLogger(__name__)


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


class TenantMeteringService:
    """Tracks real-time usage metrics and enforces quota boundaries."""

    def __init__(self) -> None:
        self._quotas: dict[str, TenantQuotaLimits] = {}
        self._usage: dict[str, TenantUsageMetrics] = {}
        self._lock = threading.RLock()

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

    def get_usage(self, tenant_id: str) -> TenantUsageMetrics:
        """Retrieves or initializes usage metrics for a tenant."""
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            today = datetime.now(UTC).strftime("%Y-%m-%d")

            if clean_tenant not in self._usage:
                self._usage[clean_tenant] = TenantUsageMetrics(last_reset_date=today)

            usage = self._usage[clean_tenant]
            if usage.last_reset_date != today:
                usage.daily_inferences = 0
                usage.last_reset_date = today

            return usage

    def record_inference(self, tenant_id: str, count: int = 1) -> None:
        """Records inference request executions."""
        with self._lock:
            usage = self.get_usage(tenant_id)
            usage.daily_inferences += count

    def record_fl_round(self, tenant_id: str) -> None:
        """Records participation in a federated learning training round."""
        with self._lock:
            usage = self.get_usage(tenant_id)
            usage.monthly_fl_rounds += 1

    def acquire_quota(
        self, tenant_id: str, feature: str = "INFERENCE", count: int = 1
    ) -> tuple[bool, str]:
        """Atomically validates and reserves quota capacity in a single operation.

        Eliminates check-then-act race conditions under concurrent burst load.
        """
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            if clean_tenant not in self._quotas:
                self._quotas[clean_tenant] = TenantQuotaLimits()
            limits = self._quotas[clean_tenant]

            today = datetime.now(UTC).strftime("%Y-%m-%d")
            if clean_tenant not in self._usage:
                self._usage[clean_tenant] = TenantUsageMetrics(last_reset_date=today)
            usage = self._usage[clean_tenant]
            if usage.last_reset_date != today:
                usage.daily_inferences = 0
                usage.last_reset_date = today

            feature_upper = feature.upper()
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

            return True, "OK"

    def release_quota(
        self, tenant_id: str, feature: str = "INFERENCE", count: int = 1
    ) -> None:
        """Releases previously acquired quota on request execution failure."""
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            if clean_tenant not in self._usage:
                return
            usage = self._usage[clean_tenant]
            feature_upper = feature.upper()
            if feature_upper == "INFERENCE":
                usage.daily_inferences = max(0, usage.daily_inferences - count)
            elif feature_upper == "FL_ROUND":
                usage.monthly_fl_rounds = max(0, usage.monthly_fl_rounds - count)
            elif feature_upper == "STORAGE":
                usage.storage_used_mb = max(0.0, usage.storage_used_mb - float(count))

    def check_quota(self, tenant_id: str, feature: str = "INFERENCE") -> tuple[bool, str]:
        """Validates if tenant action is within quota limits without consuming. Returns (allowed, reason)."""
        with self._lock:
            clean_tenant = tenant_id.lower().strip()
            if clean_tenant not in self._quotas:
                self._quotas[clean_tenant] = TenantQuotaLimits()
            limits = self._quotas[clean_tenant]

            today = datetime.now(UTC).strftime("%Y-%m-%d")
            if clean_tenant not in self._usage:
                self._usage[clean_tenant] = TenantUsageMetrics(last_reset_date=today)
            usage = self._usage[clean_tenant]
            if usage.last_reset_date != today:
                usage.daily_inferences = 0
                usage.last_reset_date = today

            feature_upper = feature.upper()
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

    def get_billing_summary(self, tenant_id: str) -> dict[str, Any]:
        """Generates billing summary metrics for dashboard and invoicing."""
        usage = self.get_usage(tenant_id)
        limits = self.get_quota_limits(tenant_id)

        # Base tier billing estimation ($0.001 per inference, $10 per FL round)
        estimated_cost_usd = (usage.daily_inferences * 0.001) + (usage.monthly_fl_rounds * 10.0)

        return {
            "tenant_id": tenant_id,
            "daily_inferences": usage.daily_inferences,
            "max_daily_inferences": limits.max_daily_inferences,
            "monthly_fl_rounds": usage.monthly_fl_rounds,
            "max_monthly_fl_rounds": limits.max_monthly_fl_rounds,
            "estimated_cost_usd": round(estimated_cost_usd, 2),
        }


_shared_tenant_metering_service = TenantMeteringService()


def get_tenant_metering_service() -> TenantMeteringService:
    """Returns the singleton TenantMeteringService instance."""
    return _shared_tenant_metering_service

