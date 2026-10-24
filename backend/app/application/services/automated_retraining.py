# ruff: noqa: UP042
"""Automated Drift-Triggered Retraining Service."""

from __future__ import annotations

import logging
import math
import threading
import types
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
    accuracy_degradation_status: (
        str  # "DEGRADATION_DETECTED" | "NO_DEGRADATION_DETECTED" | "MONITORING_INCOMPLETE"
    )
    monitoring_complete: (
        bool  # True strictly when all candidate signals (including empirical AUC) are evaluated
    )
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
        feedback_pipeline: Any | None = None,
        min_feedback_samples: int = 0,
    ) -> None:
        self.psi_threshold = psi_threshold
        self.min_auc_threshold = min_auc_threshold
        self.operational_service = operational_service
        self.feedback_pipeline = feedback_pipeline
        self.min_feedback_samples = min_feedback_samples
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
        feedback_pipeline: Any | None = None,
        min_feedback_samples: int | None = None,
    ) -> dict[str, Any]:
        """Executes genuine automated FL retraining task producing a candidate model checkpoint.

        Truthfulness Guarantee:
        Strictly prohibits fabricated completion, synthetic candidate IDs, or dummy ML metrics.
        Requires an authorized OperationalFederationService to execute genuine distributed training,
        evaluate holdout data, issue receipts, and register an inactive candidate.
        If operational_service is not supplied, fails closed with an explicit RuntimeError.

        Staged Two-Phase Feedback Consumption:
        If a feedback pipeline is supplied or configured on the service:
        1. Validated feedback is reserved under this job_id for each participating bank.
        2. If verified training finishes successfully (invariants 1-5 pass), reserved feedback is committed.
        3. If training fails, throws an exception, or is cancelled, reserved feedback is released back to pool.
        """
        effective_service = operational_service or self.operational_service
        effective_pipeline = (
            feedback_pipeline if feedback_pipeline is not None else self.feedback_pipeline
        )
        effective_min = (
            min_feedback_samples if min_feedback_samples is not None else self.min_feedback_samples
        )
        banks = list(participating_banks) if participating_banks else ["BANK_A", "BANK_B", "BANK_C"]

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

            # Two-phase feedback reservation
            reserved_banks: list[str] = []
            feedback_manifests: dict[str, dict[str, Any]] = {}
            total_reserved = 0
            if effective_pipeline is not None:
                for bank in banks:
                    res_batch = effective_pipeline.reserve_retraining_batch(
                        tenant_id=bank,
                        job_id=job_id,
                        batch_size=32,
                        min_priority=1,
                        stratified=True,
                        require_validated=True,
                    )
                    count = res_batch.get("batch_size", 0)
                    if effective_min > 0 and count < effective_min:
                        # Insufficient verified data -> release any reservations and fail closed
                        for b in reserved_banks:
                            effective_pipeline.release_reserved_feedback(tenant_id=b, job_id=job_id)
                        effective_pipeline.release_reserved_feedback(tenant_id=bank, job_id=job_id)
                        record.status = "FAILED"
                        record.details["failure_reason"] = (
                            f"Insufficient verified feedback data for bank '{bank}': "
                            f"got {count}, minimum required {effective_min}."
                        )
                        logger.error(
                            "Retraining job %s failed closed: %s",
                            job_id,
                            record.details["failure_reason"],
                        )
                        raise RuntimeError(
                            f"Retraining job '{job_id}' cannot execute: Insufficient verified feedback data for bank '{bank}' "
                            f"({count} < {effective_min} minimum required)."
                        )
                    if count > 0:
                        reserved_banks.append(bank)
                        total_reserved += count
                        feedback_manifests[bank] = {
                            "job_id": job_id,
                            "bank_id": bank,
                            "sample_count": count,
                            "sample_hashes": [
                                it["transaction_id_hash"] for it in res_batch.get("items", [])
                            ],
                        }

            record.status = "RUNNING"
            record.details["participating_banks"] = banks

        # Execute genuine operational federation outside the lock
        try:
            target_rounds = record.details.get("target_rounds", 3)
            sim_id = f"sim_retrain_{job_id}"
            fed_kwargs: dict[str, Any] = {
                "job_id": job_id,
                "simulation_id": sim_id,
                "num_rounds": target_rounds,
                "participating_banks": banks,
                "consortium_id": consortium_id,
            }
            if feedback_manifests:
                fed_kwargs["feedback_manifests"] = feedback_manifests

            fed_result = effective_service.execute_operational_federation(**fed_kwargs)
            if not isinstance(fed_result, dict):
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation failed for retraining job '{job_id}': result must be a dict, got {type(fed_result)}."
                )

            # Invariant 1: status == "COMPLETED"
            if fed_result.get("status") != "COMPLETED":
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation failed for retraining job '{job_id}': status is not COMPLETED ({fed_result.get('status')})."
                )

            # Invariant 2: is_verified == True
            if fed_result.get("is_verified") is not True:
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation failed for retraining job '{job_id}': execution evidence is unverified (is_verified is not True)."
                )

            # Invariant 3: real, valid candidate model version
            saved_version = fed_result.get("saved_version")
            candidate_version_str: str | None = None
            if isinstance(saved_version, dict) and saved_version.get("version") is not None:
                candidate_version_str = f"v{saved_version['version']}"
            elif fed_result.get("candidate_model_version"):
                candidate_version_str = str(fed_result["candidate_model_version"]).strip()
            elif isinstance(saved_version, dict) and saved_version.get("filename"):
                candidate_version_str = str(saved_version["filename"]).strip()

            if (
                not candidate_version_str
                or candidate_version_str in ("None", "vNone", "model_vNone", "")
                or "None" in candidate_version_str
            ):
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation for retraining job '{job_id}' did not produce a valid candidate model version: {candidate_version_str!r}."
                )

            # Invariant 4: verified execution metrics (non-empty dict with finite numeric metrics)
            completed_rec = fed_result.get("completed_record")
            raw_metrics = (
                getattr(completed_rec, "observed_metrics", None)
                or fed_result.get("evaluation_metrics")
                or fed_result.get("metrics")
            )
            if not isinstance(raw_metrics, (dict, types.MappingProxyType)) or not raw_metrics:
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation for retraining job '{job_id}' missing verified execution metrics."
                )

            observed_metrics: dict[str, float] = {}
            for k, v in raw_metrics.items():
                if isinstance(v, (int, float)) and math.isfinite(v):
                    observed_metrics[str(k)] = float(v)
            if not observed_metrics:
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation for retraining job '{job_id}' produced no finite numeric metrics: {raw_metrics}."
                )

            # Invariant 5: no automatic champion promotion
            is_promoted = fed_result.get("is_promoted_champion", False)
            if is_promoted is True:
                with self._lock:
                    record.status = "FAILED"
                    record.details["operational_result"] = fed_result
                raise RuntimeError(
                    f"Operational federation for retraining job '{job_id}' violated Four-Eyes governance: automated retraining cannot automatically promote candidate to champion."
                )

            # Commit reserved feedback ONLY upon verified training execution evidence matching this job
            has_feedback_reservation = bool(reserved_banks and total_reserved > 0)
            lineage_verified = False
            if has_feedback_reservation:
                job_matches = fed_result.get("job_id") == job_id
                fl_verified = fed_result.get("feedback_lineage_verified") is True
                verified_jobs = fed_result.get("verified_feedback_jobs") or []
                if job_matches and fl_verified and (job_id in verified_jobs):
                    lineage_verified = True
                else:
                    lineage_verified = False
                    logger.warning(
                        "Retraining job %s completed, but feedback lineage was NOT verified. "
                        "Reserved feedback will NOT be consumed (retained as unconsumed). "
                        "Reason: job_matches=%s, fl_verified=%s, verified_jobs=%s",
                        job_id,
                        job_matches,
                        fl_verified,
                        verified_jobs,
                    )

            if has_feedback_reservation:
                if lineage_verified and effective_pipeline is not None:
                    total_committed = 0
                    committed_banks: list[str] = []
                    failed_banks: list[str] = []
                    for b in reserved_banks:
                        try:
                            c = effective_pipeline.commit_consumed_feedback(
                                tenant_id=b,
                                job_id=job_id,
                                training_run_id=candidate_version_str,
                            )
                            total_committed += c
                            committed_banks.append(b)
                        except Exception as c_exc:
                            logger.error("Failed to commit consumed feedback for bank %s: %s", b, c_exc)
                            failed_banks.append(b)
                    record.details["feedback_consumed"] = True
                    record.details["committed_feedback_samples"] = total_committed
                    record.details["committed_banks"] = committed_banks
                    if failed_banks:
                        record.details["failed_commit_banks"] = failed_banks
                else:
                    # Retain feedback as unconsumed by safely releasing reservations
                    if effective_pipeline is not None:
                        for b in reserved_banks:
                            effective_pipeline.release_reserved_feedback(tenant_id=b, job_id=job_id)
                    record.details["feedback_consumed"] = False
                    record.details["committed_feedback_samples"] = 0
                    record.details["feedback_lineage_unverified_reason"] = (
                        f"Operational federation result (job_id={fed_result.get('job_id')}) did not provide "
                        f"verified feedback lineage evidence for job '{job_id}'."
                    )
            else:
                record.details["feedback_consumed"] = False
                record.details["committed_feedback_samples"] = 0

            with self._lock:
                record.status = "COMPLETED"
                record.candidate_model_version = candidate_version_str
                record.details["metrics"] = dict(observed_metrics)
                record.details["saved_version"] = saved_version
                record.details["is_promoted_champion"] = False

            logger.info(
                "Retraining job %s completed via OperationalFederationService. Candidate model: %s",
                job_id,
                candidate_version_str,
            )
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "candidate_model_version": candidate_version_str,
                "metrics": dict(observed_metrics),
                "evaluation_metrics": dict(observed_metrics),
                "is_promoted_champion": False,
                "feedback_consumed": record.details.get("feedback_consumed", False),
                "committed_feedback_samples": record.details.get("committed_feedback_samples", 0),
                "committed_banks": record.details.get("committed_banks", []),
                "failed_commit_banks": record.details.get("failed_commit_banks", []),
                "feedback_lineage_unverified_reason": record.details.get("feedback_lineage_unverified_reason"),
            }
        except Exception as exc:
            # Release any reserved feedback on failure
            if effective_pipeline is not None:
                for b in banks:
                    try:
                        effective_pipeline.release_reserved_feedback(tenant_id=b, job_id=job_id)
                    except Exception as rel_exc:
                        logger.warning(
                            "Failed to release reserved feedback for bank %s: %s", b, rel_exc
                        )
            with self._lock:
                record.status = "FAILED"
                record.details["failure_exception"] = str(exc)
            raise

    def cancel_job(self, job_id: str, reason: str = "Cancelled by operator") -> bool:
        """Cancels a pending or triggered retraining job and releases reserved feedback."""
        with self._lock:
            if job_id not in self._jobs:
                return False
            record = self._jobs[job_id]
            if record.status in ("COMPLETED", "FAILED"):
                return False
            record.status = "CANCELLED"
            record.details["cancellation_reason"] = reason

            if self.feedback_pipeline is not None:
                banks = record.details.get("participating_banks") or ["BANK_A", "BANK_B", "BANK_C"]
                for b in banks:
                    try:
                        self.feedback_pipeline.release_reserved_feedback(tenant_id=b, job_id=job_id)
                    except Exception as rel_exc:
                        logger.warning(
                            "Failed to release reserved feedback on cancel for bank %s: %s",
                            b,
                            rel_exc,
                        )

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
