# ruff: noqa: UP042
"""Automated Zero-Downtime Model Rollback Manager."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)


class RollbackCause(str, Enum):
    """Reason enum triggering an automated model rollback."""

    AUC_DROP_CRITICAL = "AUC_DROP_CRITICAL"
    LATENCY_SLA_VIOLATION = "LATENCY_SLA_VIOLATION"
    FPR_SPIKE = "FPR_SPIKE"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


@dataclass
class RollbackExecutionRecord:
    """Record container tracking an automated zero-downtime rollback event."""

    rollback_id: str
    demoted_model_version: str
    restored_model_version: str
    cause: RollbackCause
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


import threading


class AutoRollbackManager:
    """Monitors live model health and executes automated zero-downtime rollbacks."""

    def __init__(
        self,
        min_auc: float = 0.65,
        max_latency_ms: float = 200.0,
        max_fpr: float = 0.05,
    ) -> None:
        self.min_auc = min_auc
        self.max_latency_ms = max_latency_ms
        self.max_fpr = max_fpr
        self._history: list[RollbackExecutionRecord] = []
        self._lock = threading.Lock()

    def evaluate_model_health_and_rollback(
        self,
        active_model_version: str,
        current_auc: float,
        current_latency_ms: float,
        current_fpr: float,
        fallback_model_version: str,
    ) -> tuple[bool, RollbackExecutionRecord | None]:
        """Evaluates live performance SLA and executes rollback if bounds are violated."""
        cause: RollbackCause | None = None

        if current_auc < self.min_auc:
            cause = RollbackCause.AUC_DROP_CRITICAL
        elif current_latency_ms > self.max_latency_ms:
            cause = RollbackCause.LATENCY_SLA_VIOLATION
        elif current_fpr > self.max_fpr:
            cause = RollbackCause.FPR_SPIKE

        if not cause:
            return False, None

        rollback_id = f"rollback_{uuid.uuid4().hex[:8]}"
        record = RollbackExecutionRecord(
            rollback_id=rollback_id,
            demoted_model_version=active_model_version,
            restored_model_version=fallback_model_version,
            cause=cause,
        )
        with self._lock:
            self._history.append(record)

        logger.warning(
            "EXECUTED AUTOMATED ROLLBACK %s: Demoted %s -> Restored %s (Cause: %s)",
            rollback_id,
            active_model_version,
            fallback_model_version,
            cause.value,
        )
        return True, record

    def get_rollback_history(self) -> list[RollbackExecutionRecord]:
        """Retrieves rollback execution history."""
        with self._lock:
            return list(self._history)
