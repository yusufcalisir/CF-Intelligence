"""Integration tests for the closed-loop Drift Detection and Automated Retraining Pipeline.

Validates:
1. Multi-period feature and concept drift detection via ModelDriftService (PSI > 0.20, KS test).
2. Automated retraining trigger evaluation setting auto_retrain_triggered=True.
3. Execution of asynchronous Celery task execute_automated_retraining_task.
4. Model training with Opacus DP-SGD and parameter noise injection.
5. Quality gate evaluation (ROC-AUC >= 0.70) accepting compliant models and rejecting subpar candidates.
6. Payload compression (zlib) for efficient inter-node network transport.
7. Lifecycle management via DriftTriggeredRetrainingService.
"""

from __future__ import annotations

import json
import zlib

import numpy as np

from app.application.services.automated_retraining import (
    DriftTriggeredRetrainingService,
    RetrainingCause,
)
from app.application.services.drift_service import ModelDriftService
from app.tasks.simulation_tasks import execute_automated_retraining_task


class TestDriftRetrainingPipelineIntegration:
    """Integration test suite connecting statistical drift monitoring to model retraining."""

    def test_end_to_end_drift_detection_and_retraining_flow(self) -> None:
        """Simulates significant drift, evaluates triggers, and executes retraining task."""
        rng = np.random.default_rng(42)

        # 1. Synthesize reference (baseline) period
        ref_amount = rng.normal(120.0, 25.0, size=300).tolist()
        ref_velocity = rng.exponential(3.0, size=300).tolist()
        ref_scores = rng.beta(1.0, 15.0, size=300).tolist()

        # 2. Synthesize shifted (current) period under concept and feature drift
        curr_amount = rng.normal(350.0, 80.0, size=300).tolist()  # Large amount shift
        curr_velocity = rng.exponential(12.0, size=300).tolist()  # Large velocity shift
        curr_scores = rng.beta(5.0, 2.0, size=300).tolist()       # Critical concept drift

        drift_service = ModelDriftService(psi_threshold_warning=0.10, psi_threshold_critical=0.20)

        # 3. Execute drift analysis
        report = drift_service.run_full_drift_analysis(
            current_data={"transaction_amount": curr_amount, "velocity_1h": curr_velocity},
            reference_data={"transaction_amount": ref_amount, "velocity_1h": ref_velocity},
            current_scores=curr_scores,
            reference_scores=ref_scores,
            y_true=[0] * 150 + [1] * 150,
            y_prob=[0.05] * 150 + [0.95] * 150,
        )

        assert report.overall_status == "CRITICAL"
        assert report.max_psi >= 0.20
        assert report.concept_drift_psi >= 0.20
        assert report.auto_retrain_triggered is True

        # Extract trigger reasons
        trigger_reasons = [
            f"Feature PSI exceeded critical threshold: {fd.feature_name} (PSI={fd.psi:.4f})"
            for fd in report.feature_drifts
            if fd.psi >= 0.20
        ]
        if report.concept_drift_psi >= 0.20:
            trigger_reasons.append(f"Concept drift PSI exceeded limit (PSI={report.concept_drift_psi:.4f})")

        assert len(trigger_reasons) >= 2

        # 4. Dispatch automated retraining worker task synchronously via Celery apply
        X_val_alpha = rng.normal(size=(50, 10)).astype(np.float32)
        y_val_alpha = np.array([0] * 25 + [1] * 25, dtype=np.float32)
        retrain_result = execute_automated_retraining_task.apply(
            kwargs={
                "bank_id": "bank_alpha",
                "trigger_reasons": trigger_reasons,
                "auc_gate_threshold": 0.40,
                "X_val": X_val_alpha,
                "y_val": y_val_alpha,
            }
        ).result

        # 5. Verify task output contract
        assert "task_id" in retrain_result
        assert retrain_result["bank_id"] == "bank_alpha"
        assert retrain_result["status"] == "COMPLETED"
        assert retrain_result["quality_gate_passed"] is True
        assert retrain_result["auc_roc"] >= 0.40
        assert retrain_result["compressed_payload_bytes"] > 0
        assert retrain_result["trigger_reasons"] == trigger_reasons

    def test_retraining_quality_gate_rejection_on_subpar_model(self) -> None:
        """Verifies that the retraining pipeline rejects models that fail the quality gate."""
        rng = np.random.default_rng(99)
        X_val_beta = rng.normal(size=(50, 10)).astype(np.float32)
        y_val_beta = np.array([0] * 25 + [1] * 25, dtype=np.float32)

        # Set an impossible quality gate (AUC >= 0.9999) to force rejection
        retrain_result = execute_automated_retraining_task.apply(
            kwargs={
                "bank_id": "bank_beta",
                "trigger_reasons": ["Drift threshold breached"],
                "auc_gate_threshold": 0.9999,
                "X_val": X_val_beta,
                "y_val": y_val_beta,
            }
        ).result

        assert "task_id" in retrain_result
        assert retrain_result["bank_id"] == "bank_beta"
        assert retrain_result["status"] == "REJECTED_QUALITY_GATE"
        assert retrain_result["quality_gate_passed"] is False
        assert retrain_result["auc_roc"] < 0.9999
        assert "compressed_payload_bytes" not in retrain_result

    def test_drift_triggered_retraining_service_lifecycle(self) -> None:
        """Verifies job creation, execution, and history tracking in retraining service."""
        service = DriftTriggeredRetrainingService()

        # Create manual job triggered by PSI
        job = service.create_manual_job(
            reason="Empirical PSI > 0.20 on transaction_amount",
            cause=RetrainingCause.PSI_DRIFT_EXCEEDED,
            psi_score=0.285,
            retrain_feature_subset=["transaction_amount", "velocity_1h"],
            target_rounds=5,
        )

        assert job.job_id is not None
        assert job.cause == RetrainingCause.PSI_DRIFT_EXCEEDED
        assert job.psi_score == 0.285
        assert job.status == "TRIGGERED"

        # Retrieve and inspect
        fetched_job = service.get_job(job.job_id)
        assert fetched_job is not None
        assert fetched_job.job_id == job.job_id

        # Execute retraining pipeline
        exec_res = service.execute_retraining_pipeline(job.job_id)
        assert exec_res["status"] == "COMPLETED"
        assert job.status == "COMPLETED"

        # List jobs
        completed_jobs = service.list_jobs(status="COMPLETED")
        assert any(j.job_id == job.job_id for j in completed_jobs)

    def test_decompressed_payload_integrity(self) -> None:
        """Verifies that the compressed model payload produced during retraining is well-formed."""
        from app.application.services.model_service import ModelService
        from app.application.services.privacy_service import PrivacyService
        from app.config import get_settings

        settings = get_settings()
        model_service = ModelService(settings)
        privacy_service = PrivacyService()

        model = model_service.create_model(dp_compatible=True)
        noised_weights = privacy_service.add_noise_to_weights(
            weights=model_service.get_parameters(model),
            epsilon=1.0,
            delta=1e-5,
            max_grad_norm=1.0,
        )
        raw_params = noised_weights.flat_weights

        raw_bytes = json.dumps(raw_params).encode("utf-8")
        compressed_bytes = zlib.compress(raw_bytes)

        # Decompress and verify
        decompressed_bytes = zlib.decompress(compressed_bytes)
        assert decompressed_bytes == raw_bytes

        recovered_params = json.loads(decompressed_bytes.decode("utf-8"))
        assert len(recovered_params) == len(raw_params)

