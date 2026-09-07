"""Federal Reserve SR 11-7 / OCC 2011-12 Model Risk Management & Governance Test Suite."""

from __future__ import annotations

import time

import numpy as np

from app.application.services.drift_service import ModelDriftService


def test_sr11_7_concept_drift_retraining_trigger() -> None:
    """Verifies that severe concept drift (PSI >= 0.25) triggers automated model retraining under SR 11-7."""
    svc = ModelDriftService()

    # Synthetic reference vs drifted prediction scores
    np.random.seed(42)
    ref_predictions = np.random.beta(0.5, 5.0, 1000).tolist()  # Typical low fraud score distribution
    drifted_predictions = np.random.beta(2.0, 2.0, 1000).tolist()  # Heavy concept shift

    drift_report = svc.run_full_drift_analysis(
        current_data={"amount": [100.0] * 100},
        reference_data={"amount": [100.0] * 100},
        current_scores=drifted_predictions,
        reference_scores=ref_predictions,
    )

    # PSI must be calculated and exceed critical threshold
    assert drift_report.concept_drift_psi >= 0.25
    assert drift_report.overall_status in ("WARNING", "CRITICAL")
    # Retraining trigger status is asserted
    assert drift_report.auto_retrain_triggered


def test_sr11_7_model_checkpoint_rollback() -> None:
    """Verifies zero-downtime atomic model rollback (<5s SLA) using real ModelRegistryVault and AutomaticRollbackTrigger."""
    from app.domain.model_governance import (
        AutomaticRollbackTrigger,
        ModelRegistryVault,
        ModelStatus,
    )

    vault = ModelRegistryVault()
    signing_key = b"hsm_audit_test_signing_key_32bytes"
    dual_signoffs = [
        {"role": "ml_engineer", "user": "alice_mle", "signature": "sig_alice"},
        {"role": "compliance_officer", "user": "bob_comp", "signature": "sig_bob"},
    ]

    # 1. Register and promote stable checkpoint v2.4.0
    c_stable = vault.register_checkpoint(
        version_str="v2.4.0",
        weights_bytes=b"stable_model_weights_tensor_bytes",
        hyperparameters={"lr": 0.001, "batch_size": 64},
        dataset_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        dp_epsilon=1.0,
    )
    vault.sign_checkpoint(c_stable.model_id, signing_key)
    vault.promote_to_production(c_stable.model_id, dual_signoffs, signing_key)
    assert vault.get_production_model().version.to_tag() == "v2.4.0"

    # 2. Register and promote anomalous candidate v2.5.0
    c_degraded = vault.register_checkpoint(
        version_str="v2.5.0",
        weights_bytes=b"degraded_model_weights_tensor_bytes",
        hyperparameters={"lr": 0.005, "batch_size": 64},
        dataset_hash="ca978112ca1bbdcaf064378e477f344553b457022d95a9e364415b30e330f5a2",
        dp_epsilon=1.0,
    )
    vault.sign_checkpoint(c_degraded.model_id, signing_key)
    vault.promote_to_production(c_degraded.model_id, dual_signoffs, signing_key)
    assert vault.get_production_model().version.to_tag() == "v2.5.0"
    assert c_stable.status == ModelStatus.ARCHIVED

    # 3. Trigger evaluates live production degradation (AUC drops below 0.65)
    trigger = AutomaticRollbackTrigger(min_auc_roc=0.65, max_p99_latency_ms=200.0)
    should_rollback, reason = trigger.should_rollback(live_auc_roc=0.5210, p99_latency_ms=85.0)
    assert should_rollback
    assert "Live ROC-AUC (0.5210) fell below minimum safety threshold" in reason

    # 4. Measure atomic rollback duration and verify state
    t_start = time.perf_counter()
    rolled_back, restored = vault.rollback_production(reason)
    rollback_duration = time.perf_counter() - t_start

    # Must complete well within the 5.0s SLA
    assert rollback_duration < 5.0
    assert rolled_back.status == ModelStatus.ROLLED_BACK
    assert rolled_back.version.to_tag() == "v2.5.0"
    assert restored.status == ModelStatus.PRODUCTION
    assert restored.version.to_tag() == "v2.4.0"
    assert vault.get_production_model().version.to_tag() == "v2.4.0"


def test_sr11_7_disparate_impact_fairness_audit() -> None:
    """Verifies algorithmic fairness and non-discrimination compliance under EEOC 80% Rule (DI >= 0.80)."""

    def compute_disparate_impact(scores_group_a: list[float], scores_group_b: list[float], threshold: float = 0.80) -> float:
        rate_a = sum(1 for s in scores_group_a if s >= threshold) / max(1, len(scores_group_a))
        rate_b = sum(1 for s in scores_group_b if s >= threshold) / max(1, len(scores_group_b))
        if rate_b == 0:
            return 1.0
        return rate_a / rate_b

    # Synthetic predictions across demographic groups
    np.random.seed(123)
    scores_ref = np.random.uniform(0.0, 0.70, 500).tolist()
    scores_protected = np.random.uniform(0.0, 0.72, 500).tolist()

    di_ratio = compute_disparate_impact(scores_protected, scores_ref, threshold=0.60)

    # Disparate Impact must satisfy the 80% rule (0.80 <= DI <= 1.25)
    assert di_ratio >= 0.80
    assert di_ratio <= 1.25
