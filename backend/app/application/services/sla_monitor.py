"""Latency SLA Monitor & Percentile Tracker."""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SLAMetricsSummary:
    """Container for real-time latency SLA percentile distributions."""

    total_requests: int
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    sla_violations_count: int
    sla_compliance_pct: float


class RealtimeSLAMonitor:
    """Tracks latency samples and monitors p50/p95/p99 SLA compliance."""

    def __init__(self, target_sla_ms: float = 100.0) -> None:
        self.target_sla_ms = target_sla_ms
        self._latencies: deque[float] = deque(maxlen=10000)
        self._violations_count: int = 0

    def record_latency(self, latency_ms: float) -> bool:
        """Records latency sample. Returns True if compliant, False if SLA target is breached."""
        self._latencies.append(latency_ms)
        is_compliant = latency_ms <= self.target_sla_ms

        if not is_compliant:
            self._violations_count += 1
            logger.warning(
                "SLA Target Breached! Latency: %.2fms > Target: %.2fms",
                latency_ms,
                self.target_sla_ms,
            )

        return is_compliant

    def get_sla_summary(self) -> SLAMetricsSummary:
        """Computes p50, p95, p99 percentiles and SLA compliance percentage."""
        if not self._latencies:
            return SLAMetricsSummary(
                total_requests=0,
                p50_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                sla_violations_count=0,
                sla_compliance_pct=100.0,
            )

        sorted_latencies = sorted(self._latencies)
        n = len(sorted_latencies)

        def _percentile(pct: float) -> float:
            k = (n - 1) * (pct / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return sorted_latencies[f]
            return sorted_latencies[f] * (c - k) + sorted_latencies[c] * (k - f)

        p50 = round(_percentile(50.0), 2)
        p95 = round(_percentile(95.0), 2)
        p99 = round(_percentile(99.0), 2)

        compliance_pct = round(((n - self._violations_count) / n) * 100.0, 2)

        return SLAMetricsSummary(
            total_requests=n,
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            sla_violations_count=self._violations_count,
            sla_compliance_pct=compliance_pct,
        )
