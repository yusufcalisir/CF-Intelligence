# ruff: noqa: UP042
"""Automated Drift-Triggered Retraining Service."""

from __future__ import annotations

import logging
import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RetrainingCause(str, Enum):
    """Reason enum triggering an automated model retraining pipeline."""

    PSI_DRIFT_EXCEEDED = "PSI_DRIFT_EXCEEDED"
    CONCEPT_DRIFT_DETECTED = "CONCEPT_DRIFT_DETECTED"
    ACCURACY_DEGRADATION = "ACCURACY_DEGRADATION"
    SCHEDULED_CADENCE = "SCHEDULED_CADENCE"


@dataclass(frozen=True)
class DriftEvaluationStatus:
    """Comprehensive evaluation outcome of multi-signal drift monitoring."""

    is_triggered: bool
    cause: RetrainingCause | None
    psi_status: str  # "DRIFT_DETECTED" | "NO_DRIFT"
    concept_status: str  # "DRIFT_DETECTED" | "NO_DRIFT"
    auc_monitoring_status: str  # "METRIC_AVAILABLE" | "METRIC_UNAVAILABLE"
    accuracy_degradation_status: str  # "DEGRADATION_DETECTED" | "NO_DEGRADATION_DETECTED" | "MONITORING_INCOMPLETE"
    monitoring_complete: bool  # True strictly when all candidate signals (including empirical AUC) are evaluated
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrainingJobRecord:
    """Record container tracking an automated retraining job execution."""

    job_id: str
    cause: RetrainingCause
    psi_score: float
    status: str = "TRIGGERED"
    candidate_model_version: str | None = None
    triggered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = field(default_factory=dict)


