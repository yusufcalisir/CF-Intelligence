"""Enterprise Observability & Model Drift Monitoring Endpoints.

Exposes statistical feature drift analysis (KS-test, Wasserstein, PSI),
model calibration (Brier Score, ECE), Alertmanager active alerts webhook & feed,
and automated re-training triggers.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.application.services.automated_retraining import (
    DriftTriggeredRetrainingService,
    RetrainingCause,
)
from app.application.services.drift_service import ModelDriftService
from app.infrastructure import telemetry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])

_drift_service = ModelDriftService()
_retraining_service = DriftTriggeredRetrainingService()

# ── Isolated Pseudo-Random Generator (Zero Global Seed Mutation) ──
_rng = np.random.default_rng(42)
_ref_amount = _rng.normal(loc=150.0, scale=45.0, size=200).tolist()
_ref_velocity = _rng.exponential(scale=2.5, size=200).tolist()
_ref_risk_score = _rng.beta(a=1.5, b=5.0, size=200).tolist()

# Default current (mild/moderate drift)
_curr_amount = _rng.normal(loc=280.0, scale=80.0, size=200).tolist()
_curr_velocity = _rng.exponential(scale=4.2, size=200).tolist()
_curr_risk_score = _rng.beta(a=2.5, b=3.5, size=200).tolist()

# Labels & probabilities for calibration
_sample_labels = [0] * 160 + [1] * 40
_sample_probs = (
    _rng.uniform(0.0, 0.4, size=160).tolist() + _rng.uniform(0.6, 0.95, size=40).tolist()
)


# ── Schemas ───────────────────────────────────────────────────


class FeatureDriftResponse(BaseModel):
    feature_name: str
    ks_statistic: float
    ks_p_value: float
    wasserstein_distance: float
    psi: float
    status: str


class CalibrationBinResponse(BaseModel):
    bin_index: int
    prob_min: float
    prob_max: float
    mean_predicted_prob: float
    empirical_fraud_ratio: float
    sample_count: int


class CalibrationResponse(BaseModel):
    brier_score: float
    expected_calibration_error: float
    max_calibration_error: float
    is_well_calibrated: bool
    evaluated_at: str
    bins: list[CalibrationBinResponse]


class DriftAnalysisResponse(BaseModel):
    overall_status: str
    max_psi: float
    mean_ks_p_value: float
    concept_drift_psi: float
    auto_retrain_triggered: bool
    evaluated_at: str
    feature_drifts: list[FeatureDriftResponse]
    calibration: CalibrationResponse | None = None


class ActiveAlertResponse(BaseModel):
    alert_name: str
    severity: str
    summary: str
    started_at: str
    status: str


class RetrainTriggerResponse(BaseModel):
    triggered: bool
    reason: str
    new_simulation_id: str | None = None
    triggered_at: str


class DriftEvaluationRequest(BaseModel):
    current_features: dict[str, list[float]]
    reference_features: dict[str, list[float]]
    current_risk_scores: list[float]
    reference_risk_scores: list[float]
    ground_truth_labels: list[int] | None = None
    predicted_probabilities: list[float] | None = None


class AlertmanagerAlert(BaseModel):
    status: str = "firing"  # "firing" | "resolved"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None
    endsAt: str | None = None


class AlertmanagerWebhookPayload(BaseModel):
    version: str | None = "4"
    groupKey: str | None = None
    status: str = "firing"
    receiver: str | None = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class RetrainingJobResponse(BaseModel):
    job_id: str
    cause: str
    psi_score: float
    status: str
    candidate_model_version: str | None = None
    triggered_at: str
    details: dict[str, Any] = Field(default_factory=dict)


# ── Thread-Safe Alert Store ───────────────────────────────────


class AlertStore:
    """Thread-safe storage for active and resolved Prometheus Alertmanager alerts."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._alerts: dict[str, ActiveAlertResponse] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._alerts["SignificantConceptDrift"] = ActiveAlertResponse(
            alert_name="SignificantConceptDrift",
            severity="warning",
            summary="Concept drift PSI exceeded 0.10 warning threshold (PSI=0.142).",
            started_at=now_str,
            status="firing",
        )
        self._alerts["HighGatewayLatency"] = ActiveAlertResponse(
            alert_name="HighGatewayLatency",
            severity="info",
            summary="API Gateway 95th percentile latency elevated (105ms).",
            started_at=now_str,
            status="resolved",
        )

    def record_alert(self, alert: ActiveAlertResponse) -> None:
        with self._lock:
            self._alerts[alert.alert_name] = alert

    def resolve_alert(self, alert_name: str) -> bool:
        with self._lock:
            if alert_name in self._alerts:
                self._alerts[alert_name].status = "resolved"
                return True
            return False

    def list_alerts(self, status_filter: str | None = None) -> list[ActiveAlertResponse]:
        with self._lock:
            if status_filter is None:
                return list(self._alerts.values())
            return [a for a in self._alerts.values() if a.status == status_filter]

    def clear(self) -> None:
        with self._lock:
            self._alerts.clear()
            self._init_defaults()


