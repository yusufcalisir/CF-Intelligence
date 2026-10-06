"""Unit tests for RetrainingTriggerEngine and execute_automated_retraining_task."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.services.retraining_trigger_engine import RetrainingTriggerEngine
from app.tasks.simulation_tasks import execute_automated_retraining_task


def test_retraining_trigger_engine_ingestion_threshold() -> None:
    """Verifies ingestion threshold trigger logic at 50,000 record boundary."""
    engine = RetrainingTriggerEngine(ingestion_threshold=50000)

    assert engine.check_ingestion_threshold(49999) is False
    assert engine.check_ingestion_threshold(50000) is True
    assert engine.check_ingestion_threshold(100000) is True


def test_retraining_trigger_engine_drift_threshold() -> None:
    """Verifies PSI > 0.20 and KS p-value < 0.05 drift triggers."""
    engine = RetrainingTriggerEngine(psi_threshold=0.20, ks_pvalue_threshold=0.05)

    # Below limits
    assert engine.check_drift_threshold(psi_score=0.10, ks_p_value=0.50) is False

    # PSI breach (> 0.20)
    assert engine.check_drift_threshold(psi_score=0.25, ks_p_value=0.50) is True

    # KS breach (< 0.05)
    assert engine.check_drift_threshold(psi_score=0.10, ks_p_value=0.01) is True


def test_retraining_trigger_engine_scheduled_cadence() -> None:
    """Verifies 24-hour periodic cron cadence trigger."""
    engine = RetrainingTriggerEngine(cadence_hours=24)

    now = datetime.now(UTC)

    # Never run before
    assert engine.check_scheduled_cadence(None) is True

    # 10 hours ago -> Not due
    assert engine.check_scheduled_cadence(now - timedelta(hours=10)) is False

    # 25 hours ago -> Due
    assert engine.check_scheduled_cadence(now - timedelta(hours=25)) is True


def test_retraining_trigger_engine_evaluate_triggers() -> None:
    """Verifies evaluate_triggers compiles reasons list correctly."""
    engine = RetrainingTriggerEngine(
        ingestion_threshold=50000, psi_threshold=0.20, cadence_hours=24
    )

    now = datetime.now(UTC)

    res = engine.evaluate_triggers(
        record_count=60000,
        psi_score=0.25,
        ks_p_value=0.01,
        last_run_timestamp=now - timedelta(hours=10),
    )

    assert res["is_triggered"] is True
    assert "INGESTION_THRESHOLD_REACHED" in res["reasons"]
    assert "STATISTICAL_DRIFT_DETECTED" in res["reasons"]
    assert "SCHEDULED_CADENCE_ELAPSED" not in res["reasons"]


import numpy as np


def test_execute_automated_retraining_task_execution() -> None:
    """Verifies execute_automated_retraining_task worker execution and ROC-AUC quality gate."""
    # 1. Missing validation data must fail closed (AGENTS.md: No fabricated random inputs)
    res_missing = execute_automated_retraining_task.__wrapped__(
        "bank_test",
        ["INGESTION_THRESHOLD_REACHED"],
        0.30,
    )
    assert res_missing["status"] == "REJECTED_MISSING_VALIDATION_DATA"
    assert res_missing["quality_gate_passed"] is False
    assert res_missing["auc_roc"] is None

    # 2. Execution with empirical validation data and normal ROC-AUC gate threshold (0.30)
    rng = np.random.default_rng(42)
    X_val = rng.normal(size=(50, 10)).astype(np.float32)
    y_val = np.array([0] * 25 + [1] * 25, dtype=np.float32)

    res_pass = execute_automated_retraining_task.__wrapped__(
        "bank_test",
        ["INGESTION_THRESHOLD_REACHED"],
        0.30,
        X_val=X_val,
        y_val=y_val,
    )
    assert res_pass["status"] == "COMPLETED"
    assert res_pass["quality_gate_passed"] is True
    assert "compressed_payload_bytes" in res_pass
    assert res_pass["compressed_payload_bytes"] > 0

    # 3. Execution with impossibly high ROC-AUC gate threshold (0.99)
    res_fail = execute_automated_retraining_task.__wrapped__(
        "bank_test",
        ["STATISTICAL_DRIFT_DETECTED"],
        0.99,
        X_val=X_val,
        y_val=y_val,
    )
    assert res_fail["status"] == "REJECTED_QUALITY_GATE"
    assert res_fail["quality_gate_passed"] is False
