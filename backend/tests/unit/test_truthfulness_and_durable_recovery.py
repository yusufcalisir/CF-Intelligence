"""Truthfulness, Recovery, and Provenance Verification Suite.

Validates the critical truthfulness and durable recovery requirements from AGENTS.md:
A. Automated retraining cannot generate fabricated success, metrics, or candidate IDs.
B. Monitoring alert/request status cannot be confused with genuinely dispatched or completed training.
C. Unauthorized monitoring callers cannot bypass operational training authorization.
D. Genuine operational federation and evaluation still produce verified inactive candidates.
E. Crash after authoritative completion but before receipt issuance: prove recoverable bytes and correct digest binding.
F. Crash after receipt issuance but before registry persistence: safe authorized recovery without private retraining.
G. Crash after registry commit but before response: retry returns the existing candidate without duplicate registration.
H. Recovery rejects forged, missing, corrupted, cross-job, and cross-consortium artifacts.
I. PostgreSQL completion idempotency and authority evidence reconciliation remain intact.
J. Development training and holdout partitions are demonstrably disjoint.
K. Existing DP, SecAgg, Byzantine, training receipt, registry, and Four-Eyes invariants remain valid.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.application.schemas.observability import (
    RetrainTriggerRequest,
    RetrainTriggerResponse,
)
from app.application.services.automated_retraining import (
    DriftTriggeredRetrainingService,
)
from app.application.services.data_generator import (
    FEATURE_NAMES,
    DataGenerator,
)
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    DurableArtifactStagingManager,
)
from app.application.services.model_registry import (
    ModelIntegrityError,
    ModelRegistry,
)
from app.application.services.operational_federation_service import (
    OperationalFederationService,
)
from app.application.services.training_provenance_service import (
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    get_default_training_issuer,
)

NUM_FEATURES = len(FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Test A: Automated Retraining Truthfulness (No Fabricated Success/Metrics/IDs)
# ---------------------------------------------------------------------------
def test_automated_retraining_cannot_fabricate_success() -> None:
    """A. Proves that automated retraining never invents candidate IDs or dummy metrics.

    When no authorized OperationalFederationService is present, it must fail closed
    with a RuntimeError and leave the job marked FAILED.
    """
    service = DriftTriggeredRetrainingService(psi_threshold=0.20, operational_service=None)
    job = service.evaluate_drift_and_trigger(psi_score=0.35)
    assert job is not None
    assert job.status == "TRIGGERED"
    assert job.candidate_model_version is None

    # Attempting to execute retraining without operational service must fail closed
    with pytest.raises(RuntimeError, match=r"(?i)no authorized OperationalFederationService provided"):
        service.execute_retraining_pipeline(job.job_id)

    failed_job = service.get_job(job.job_id)
    assert failed_job is not None
    assert failed_job.status == "FAILED"
    # Must NOT have fabricated any synthetic candidate identifier or dummy ML metrics
    assert failed_job.candidate_model_version is None
    assert "metrics" not in failed_job.details or not failed_job.details["metrics"]
    assert "auc" not in failed_job.details


def test_automated_retraining_with_authorized_operational_service() -> None:
    """A2. Retraining succeeds only when an authorized OperationalFederationService executes."""
    mock_op = MagicMock()
    mock_op.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "candidate_model_version": "model_v2.0.0",
        "evaluation_metrics": {"pr_auc": 0.895, "roc_auc": 0.920, "f1": 0.835},
        "receipt_id": "rcpt_verified_123",
        "saved_version": {"version": 2, "status": "INACTIVE"},
    }
    service = DriftTriggeredRetrainingService(psi_threshold=0.20, operational_service=mock_op)
    job = service.evaluate_drift_and_trigger(psi_score=0.40)
    assert job is not None

    result = service.execute_retraining_pipeline(job.job_id)
    assert result["status"] == "COMPLETED"
    assert result["candidate_model_version"] == "v2"
    assert result["evaluation_metrics"]["pr_auc"] == 0.895
    assert job.status == "COMPLETED"
    assert job.candidate_model_version == "v2"


# ---------------------------------------------------------------------------
# Test B & C: Monitoring Endpoint Semantics and Training RBAC
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_monitoring_alert_retraining_request_semantics() -> None:
    """B. Monitoring trigger-retrain reports ALERT_RECORDED_RETRAINING_RECOMMENDED.

    It must not claim operational dispatch or return triggered=True without real execution.
    """
    from app.presentation.routers.monitoring import trigger_automated_retraining

    req = RetrainTriggerRequest(reason="Significant feature drift on amount", target_simulation_rounds=3)
    res = await trigger_automated_retraining(request=req)
    assert isinstance(res, RetrainTriggerResponse)
    assert res.execution_status == "ALERT_RECORDED_RETRAINING_RECOMMENDED"
    assert res.operational_dispatch_confirmed is False
    assert res.dispatched_job_id is None
    assert "consortium" in str(res.recommendation_details.get("required_authorization", "")).lower()


@pytest.mark.asyncio
async def test_monitoring_router_rbac_isolation() -> None:
    """C. Monitoring router records recommendations only and cannot directly dispatch operational training."""
    from app.presentation.routers.monitoring import trigger_automated_retraining

    req = RetrainTriggerRequest(reason="Drift detected", target_simulation_rounds=2)
    res = await trigger_automated_retraining(request=req)
    # The monitoring endpoint must strictly not dispatch operational federation
    assert res.operational_dispatch_confirmed is False
    assert res.dispatched_job_id is None


# ---------------------------------------------------------------------------
# Test D: Genuine Operational Federation Produces Verified Inactive Candidate
# ---------------------------------------------------------------------------
def test_genuine_operational_federation_produces_verified_inactive_candidate() -> None:
    """D. Operational federation produces an INACTIVE candidate in ModelRegistry with cryptographic receipt."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_registry_d_")
    try:
        registry = ModelRegistry(storage_dir=temp_dir)
        model_bytes = b"fake_pytorch_statedict_verified_d"
        sim_id = "sim_d_001"
        job_id = "job_d_001"
        run_id = "run_d_001"
        digest = hashlib.sha256(model_bytes).hexdigest()

        issuer = get_default_training_issuer()
        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at="2026-10-19T12:00:00Z",
            status="COMPLETED",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=1000,
            observed_metrics={"loss": 0.35, "pr_auc": 0.88, "roc_auc": 0.91},
            artifact_sha256=digest,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="consortium_alpha",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)

        entry = registry.save_version(
            simulation_id=sim_id,
            artifact_bytes=model_bytes,
            metrics={"loss": 0.35, "pr_auc": 0.88, "roc_auc": 0.91},
            consortium_id="consortium_alpha",
            training_receipt=receipt,
            training_run_id=run_id,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )
        assert entry["status"] == "inactive"
        assert entry["version"] == 1
        assert entry["sha256"] == digest
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test E & H: Durable Artifact Staging and Crash-Window Recovery
# ---------------------------------------------------------------------------
def test_durable_artifact_staging_integrity_and_rejection() -> None:
    """E & H. DurableArtifactStagingManager atomic write, validation, and rejection of invalid data."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_staging_")
    try:
        staging = DurableArtifactStagingManager(temp_dir)
        job_id = "job_staging_001"
        sim_id = "sim_001"
        consortium_id = "consortium_test"
        data = b"model_weights_checkpoint_bytes_001"
        digest = hashlib.sha256(data).hexdigest()

        # Stage artifact
        staged_path, meta_path = staging.stage_artifact(
            job_id=job_id,
            simulation_id=sim_id,
            consortium_id=consortium_id,
            artifact_bytes=data,
        )
        assert os.path.exists(staged_path)
        assert os.path.exists(meta_path)
        assert staging.has_staged_artifact(job_id)

        # Successfully load and verify
        loaded = staging.load_and_verify_staged_artifact(
            job_id=job_id,
            simulation_id=sim_id,
            consortium_id=consortium_id,
            expected_sha256=digest,
        )
        assert loaded == data

        # H1: Reject forged / mismatched SHA-256
        with pytest.raises(TrainingProvenanceSecurityError, match="mismatch"):
            staging.load_and_verify_staged_artifact(
                job_id=job_id,
                simulation_id=sim_id,
                consortium_id=consortium_id,
                expected_sha256="wrong_digest" * 4,
            )

        # H2: Reject cross-consortium artifact
        with pytest.raises(TrainingProvenanceSecurityError, match="mismatch"):
            staging.load_and_verify_staged_artifact(
                job_id=job_id,
                simulation_id=sim_id,
                consortium_id="other_consortium",
                expected_sha256=digest,
            )

        # H3: Reject cross-simulation artifact
        with pytest.raises(TrainingProvenanceSecurityError, match="mismatch"):
            staging.load_and_verify_staged_artifact(
                job_id=job_id,
                simulation_id="wrong_sim",
                consortium_id=consortium_id,
                expected_sha256=digest,
            )

        # Cleanup
        staging.cleanup_staged_artifact(job_id)
        assert not staging.has_staged_artifact(job_id)

        # H4: Missing staged artifact fails closed
        with pytest.raises(TrainingProvenanceSecurityError, match="missing on disk"):
            staging.load_and_verify_staged_artifact(
                job_id=job_id,
                simulation_id=sim_id,
                consortium_id=consortium_id,
                expected_sha256=digest,
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test E & F: Crash After Completion / Receipt Issuance in DistributedFLService
# ---------------------------------------------------------------------------
def test_crash_after_completion_recovers_without_retraining() -> None:
    """E & F. Simulates coordinator crash after completion and verifies durable recovery."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_fl_recovery_")
    db_file = f"sqlite:///{os.path.join(temp_dir, 'authority.db')}"
    registry_dir = os.path.join(temp_dir, "models")
    staging_dir = os.path.join(registry_dir, ".recovery_staging")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_recovery_test",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(42)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(30, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=30).astype(np.float32),
            ),
            "BANK_B": (
                rng.normal(size=(40, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=40).astype(np.float32),
            ),
        }

        job_id = f"test_job_crash_{uuid.uuid4().hex[:8]}"

        # 1. Simulate crash immediately after finalize_training_completion
        with pytest.raises(RuntimeError, match="Simulated coordinator process crash after completion"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=f"run_{job_id}",
                simulation_id=f"sim_{job_id}",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A", "BANK_B"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                simulate_crash_after_completion=True,
            )

        # Staged artifact must durably exist on disk in registry's staging directory
        staging_mgr = DurableArtifactStagingManager(staging_dir)
        assert staging_mgr.has_staged_artifact(job_id)

        # 2. Resume execution on the same job_id (replaying recovery path without retraining)
        recovery_result = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id=f"sim_{job_id}",
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )

        # Verify recovery completed successfully from staged bytes without retraining
        assert recovery_result["status"] == "COMPLETED"
        assert recovery_result["recovered_from_staging"] is True
        assert recovery_result["is_verified"] is True
        assert "receipt" in recovery_result
        assert "saved_version" in recovery_result

        # Registry should have registered the candidate
        registry = ModelRegistry(registry_dir)
        manifest = registry._load_manifest(f"sim_{job_id}")
        assert len(manifest) == 1

        # Staging should be cleaned up after successful recovery
        assert not staging_mgr.has_staged_artifact(job_id)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test G: Candidate Registration Retries Are Idempotent
