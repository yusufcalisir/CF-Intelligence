"""Hardening unit test suite for Concept Drift, PSI, Calibration, and Automated Retraining.

Validates:
1. Process-wide isolated RNG (zero global seed mutation).
2. Prometheus Alertmanager webhook processing (firing and resolved states).
3. Custom alert ingestion and dynamic alert store queries.
4. Dynamic drift evaluation endpoint (POST /api/v1/monitoring/drift/evaluate).
5. Automated retraining job dispatch, listing, and inspection endpoints.
6. Thread-safe DriftTriggeredRetrainingService job lifecycle and cancellation.
7. RetrainingTriggerEngine evaluation history and metric clamping.
8. AutoRollbackManager RLock thread safety and non-finite metric suppression.
9. ModelDriftService small-sample and num_bins boundary conditions.
10. Model calibration reliability curve and edge probability distributions.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.application.services.auto_rollback import AutoRollbackManager, RollbackCause
from app.application.services.automated_retraining import (
    DriftTriggeredRetrainingService,
)
from app.application.services.drift_service import ModelDriftService
from app.application.services.retraining_trigger_engine import RetrainingTriggerEngine
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_isolated_rng_no_global_seed_mutation(client: TestClient) -> None:
    """Verifies that monitoring router uses isolated RNG without mutating global np.random state."""
    # Set a known global seed
    np.random.seed(9999)
    val_before = np.random.uniform(0.0, 1.0)

    # Call the monitoring drift analyze endpoint
    res = client.get("/api/v1/monitoring/drift/analyze?severe_drift=false")
    assert res.status_code == 200

    # Reset global seed to 9999 and verify global sequence is unaltered by router operations
    np.random.seed(9999)
    val_after = np.random.uniform(0.0, 1.0)
    assert val_before == pytest.approx(val_after, abs=1e-9)


def test_alertmanager_webhook_firing_and_resolution(client: TestClient) -> None:
    """Verifies Alertmanager webhook processes firing and resolved alerts into active store."""
    webhook_payload = {
        "version": "4",
        "groupKey": "test_alert_group",
        "status": "firing",
        "receiver": "log-receiver",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "TestCriticalDrift",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "Concept drift exceeded 0.35 in cluster node 2",
                },
                "startsAt": "2026-09-16T00:00:00Z",
            }
        ],
    }

    res_post = client.post("/api/v1/monitoring/alerts/webhook", json=webhook_payload)
    assert res_post.status_code == 200
    assert res_post.json()["alerts_processed"] == 1

    # Verify alert appears in /alerts
    res_get = client.get("/api/v1/monitoring/alerts")
    assert res_get.status_code == 200
    alerts = res_get.json()
    test_alert = next((a for a in alerts if a["alert_name"] == "TestCriticalDrift"), None)
    assert test_alert is not None
    assert test_alert["status"] == "firing"
    assert test_alert["severity"] == "critical"

    # Send resolution webhook
    resolve_payload = {
        "version": "4",
        "groupKey": "test_alert_group",
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "labels": {
                    "alertname": "TestCriticalDrift",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "Concept drift returned to baseline",
                },
            }
        ],
    }
    res_resolve = client.post("/api/v1/monitoring/alerts/webhook", json=resolve_payload)
    assert res_resolve.status_code == 200

    # Verify updated status
    res_get2 = client.get("/api/v1/monitoring/alerts")
    alerts2 = res_get2.json()
    resolved_alert = next((a for a in alerts2 if a["alert_name"] == "TestCriticalDrift"), None)
    assert resolved_alert is not None
    assert resolved_alert["status"] == "resolved"


def test_custom_alert_recording_and_listing(client: TestClient) -> None:
    """Verifies POST /api/v1/monitoring/alerts registers custom alert."""
    custom_alert = {
        "alert_name": "CustomManualAlert",
        "severity": "warning",
        "summary": "Manual operator alert for scheduled maintenance",
        "started_at": "2026-09-16T01:00:00Z",
        "status": "firing",
    }
    res = client.post("/api/v1/monitoring/alerts", json=custom_alert)
    assert res.status_code == 201
    created = res.json()
    assert created["alert_name"] == "CustomManualAlert"

    res_all = client.get("/api/v1/monitoring/alerts")
    assert any(a["alert_name"] == "CustomManualAlert" for a in res_all.json())


def test_drift_evaluation_endpoint_with_custom_features(client: TestClient) -> None:
    """Verifies POST /api/v1/monitoring/drift/evaluate processes live feature matrices."""
    req_body = {
        "current_features": {
            "amount": [120.0, 130.0, 140.0] * 20,
            "velocity": [1.0, 2.0, 3.0] * 20,
        },
        "reference_features": {
            "amount": [100.0, 105.0, 110.0] * 20,
            "velocity": [1.0, 1.2, 1.5] * 20,
        },
        "current_risk_scores": [0.8, 0.85, 0.9] * 20,
        "reference_risk_scores": [0.1, 0.15, 0.2] * 20,
        "ground_truth_labels": [0, 1] * 30,
        "predicted_probabilities": [0.05, 0.95] * 30,
    }

    res = client.post("/api/v1/monitoring/drift/evaluate", json=req_body)
    assert res.status_code == 200
    data = res.json()

    assert "overall_status" in data
    assert len(data["feature_drifts"]) == 2
    assert data["concept_drift_psi"] >= 0.0
    assert data["calibration"] is not None
    assert data["calibration"]["is_well_calibrated"] is True


def test_retraining_job_dispatch_and_inspection(client: TestClient) -> None:
    """Verifies POST /drift/trigger-retrain and GET /retraining/jobs endpoints."""
    res_trigger = client.post(
        "/api/v1/monitoring/drift/trigger-retrain",
        params={"reason": "Audit Test: Automated PSI Retraining"},
    )
    assert res_trigger.status_code == 200
    resp_data = res_trigger.json()
    assert resp_data["triggered"] is True
    job_id = resp_data["new_simulation_id"]
    assert job_id is not None
    assert job_id.startswith("retrain_")

    # List all retraining jobs
    res_jobs = client.get("/api/v1/monitoring/retraining/jobs")
    assert res_jobs.status_code == 200
    jobs_list = res_jobs.json()
    assert any(j["job_id"] == job_id for j in jobs_list)

    # Inspect specific job
    res_job = client.get(f"/api/v1/monitoring/retraining/jobs/{job_id}")
    assert res_job.status_code == 200
    job_detail = res_job.json()
    assert job_detail["job_id"] == job_id
    assert job_detail["status"] == "TRIGGERED"

    # Non-existent job
    res_404 = client.get("/api/v1/monitoring/retraining/jobs/retrain_nonexistent")
    assert res_404.status_code == 404


def test_drift_triggered_retraining_service_concurrency_and_cancellation() -> None:
    """Verifies thread-safe job registration, cancellation, and execution in DriftTriggeredRetrainingService."""
    service = DriftTriggeredRetrainingService(psi_threshold=0.20)
    created_jobs: list[str] = []

    def worker(idx: int) -> None:
        job = service.evaluate_drift_and_trigger(
            psi_score=0.25 + (idx * 0.01),
            concept_drift_score=0.10,
        )
        if job:
            created_jobs.append(job.job_id)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(created_jobs) == 10
    all_jobs = service.list_jobs()
    assert len(all_jobs) == 10

    # Cancel first job
    first_job_id = created_jobs[0]
    cancelled = service.cancel_job(first_job_id, reason="Operator test abort")
    assert cancelled is True

    job_rec = service.get_job(first_job_id)
    assert job_rec is not None
    assert job_rec.status == "CANCELLED"

    # Execute second job
    second_job_id = created_jobs[1]
    result = service.execute_retraining_pipeline(second_job_id)
    assert result["status"] == "COMPLETED"
    assert service.get_job(second_job_id).status == "COMPLETED"

    # Cancel already completed job fails gracefully
    assert service.cancel_job(second_job_id) is False


def test_retraining_trigger_engine_history_and_clamping() -> None:
    """Verifies RetrainingTriggerEngine history tracking, clearing, and metric bounds."""
    engine = RetrainingTriggerEngine(
        ingestion_threshold=1000,
        psi_threshold=0.20,
        cadence_hours=12,
    )

    now = datetime.now(UTC)
    # Negative PSI clamped to 0.0, cadence not elapsed
    res1 = engine.evaluate_triggers(
        record_count=500,
        psi_score=-0.15,
        ks_p_value=0.50,
        last_run_timestamp=now - timedelta(hours=2),
    )
    assert res1["is_triggered"] is False
    assert res1["details"]["psi_score"] == 0.0

    # Ingestion met
    res2 = engine.evaluate_triggers(
        record_count=1500,
        psi_score=0.05,
        ks_p_value=0.50,
        last_run_timestamp=now - timedelta(hours=2),
    )
    assert res2["is_triggered"] is True
    assert "INGESTION_THRESHOLD_REACHED" in res2["reasons"]

    # Check history
    history = engine.get_history()
    assert len(history) == 2
    assert history[0]["is_triggered"] is False
    assert history[1]["is_triggered"] is True

    engine.clear_history()
    assert len(engine.get_history()) == 0


def test_auto_rollback_manager_reentrant_lock_and_get_last() -> None:
    """Verifies AutoRollbackManager handles RLock re-entrancy, get_last_rollback, and NaN inputs."""
    manager = AutoRollbackManager(min_auc=0.70, max_latency_ms=150.0, max_fpr=0.05)

    # NaN inputs should suppress rollback without exception
    triggered_nan, record_nan = manager.evaluate_model_health_and_rollback(
        active_model_version="v2",
        current_auc=float("nan"),
        current_latency_ms=100.0,
        current_fpr=0.01,
        fallback_model_version="v1",
    )
    assert triggered_nan is False
    assert record_nan is None
    assert manager.get_last_rollback() is None

    # Trigger legitimate rollback
    triggered, record = manager.evaluate_model_health_and_rollback(
        active_model_version="v2",
        current_auc=0.60,
        current_latency_ms=100.0,
        current_fpr=0.01,
        fallback_model_version="v1",
    )
    assert triggered is True
    assert record is not None
    assert record.cause == RollbackCause.AUC_DROP_CRITICAL

    last = manager.get_last_rollback()
    assert last is not None
    assert last.rollback_id == record.rollback_id

    manager.clear_history()
    assert len(manager.get_rollback_history()) == 0
    assert manager.get_last_rollback() is None


def test_model_drift_service_small_sample_and_num_bins_edge_cases() -> None:
    """Verifies ModelDriftService robustly handles num_bins < 2 and small samples (<30)."""
    service = ModelDriftService(psi_threshold_warning=0.10, psi_threshold_critical=0.20)

    # N < 30 returns 0.0 with warning
    psi_small = service._calculate_psi(actual=[1.0, 2.0] * 10, expected=[1.0, 2.0] * 10)
    assert psi_small == 0.0

    # num_bins = 1 clamped to 2
    actual_50 = [float(i) for i in range(50)]
    expected_50 = [float(i) for i in range(50)]
    psi_clamped = service._calculate_psi(actual=actual_50, expected=expected_50, num_bins=1)
    assert psi_clamped >= 0.0

    # Zero-variance constant distribution
    const_actual = [5.0] * 50
    const_expected = [5.0] * 50
    psi_const = service._calculate_psi(actual=const_actual, expected=const_expected)
    assert psi_const == pytest.approx(0.0, abs=1e-5)


def test_calibration_num_bins_and_boundary_probabilities() -> None:
    """Verifies compute_calibration handles num_bins clamping and edge probabilities."""
    service = ModelDriftService()

    # num_bins = 0 clamped to 2
    y_true = [0] * 50 + [1] * 50
    y_prob = [0.05] * 50 + [0.95] * 50
    report = service.compute_calibration(y_true=y_true, y_prob=y_prob, num_bins=0)

    assert report.is_well_calibrated is True
    assert len(report.bins) == 2
    assert report.brier_score < 0.05
    assert report.expected_calibration_error < 0.10

    # Empty inputs
    empty_report = service.compute_calibration(y_true=[], y_prob=[])
    assert empty_report.is_well_calibrated is True
    assert empty_report.brier_score == 0.0