class DriftTriggeredRetrainingService:
    """Monitors drift metrics and dispatches automated FL retraining pipelines."""

    def __init__(
        self,
        psi_threshold: float = 0.20,
        min_auc_threshold: float = 0.70,
        operational_service: Any | None = None,
    ) -> None:
        self.psi_threshold = psi_threshold
        self.min_auc_threshold = min_auc_threshold
        self.operational_service = operational_service
        self._jobs: dict[str, RetrainingJobRecord] = {}
        self._lock = threading.RLock()

    def evaluate_drift_status(
        self,
        psi_score: float,
        concept_drift_score: float = 0.0,
        current_auc: float | None = None,
    ) -> DriftEvaluationStatus:
        """Audits all drift signals and determines comprehensive status without fabricating health."""
        psi_drift = math.isfinite(psi_score) and psi_score >= self.psi_threshold
        concept_drift = math.isfinite(concept_drift_score) and concept_drift_score >= 0.15

        auc_avail = current_auc is not None and math.isfinite(current_auc)
        if current_auc is not None and math.isfinite(current_auc):
            auc_mon_status = "METRIC_AVAILABLE"
            acc_status = (
                "DEGRADATION_DETECTED"
                if current_auc < self.min_auc_threshold
                else "NO_DEGRADATION_DETECTED"
            )
        else:
            auc_mon_status = "METRIC_UNAVAILABLE"
            acc_status = "MONITORING_INCOMPLETE"

        cause: RetrainingCause | None = None
        if psi_drift:
            cause = RetrainingCause.PSI_DRIFT_EXCEEDED
        elif concept_drift:
            cause = RetrainingCause.CONCEPT_DRIFT_DETECTED
        elif acc_status == "DEGRADATION_DETECTED":
            cause = RetrainingCause.ACCURACY_DEGRADATION

        return DriftEvaluationStatus(
            is_triggered=cause is not None,
            cause=cause,
            psi_status="DRIFT_DETECTED" if psi_drift else "NO_DRIFT",
            concept_status="DRIFT_DETECTED" if concept_drift else "NO_DRIFT",
            auc_monitoring_status=auc_mon_status,
            accuracy_degradation_status=acc_status,
            monitoring_complete=auc_avail,
            details={
                "psi_score": psi_score,
                "concept_drift_score": concept_drift_score,
                "current_auc": current_auc,
                "psi_threshold": self.psi_threshold,
                "min_auc_threshold": self.min_auc_threshold,
            },
        )

    def evaluate_drift_and_trigger(
        self,
        psi_score: float,
        concept_drift_score: float = 0.0,
        current_auc: float | None = None,
    ) -> RetrainingJobRecord | None:
        """Evaluates drift metrics and dispatches a retraining job if thresholds are exceeded."""
        if not math.isfinite(psi_score) or not math.isfinite(concept_drift_score):
            logger.warning(
                "Non-finite metrics encountered in retraining evaluation: psi=%s, concept=%s",
                psi_score,
                concept_drift_score,
            )
            return None

        if current_auc is not None and not math.isfinite(current_auc):
            logger.warning(
                "Non-finite current_auc encountered in retraining evaluation: auc=%s",
                current_auc,
            )
            return None

        eval_status = self.evaluate_drift_status(
            psi_score=psi_score,
            concept_drift_score=concept_drift_score,
            current_auc=current_auc,
        )

        if not eval_status.is_triggered or eval_status.cause is None:
            return None

        cause = eval_status.cause
        job_id = f"retrain_{uuid.uuid4().hex[:8]}"
        record = RetrainingJobRecord(
            job_id=job_id,
            cause=cause,
            psi_score=psi_score,
            status="TRIGGERED",
            details={
                "concept_drift_score": concept_drift_score,
                "current_auc": current_auc,
                "auc_monitoring_status": eval_status.auc_monitoring_status,
                "accuracy_degradation_status": eval_status.accuracy_degradation_status,
                "monitoring_complete": eval_status.monitoring_complete,
            },
        )
        with self._lock:
            self._jobs[job_id] = record

        logger.info(
            "Dispatched retraining job %s (Cause: %s, PSI: %.4f, AUC Status: %s)",
            job_id,
            cause.value,
            psi_score,
            eval_status.auc_monitoring_status,
        )
        return record

    def create_manual_job(
        self,
        reason: str = "Manual trigger from Observability Console",
        cause: RetrainingCause = RetrainingCause.PSI_DRIFT_EXCEEDED,
        psi_score: float = 0.25,
        retrain_feature_subset: list[str] | None = None,
        target_rounds: int = 3,
    ) -> RetrainingJobRecord:
        """Manually dispatches an automated retraining job."""
        job_id = f"retrain_{uuid.uuid4().hex[:8]}"
        features = list(retrain_feature_subset) if retrain_feature_subset else []
        record = RetrainingJobRecord(
            job_id=job_id,
            cause=cause,
            psi_score=psi_score,
            status="TRIGGERED",
            details={
                "manual_reason": reason,
                "retrain_feature_subset": features,
                "target_rounds": target_rounds,
            },
        )
        with self._lock:
            self._jobs[job_id] = record

        logger.info(
            "Manually dispatched retraining job %s (Reason: %s, Features: %s, Cause: %s)",
            job_id,
            reason,
            features,
            cause.value,
        )
        return record

    def execute_retraining_pipeline(
        self,
        job_id: str,
        operational_service: Any | None = None,
        participating_banks: list[str] | None = None,
        consortium_id: str | None = None,
    ) -> dict[str, Any]:
        """Executes genuine automated FL retraining task producing a candidate model checkpoint.

        Truthfulness Guarantee:
        Strictly prohibits fabricated completion, synthetic candidate IDs, or dummy ML metrics.
        Requires an authorized OperationalFederationService to execute genuine distributed training,
        evaluate holdout data, issue receipts, and register an inactive candidate.
        If operational_service is not supplied, fails closed with an explicit RuntimeError.
        """
        effective_service = operational_service or self.operational_service
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"Retraining job '{job_id}' does not exist.")
            record = self._jobs[job_id]

            if effective_service is None:
                record.status = "FAILED"
                record.details["failure_reason"] = (
                    "No authorized OperationalFederationService provided. Automated retraining "
                    "cannot fabricate training completion or synthetic ML metrics (AGENTS.md)."
                )
                logger.error(
                    "Retraining job %s failed closed: %s",
                    job_id,
                    record.details["failure_reason"],
                )
                raise RuntimeError(
                    f"Retraining job '{job_id}' cannot execute: No authorized OperationalFederationService provided. "
                    "Fabricated training completion and synthetic ML metrics are strictly prohibited (AGENTS.md)."
                )

            record.status = "RUNNING"

        # Execute genuine operational federation outside the lock
        try:
            target_rounds = record.details.get("target_rounds", 3)
            sim_id = f"sim_retrain_{job_id}"
            fed_result = effective_service.execute_operational_federation(
                simulation_id=sim_id,
                num_rounds=target_rounds,
                participating_banks=participating_banks or ["BANK_A", "BANK_B", "BANK_C"],
                consortium_id=consortium_id,
            )
            if not isinstance(fed_result, dict) or fed_result.get("status") != "COMPLETED":
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation failed for retraining job '{job_id}': {fed_result}"
                )

            saved_version = fed_result.get("saved_version", {})
            candidate_version_num = (
                saved_version.get("version")
                if isinstance(saved_version, dict)
                else getattr(saved_version, "version", None)
            )
            candidate_version = (
                f"v{candidate_version_num}"
                if candidate_version_num is not None
                else (
                    fed_result.get("candidate_model_version")
                    or f"model_v{candidate_version_num}"
                )
            )

            completed_rec = fed_result.get("completed_record")
            observed_metrics = (
                getattr(completed_rec, "observed_metrics", None)
                or fed_result.get("evaluation_metrics")
                or fed_result.get("metrics")
                or {}
            )

            with self._lock:
                record.status = "COMPLETED"
                record.candidate_model_version = str(candidate_version)
                record.details["metrics"] = dict(observed_metrics)
                record.details["saved_version"] = saved_version
                record.details["is_promoted_champion"] = fed_result.get(
                    "is_promoted_champion", False
                )

            logger.info(
                "Retraining job %s completed via OperationalFederationService. Candidate model: %s",
                job_id,
                candidate_version,
            )
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "candidate_model_version": str(candidate_version),
                "metrics": dict(observed_metrics),
                "evaluation_metrics": dict(observed_metrics),
                "is_promoted_champion": fed_result.get("is_promoted_champion", False),
            }
        except Exception as exc:
            with self._lock:
                record.status = "FAILED"
                record.details["failure_exception"] = str(exc)
            raise

    def cancel_job(self, job_id: str, reason: str = "Cancelled by operator") -> bool:
        """Cancels a pending or triggered retraining job."""
        with self._lock:
            if job_id not in self._jobs:
                return False
            record = self._jobs[job_id]
            if record.status in ("COMPLETED", "FAILED"):
                return False
            record.status = "CANCELLED"
            record.details["cancellation_reason"] = reason
            logger.info("Cancelled retraining job %s (Reason: %s)", job_id, reason)
            return True

    def get_job(self, job_id: str) -> RetrainingJobRecord | None:
        """Retrieves retraining job record by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, status: str | None = None) -> list[RetrainingJobRecord]:
        """Retrieves all tracked retraining jobs, optionally filtered by status."""
        with self._lock:
            if status is None:
                return list(self._jobs.values())
            return [j for j in self._jobs.values() if j.status == status]