_alert_store = AlertStore()


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/drift/analyze", response_model=DriftAnalysisResponse)
async def analyze_model_drift(severe_drift: bool = False) -> DriftAnalysisResponse:
    """Execute statistical Feature Drift and Concept Drift analysis against reference baselines."""
    # Use isolated RNG to prevent global process seed pollution
    curr_amt = (
        _rng.normal(loc=450.0, scale=120.0, size=200).tolist()
        if severe_drift
        else _curr_amount
    )
    curr_vel = (
        _rng.exponential(scale=6.8, size=200).tolist() if severe_drift else _curr_velocity
    )
    curr_risk = (
        _rng.beta(a=4.0, b=1.5, size=200).tolist() if severe_drift else _curr_risk_score
    )

    current_data = {
        "transaction_amount": curr_amt,
        "velocity_1h": curr_vel,
        "device_risk_index": _rng.uniform(0.1, 0.9, size=200).tolist(),
    }
    reference_data = {
        "transaction_amount": _ref_amount,
        "velocity_1h": _ref_velocity,
        "device_risk_index": _rng.uniform(0.1, 0.9, size=200).tolist(),
    }

    rpt = _drift_service.run_full_drift_analysis(
        current_data=current_data,
        reference_data=reference_data,
        current_scores=curr_risk,
        reference_scores=_ref_risk_score,
        y_true=_sample_labels,
        y_prob=_sample_probs,
    )

    # Record metrics in Prometheus gauges
    telemetry.cfi_concept_drift_psi.record(rpt.concept_drift_psi)
    if rpt.feature_drifts:
        telemetry.cfi_feature_drift_ks_stat.record(
            max(fd.ks_statistic for fd in rpt.feature_drifts)
        )
    if rpt.calibration:
        telemetry.cfi_model_brier_score.record(rpt.calibration.brier_score)
        telemetry.cfi_model_ece.record(rpt.calibration.expected_calibration_error)

    # Dynamically register alert if drift thresholds are breached
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if rpt.overall_status in ("WARNING", "CRITICAL"):
        _alert_store.record_alert(
            ActiveAlertResponse(
                alert_name="SignificantConceptDrift",
                severity="critical" if rpt.overall_status == "CRITICAL" else "warning",
                summary=f"Concept drift PSI ({rpt.concept_drift_psi:.4f}) reached {rpt.overall_status} threshold.",
                started_at=now_str,
                status="firing",
            )
        )

    calib_resp = None
    if rpt.calibration:
        calib_resp = CalibrationResponse(
            brier_score=rpt.calibration.brier_score,
            expected_calibration_error=rpt.calibration.expected_calibration_error,
            max_calibration_error=rpt.calibration.max_calibration_error,
            is_well_calibrated=rpt.calibration.is_well_calibrated,
            evaluated_at=rpt.calibration.evaluated_at,
            bins=[
                CalibrationBinResponse(
                    bin_index=b.bin_index,
                    prob_min=b.prob_min,
                    prob_max=b.prob_max,
                    mean_predicted_prob=b.mean_predicted_prob,
                    empirical_fraud_ratio=b.empirical_fraud_ratio,
                    sample_count=b.sample_count,
                )
                for b in rpt.calibration.bins
            ],
        )

    return DriftAnalysisResponse(
        overall_status=rpt.overall_status,
        max_psi=rpt.max_psi,
        mean_ks_p_value=rpt.mean_ks_p_value,
        concept_drift_psi=rpt.concept_drift_psi,
        auto_retrain_triggered=rpt.auto_retrain_triggered,
        evaluated_at=rpt.evaluated_at,
        feature_drifts=[
            FeatureDriftResponse(
                feature_name=fd.feature_name,
                ks_statistic=fd.ks_statistic,
                ks_p_value=fd.ks_p_value,
                wasserstein_distance=fd.wasserstein_distance,
                psi=fd.psi,
                status=fd.status,
            )
            for fd in rpt.feature_drifts
        ],
        calibration=calib_resp,
    )