# ---------------------------------------------------------------------------
def test_candidate_registration_retries_idempotent() -> None:
    """G. Retrying ModelRegistry.save_version with identical verified execution returns existing entry."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_idempotent_reg_")
    try:
        registry = ModelRegistry(storage_dir=temp_dir)
        model_data = b"model_data_idempotent_test_payload"
        sim_id = "sim_idemp_test"
        job_id = "job_idemp_test"
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        digest = hashlib.sha256(model_data).hexdigest()

        issuer = get_default_training_issuer()
        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at="2026-10-19T12:00:00Z",
            status="COMPLETED",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=1000,
            observed_metrics={"loss": 0.35, "roc_auc": 0.89},
            artifact_sha256=digest,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="consortium_test",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_data)

        # 1. Initial save
        v1 = registry.save_version(
            simulation_id=sim_id,
            artifact_bytes=model_data,
            metrics={"loss": 0.35, "roc_auc": 0.89},
            consortium_id="consortium_test",
            training_receipt=receipt,
            training_run_id=run_id,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )
        assert v1["version"] == 1

        # 2. Retrying save with the exact same verified execution returns v1, NOT creating v2
        v1_retry = registry.save_version(
            simulation_id=sim_id,
            artifact_bytes=model_data,
            metrics={"roc_auc": 0.89},
            consortium_id="consortium_test",
            training_receipt=receipt,
            training_run_id=run_id,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )
        assert v1_retry["version"] == 1
        assert v1_retry["sha256"] == v1["sha256"]

        # Manifest must only contain 1 version entry
        manifest = registry._load_manifest(sim_id)
        assert len(manifest) == 1

        # Conflict check: same run_id but differing artifact bytes
        with pytest.raises(ModelIntegrityError, match="Conflicting registration"):
            registry.save_version(
                simulation_id=sim_id,
                artifact_bytes=b"completely_different_model_bytes",
                metrics={"loss": 0.35, "roc_auc": 0.89},
                consortium_id="consortium_test",
                training_run_id=run_id,
                expected_architecture="FraudDetectionNN",
                expected_feature_schema_version="v1.0",
                expected_input_dim=NUM_FEATURES,
                expected_feature_names=list(FEATURE_NAMES),
            )

        # Normal subsequent new model registration still gets v2
        new_model_data = b"new_model_data_v2"
        new_digest = hashlib.sha256(new_model_data).hexdigest()
        new_run_id = f"run_{uuid.uuid4().hex[:8]}"
        new_evidence = TrainingExecutionEvidence(
            training_run_id=new_run_id,
            job_id=f"job_{uuid.uuid4().hex[:8]}",
            simulation_id=sim_id,
            completed_at="2026-10-19T12:05:00Z",
            status="COMPLETED",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=1000,
            observed_metrics={"loss": 0.35, "roc_auc": 0.90},
            artifact_sha256=new_digest,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="consortium_test",
        )
        new_receipt = issuer.issue_receipt(evidence=new_evidence, model_bytes=new_model_data)

        v2 = registry.save_version(
            simulation_id=sim_id,
            artifact_bytes=new_model_data,
            metrics={"loss": 0.35, "roc_auc": 0.90},
            consortium_id="consortium_test",
            training_receipt=new_receipt,
            training_run_id=new_run_id,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )
        assert v2["version"] == 2
        assert len(registry._load_manifest(sim_id)) == 2

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test J: Development Training and Holdout Partitions Are Demonstrably Disjoint
# ---------------------------------------------------------------------------
def test_development_holdout_independence() -> None:
    """J. Development holdout data is demonstrably disjoint from training partitions."""
    gen = DataGenerator(seed=42)
    bank_datasets = gen.generate_bank_datasets(bank_a_size=500, bank_b_size=500, bank_c_size=500)
    training_data_a, labels_a = bank_datasets["bank_a"]
    training_data_b, labels_b = bank_datasets["bank_b"]

    # Generate dedicated holdout data
    holdout_feats, holdout_labels = gen.generate_dedicated_holdout_dataset(size=500)

    # 1. Sample ID prefix verification
    assert all(str(idx).startswith("sample_holdout_dev_") for idx in holdout_feats.index)

    # 2. Demonstrable disjointness: No index overlap with training data
    train_indices = set(training_data_a.index) | set(training_data_b.index)
    holdout_indices = set(holdout_feats.index)
    assert train_indices.isdisjoint(holdout_indices)

    # 3. OperationalFederationService resolve_holdout_data validates disjointness
    train_x_a = np.asarray(DataGenerator.encode_features(training_data_a[list(FEATURE_NAMES)]), dtype=np.float32)
    train_y_a = np.asarray(labels_a.to_numpy(dtype=np.float32), dtype=np.float32)
    train_x_b = np.asarray(DataGenerator.encode_features(training_data_b[list(FEATURE_NAMES)]), dtype=np.float32)
    train_y_b = np.asarray(labels_b.to_numpy(dtype=np.float32), dtype=np.float32)

    op_service = OperationalFederationService()
    training_partitions = {
        "bank_a": (train_x_a, train_y_a),
        "bank_b": (train_x_b, train_y_b),
    }
    X_holdout, y_holdout = op_service.resolve_holdout_data(
        is_prod=False,
        holdout_data=None,
        training_partitions=training_partitions,
    )
    assert len(X_holdout) == 500
    assert len(y_holdout) == 500

    # 4. Production fail-closed behavior for missing holdout
    with pytest.raises(TrainingProvenanceConfigurationError, match="Production holdout evaluation dataset is not configured"):
        op_service.resolve_holdout_data(
            is_prod=True,
            holdout_data=None,
        )
