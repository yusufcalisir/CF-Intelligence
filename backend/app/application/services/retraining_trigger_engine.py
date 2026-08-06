"""Retraining Trigger Engine for Automated Asynchronous Background Workflows."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_INGESTION_THRESHOLD = 50000
DEFAULT_PSI_THRESHOLD = 0.20
DEFAULT_KS_PVALUE_THRESHOLD = 0.05
DEFAULT_CADENCE_HOURS = 24


class RetrainingTriggerEngine:
    """Evaluates data ingestion thresholds, statistical drift metrics (PSI / KS test),

    and cron schedules to dispatch automated model retraining tasks.
    """

    def __init__(
        self,
        ingestion_threshold: int = DEFAULT_INGESTION_THRESHOLD,
        psi_threshold: float = DEFAULT_PSI_THRESHOLD,
        ks_pvalue_threshold: float = DEFAULT_KS_PVALUE_THRESHOLD,
        cadence_hours: int = DEFAULT_CADENCE_HOURS,
    ) -> None:
        self.ingestion_threshold = ingestion_threshold
        self.psi_threshold = psi_threshold
        self.ks_pvalue_threshold = ks_pvalue_threshold
        self.cadence_hours = cadence_hours

    def check_ingestion_threshold(self, record_count: int) -> bool:
        """Evaluates whether new ingested transaction record volume meets or exceeds threshold (e.g. 50k)."""
        triggered = record_count >= self.ingestion_threshold
        if triggered:
            logger.info(
                "Ingestion threshold trigger MET: %d records >= %d target batch volume.",
                record_count,
                self.ingestion_threshold,
            )
        return triggered

    def check_drift_threshold(self, psi_score: float, ks_p_value: float = 1.0) -> bool:
        """Evaluates whether Population Stability Index (PSI > 0.20) or KS test indicates drift."""
        import math

        if not math.isfinite(psi_score) or not math.isfinite(ks_p_value):
            logger.warning(
                "Non-finite drift metrics received (PSI=%s, KS_p=%s). Retraining trigger suppressed.",
                psi_score,
                ks_p_value,
            )
            return False

        psi_triggered = psi_score > self.psi_threshold
        ks_triggered = ks_p_value < self.ks_pvalue_threshold

        if psi_triggered or ks_triggered:
            logger.warning(
                "Drift detection trigger MET: PSI=%.4f (limit=%.2f), KS p-value=%.4f (limit=%.2f).",
                psi_score,
                self.psi_threshold,
                ks_p_value,
                self.ks_pvalue_threshold,
            )
            return True
        return False

    def check_scheduled_cadence(self, last_run_timestamp: datetime | None) -> bool:
        """Evaluates whether scheduled cron cadence interval has elapsed."""
        if last_run_timestamp is None:
            return True

        now = datetime.now(UTC)
        elapsed = (now - last_run_timestamp).total_seconds() / 3600.0
        triggered = elapsed >= self.cadence_hours

        if triggered:
            logger.info(
                "Scheduled consortium cadence trigger MET: %.1fh elapsed >= %dh interval.",
                elapsed,
                self.cadence_hours,
            )
        return triggered

    def evaluate_triggers(
        self,
        record_count: int = 0,
        psi_score: float = 0.0,
        ks_p_value: float = 1.0,
        last_run_timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluates all trigger criteria and returns trigger status dictionary."""
        ingestion_hit = self.check_ingestion_threshold(record_count)
        drift_hit = self.check_drift_threshold(psi_score, ks_p_value)
        cadence_hit = self.check_scheduled_cadence(last_run_timestamp)

        is_triggered = ingestion_hit or drift_hit or cadence_hit

        trigger_reasons = []
        if ingestion_hit:
            trigger_reasons.append("INGESTION_THRESHOLD_REACHED")
        if drift_hit:
            trigger_reasons.append("STATISTICAL_DRIFT_DETECTED")
        if cadence_hit:
            trigger_reasons.append("SCHEDULED_CADENCE_ELAPSED")

        return {
            "is_triggered": is_triggered,
            "reasons": trigger_reasons,
            "details": {
                "record_count": record_count,
                "psi_score": psi_score,
                "ks_p_value": ks_p_value,
                "last_run": last_run_timestamp.isoformat() if last_run_timestamp else None,
            },
        }