@router.post("/drift/evaluate", response_model=DriftAnalysisResponse)
async def evaluate_live_drift(request: DriftEvaluationRequest) -> DriftAnalysisResponse:
    """Evaluate drift metrics on live user/consortium supplied feature distributions."""
    rpt = _drift_service.run_full_drift_analysis(
        current_data=request.current_features,
        reference_data=request.reference_features,
        current_scores=request.current_risk_scores,
        reference_scores=request.reference_risk_scores,
        y_true=request.ground_truth_labels,
        y_prob=request.predicted_probabilities,
    )

    # Record metrics in Prometheus
    telemetry.cfi_concept_drift_psi.record(rpt.concept_drift_psi)
    if rpt.feature_drifts:
        telemetry.cfi_feature_drift_ks_stat.record(
            max(fd.ks_statistic for fd in rpt.feature_drifts)
        )
    if rpt.calibration:
        telemetry.cfi_model_brier_score.record(rpt.calibration.brier_score)
        telemetry.cfi_model_ece.record(rpt.calibration.expected_calibration_error)

    calib_resp = None
    if rpt.calibration:
        calib_resp = CalibrationResponse(
            brier_score=rpt.calibration.brier_score,
            expected_calibration_error=rpt.calibration.expected_calibration_error,
            max_calibration_error=rpt.calibration.max_calibration_error,
            is_well_calibrated=rpt.calibration.is_well_calibrated,
            evaluated_at=rpt.calibration.evaluated_at,
            bins=[
                CalibrationBinResponse(
                    bin_index=b.bin_index,
                    prob_min=b.prob_min,
                    prob_max=b.prob_max,
                    mean_predicted_prob=b.mean_predicted_prob,
                    empirical_fraud_ratio=b.empirical_fraud_ratio,
                    sample_count=b.sample_count,
                )
                for b in rpt.calibration.bins
            ],
        )

    return DriftAnalysisResponse(
        overall_status=rpt.overall_status,
        max_psi=rpt.max_psi,
        mean_ks_p_value=rpt.mean_ks_p_value,
        concept_drift_psi=rpt.concept_drift_psi,
        auto_retrain_triggered=rpt.auto_retrain_triggered,
        evaluated_at=rpt.evaluated_at,
        feature_drifts=[
            FeatureDriftResponse(
                feature_name=fd.feature_name,
                ks_statistic=fd.ks_statistic,
                ks_p_value=fd.ks_p_value,
                wasserstein_distance=fd.wasserstein_distance,
                psi=fd.psi,
                status=fd.status,
            )
            for fd in rpt.feature_drifts
        ],
        calibration=calib_resp,
    )


@router.get("/calibration", response_model=CalibrationResponse)
async def get_calibration_report() -> CalibrationResponse:
    """Get model probability calibration report and reliability curve points."""
    cal = _drift_service.compute_calibration(_sample_labels, _sample_probs)
    return CalibrationResponse(
        brier_score=cal.brier_score,
        expected_calibration_error=cal.expected_calibration_error,
        max_calibration_error=cal.max_calibration_error,
        is_well_calibrated=cal.is_well_calibrated,
        evaluated_at=cal.evaluated_at,
        bins=[
            CalibrationBinResponse(
                bin_index=b.bin_index,
                prob_min=b.prob_min,
                prob_max=b.prob_max,
                mean_predicted_prob=b.mean_predicted_prob,
                empirical_fraud_ratio=b.empirical_fraud_ratio,
                sample_count=b.sample_count,
            )
            for b in cal.bins
        ],
    )


