# ruff: noqa: E402
"""Automated Unit Test Suite for Drift-Triggered Retraining and Auto-Rollback Engine."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.application.services.auto_rollback import AutoRollbackManager, RollbackCause
from app.application.services.automated_retraining import (
    DriftTriggeredRetrainingService,
    RetrainingCause,
)


def test_drift_triggered_retraining_evaluation_and_execution() -> None:
    """Test PSI drift threshold evaluation and automated retraining pipeline execution."""
    service = DriftTriggeredRetrainingService(psi_threshold=0.20)

    # 1. Normal PSI score does not trigger retraining
    no_job = service.evaluate_drift_and_trigger(psi_score=0.08)
    assert no_job is None

    # 2. Elevated PSI score triggers retraining
    job = service.evaluate_drift_and_trigger(psi_score=0.25)
    assert job is not None
    assert job.cause == RetrainingCause.PSI_DRIFT_EXCEEDED
    assert job.status == "TRIGGERED"

    # 3. Fail-closed: execution without authorized operational service raises RuntimeError and marks job FAILED
    with pytest.raises(RuntimeError, match=r"(?i)no authorized OperationalFederationService provided"):
        service.execute_retraining_pipeline(job.job_id)

    fetched = service.get_job(job.job_id)
    assert fetched is not None
    assert fetched.status == "FAILED"

    # 4. Authorized operational service execution succeeds with genuine metrics and registered candidate
    mock_op_service = MagicMock()
    mock_op_service.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "candidate_model_version": "model_v1.0.1",
        "evaluation_metrics": {"pr_auc": 0.892, "roc_auc": 0.915, "f1": 0.821},
        "receipt_id": "receipt_autoret_123",
    }
    service_with_op = DriftTriggeredRetrainingService(
        psi_threshold=0.20,
        operational_service=mock_op_service,
    )
    job2 = service_with_op.evaluate_drift_and_trigger(psi_score=0.25)
    assert job2 is not None
    res2 = service_with_op.execute_retraining_pipeline(job2.job_id)
    assert res2["status"] == "COMPLETED"
    assert res2["candidate_model_version"] == "model_v1.0.1"
    assert res2["evaluation_metrics"]["pr_auc"] == 0.892
    assert res2["evaluation_metrics"]["roc_auc"] == 0.915
    fetched2 = service_with_op.get_job(job2.job_id)
    assert fetched2 is not None
    assert fetched2.status == "COMPLETED"



def test_auto_rollback_manager_health_evaluation() -> None:
    """Test automated rollback execution upon AUC drop or latency SLA violation."""
    manager = AutoRollbackManager(min_auc=0.65, max_latency_ms=200.0, max_fpr=0.05)

    # 1. Healthy metrics -> No rollback
    triggered_healthy, record_healthy = manager.evaluate_model_health_and_rollback(
        active_model_version="model_v2.0.0",
        current_auc=0.85,
        current_latency_ms=45.0,
        current_fpr=0.02,
        fallback_model_version="model_v1.0.0",
    )
    assert triggered_healthy is False
    assert record_healthy is None

    # 2. AUC drop below 0.65 -> Triggers rollback
    triggered_auc, record_auc = manager.evaluate_model_health_and_rollback(
        active_model_version="model_v2.0.0",
        current_auc=0.60,
        current_latency_ms=50.0,
        current_fpr=0.02,
        fallback_model_version="model_v1.0.0",
    )
    assert triggered_auc is True
    assert record_auc is not None
    assert record_auc.cause == RollbackCause.AUC_DROP_CRITICAL
    assert record_auc.demoted_model_version == "model_v2.0.0"
    assert record_auc.restored_model_version == "model_v1.0.0"

    # 3. Latency SLA violation (>200ms) -> Triggers rollback
    triggered_lat, record_lat = manager.evaluate_model_health_and_rollback(
        active_model_version="model_v3.0.0",
        current_auc=0.88,
        current_latency_ms=250.0,
        current_fpr=0.01,
        fallback_model_version="model_v2.0.0",
    )
    assert triggered_lat is True
    assert record_lat is not None
    assert record_lat.cause == RollbackCause.LATENCY_SLA_VIOLATION

    history = manager.get_rollback_history()
    assert len(history) == 2