@router.get("/alerts", response_model=list[ActiveAlertResponse])
async def list_active_alerts() -> list[ActiveAlertResponse]:
    """Get active and firing Prometheus Alertmanager alerts."""
    return _alert_store.list_alerts()


@router.post("/alerts", response_model=ActiveAlertResponse, status_code=status.HTTP_201_CREATED)
async def record_custom_alert(alert: ActiveAlertResponse) -> ActiveAlertResponse:
    """Record a new active alert into the system alert store."""
    _alert_store.record_alert(alert)
    return alert


@router.post("/alerts/webhook")
async def receive_alertmanager_webhook(payload: AlertmanagerWebhookPayload) -> dict[str, Any]:
    """Prometheus Alertmanager Webhook Receiver.

    Receives alerts fired or resolved by Alertmanager daemon and updates active alert state.
    """
    processed = 0
    for alert in payload.alerts:
        alert_name = alert.labels.get("alertname", "UnknownAlert")
        severity = alert.labels.get("severity", "warning").lower()
        summary = alert.annotations.get(
            "summary", alert.annotations.get("description", f"Alert {alert_name} status: {alert.status}")
        )
        started_at = alert.startsAt or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        if alert.status == "resolved":
            resolved = _alert_store.resolve_alert(alert_name)
            if not resolved:
                _alert_store.record_alert(
                    ActiveAlertResponse(
                        alert_name=alert_name,
                        severity=severity,
                        summary=summary,
                        started_at=started_at,
                        status="resolved",
                    )
                )
        else:
            _alert_store.record_alert(
                ActiveAlertResponse(
                    alert_name=alert_name,
                    severity=severity,
                    summary=summary,
                    started_at=started_at,
                    status="firing",
                )
            )
        processed += 1

    logger.info("Processed %d Alertmanager webhook alerts (Group: %s)", processed, payload.groupKey)
    return {"status": "ok", "alerts_processed": processed}


@router.post("/drift/trigger-retrain", response_model=RetrainTriggerResponse)
async def trigger_automated_retraining(
    reason: str = "Concept Drift PSI > 0.20 threshold exceeded",
) -> RetrainTriggerResponse:
    """Trigger an automated federated re-training round in response to concept drift."""
    job = _retraining_service.create_manual_job(
        reason=reason,
        cause=RetrainingCause.PSI_DRIFT_EXCEEDED,
        psi_score=0.25,
    )
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    logger.info("Automated re-training round initiated: %s (Reason: %s)", job.job_id, reason)

    return RetrainTriggerResponse(
        triggered=True,
        reason=reason,
        new_simulation_id=job.job_id,
        triggered_at=now_str,
    )


@router.get("/retraining/jobs", response_model=list[RetrainingJobResponse])
async def list_retraining_jobs(status_filter: str | None = None) -> list[RetrainingJobResponse]:
    """List all tracked automated retraining jobs."""
    jobs = _retraining_service.list_jobs(status=status_filter)
    return [
        RetrainingJobResponse(
            job_id=j.job_id,
            cause=j.cause.value,
            psi_score=j.psi_score,
            status=j.status,
            candidate_model_version=j.candidate_model_version,
            triggered_at=j.triggered_at.strftime("%Y-%m-%d %H:%M:%SZ"),
            details=j.details,
        )
        for j in jobs
    ]


@router.get("/retraining/jobs/{job_id}", response_model=RetrainingJobResponse)
async def get_retraining_job(job_id: str) -> RetrainingJobResponse:
    """Get details of a specific automated retraining job."""
    job = _retraining_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Retraining job '{job_id}' not found")
    return RetrainingJobResponse(
        job_id=job.job_id,
        cause=job.cause.value,
        psi_score=job.psi_score,
        status=job.status,
        candidate_model_version=job.candidate_model_version,
        triggered_at=job.triggered_at.strftime("%Y-%m-%d %H:%M:%SZ"),
        details=job.details,
    )
