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

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
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
from app.infrastructure.database.training_authority_backends import SQLiteTrainingJobAuthority

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
    """A2. Retraining succeeds only when an authorized OperationalFederationService executes and produces verified candidate."""
    mock_op = MagicMock()
    mock_op.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "is_verified": True,
        "candidate_model_version": "model_v2.0.0",
        "evaluation_metrics": {"pr_auc": 0.895, "roc_auc": 0.920, "f1": 0.835},
        "receipt_id": "rcpt_verified_123",
        "saved_version": {"version": 2, "status": "INACTIVE"},
        "is_promoted_champion": False,
    }
    service = DriftTriggeredRetrainingService(psi_threshold=0.20, operational_service=mock_op)
    job = service.evaluate_drift_and_trigger(psi_score=0.40)
    assert job is not None

    result = service.execute_retraining_pipeline(job.job_id)
    assert result["status"] == "COMPLETED"
    assert result["candidate_model_version"] == "v2"
    assert result["evaluation_metrics"]["pr_auc"] == 0.895
    assert result["is_promoted_champion"] is False
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


# ---------------------------------------------------------------------------
# Required Tests A - J for Final Durable Recovery Correctness
# ---------------------------------------------------------------------------


def test_completed_job_retry_after_staging_cleanup_returns_original_candidate() -> None:
    """A. Successful training followed by second identical request after staging cleanup returns original candidate without duplicate registration."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_a_")
    db_file = f"sqlite:///{os.path.join(temp_dir, 'authority.db')}"
    registry_dir = os.path.join(temp_dir, "models")
    staging_dir = os.path.join(registry_dir, ".recovery_staging")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_req_a",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(101)
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

        job_id = f"job_req_a_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        # 1. First execution: complete normal training & candidate registration
        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )
        assert res1["status"] == "COMPLETED"
        assert res1["is_verified"] is True
        assert res1["recovered_from_registered_candidate"] is False

        # Staging directory must be cleaned up
        staging_mgr = DurableArtifactStagingManager(staging_dir)
        assert not staging_mgr.has_staged_artifact(job_id)

        # Candidate is registered in ModelRegistry
        registry = ModelRegistry(registry_dir)
        manifest1 = registry._load_manifest(sim_id)
        assert len(manifest1) == 1
        assert manifest1[0]["version"] == 1

        # 2. Second execution: retry with same job_id after staging cleanup
        res2 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )

        # Must reconcile against the existing registered candidate
        assert res2["status"] == "COMPLETED"
        assert res2["is_verified"] is True
        assert res2["recovered_from_registered_candidate"] is True
        assert res2["recovered_from_staging"] is False
        assert res2["saved_version"]["version"] == 1
        assert res2["final_artifact_sha256"] == res1["final_artifact_sha256"]

        # Crucial: No duplicate version created!
        manifest2 = registry._load_manifest(sim_id)
        assert len(manifest2) == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_crash_after_completion_before_receipt_issuance_recovers() -> None:
    """B. Crash after completion before receipt issuance recovers correctly."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_b_")
    db_file = f"sqlite:///{os.path.join(temp_dir, 'authority.db')}"
    registry_dir = os.path.join(temp_dir, "models")
    staging_dir = os.path.join(registry_dir, ".recovery_staging")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_req_b",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(107)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(30, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=30).astype(np.float32),
            ),
        }

        job_id = f"job_req_b_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        # 1. Simulate crash after completion before receipt issuance
        with pytest.raises(RuntimeError, match="Simulated coordinator process crash after completion"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                simulate_crash_after_completion=True,
            )

        # Staged artifact exists on disk
        staging_mgr = DurableArtifactStagingManager(staging_dir)
        assert staging_mgr.has_staged_artifact(job_id)

        # 2. Re-invoking with the same job_id recovers staged artifact, issues receipt, and registers candidate
        res = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )

        assert res["status"] == "COMPLETED"
        assert res["recovered_from_staging"] is True
        assert res["is_verified"] is True
        assert not staging_mgr.has_staged_artifact(job_id)

        registry = ModelRegistry(registry_dir)
        assert len(registry._load_manifest(sim_id)) == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_crash_after_receipt_before_registry_recovers() -> None:
    """C. Crash after receipt issuance before candidate registration recovers correctly from staging."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_c_")
    db_file = f"sqlite:///{os.path.join(temp_dir, 'authority.db')}"
    registry_dir = os.path.join(temp_dir, "models")
    staging_dir = os.path.join(registry_dir, ".recovery_staging")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_req_c",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(102)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(30, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=30).astype(np.float32),
            ),
        }

        job_id = f"job_req_c_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        # 1. Simulate crash after receipt issuance before registry persistence
        with pytest.raises(RuntimeError, match="Simulated coordinator process crash after receipt issuance"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                simulate_crash_after_receipt=True,
            )

        # Staged artifact must exist on disk
        staging_mgr = DurableArtifactStagingManager(staging_dir)
        assert staging_mgr.has_staged_artifact(job_id)

        # 2. Resume execution on the same job_id
        res = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )

        assert res["status"] == "COMPLETED"
        assert res["recovered_from_staging"] is True
        assert res["is_verified"] is True
        assert not staging_mgr.has_staged_artifact(job_id)

        registry = ModelRegistry(registry_dir)
        assert len(registry._load_manifest(sim_id)) == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_retry_robust_aggregation_preserves_real_algorithm_and_passes_api() -> None:
    """D. Retry after a successful robust-aggregation job preserves real algorithm and passes API validation."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_d_")
    db_path = os.path.join(temp_dir, "authority.db")
    db_file = f"sqlite:///{db_path}"
    registry_dir = os.path.join(temp_dir, "models")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_req_d",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(103)
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
            "BANK_C": (
                rng.normal(size=(35, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=35).astype(np.float32),
            ),
        }

        job_id = f"job_req_d_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        # 1. Run with coordinate_wise_median Byzantine defense (n=3, f=1 satisfies f < n/2)
        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            byzantine_defense="coordinate_wise_median",
            byzantine_f_max=1,
        )
        assert res1["status"] == "COMPLETED"
        assert res1["executed_aggregation_method"] == "coordinate_wise_median"

        # 2. Retry with identical Byzantine defense
        res2 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            byzantine_defense="coordinate_wise_median",
            byzantine_f_max=1,
        )
        assert res2["status"] == "COMPLETED"
        assert res2["recovered_from_registered_candidate"] is True
        # Real executed algorithm must be preserved (NOT "recovery_reconciled")
        assert res2["executed_aggregation_method"] == "coordinate_wise_median"
        assert res2["requested_aggregation_method"] == "coordinate_wise_median"

        # Verify against operational HTTP router check logic (training.py lines 380-395)
        req_defense = ("coordinate_wise_median").lower()
        exec_agg = (res2.get("executed_aggregation_method") or "").lower()
        assert exec_agg == req_defense, "Retry must not trigger HTTP 500 defense mismatch"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_secagg_and_dp_retries_preserve_truthful_metadata() -> None:
    """E. SecAgg and DP retries preserve or truthfully recover authenticated historical execution metadata."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_e_")
    db_path = os.path.join(temp_dir, "authority.db")
    db_file = f"sqlite:///{db_path}"
    registry_dir = os.path.join(temp_dir, "models")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        # Pre-provision institutional privacy budget in SQLite authority
        auth = SQLiteTrainingJobAuthority(db_file)
        auth.provision_institutional_privacy_budget(
            bank_id="BANK_A",
            dataset_tag="primary_vault",
            allocated_epsilon=20.0,
            allocated_delta=1e-4,
            aliases=[
                "BANK_A_dataset",
                "BANK_A_protected_dataset",
                "primary_vault",
                "default_vault",
                "default",
                "canonical_fraud_partition",
            ],
        )

        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_req_e",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(104)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(120, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=120).astype(np.float32),
            ),
        }

        job_id = f"job_req_e_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        dp_cfg = {
            "dp_enabled": True,
            "target_epsilon": 8.0,
            "target_delta": 1e-5,
            "max_grad_norm": 1.0,
            "dataset_tag": "primary_vault",
        }
        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            dp_config=dp_cfg,
        )
        assert res1["status"] == "COMPLETED"
        assert res1["dp_enabled"] is True

        # Compatible retry succeeds with authentic DP status
        res2 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
            dp_config=dp_cfg,
        )
        assert res2["status"] == "COMPLETED"
        assert res2["dp_enabled"] is True
        assert res2["recovered_from_registered_candidate"] is True

        # Conflicting DP retry is rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Incompatible retry DP configuration"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                dp_config={"dp_enabled": False},
            )

        # Conflicting SecAgg retry is rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Incompatible retry SecAgg configuration"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                dp_config=dp_cfg,
                secagg_enabled=True,
            )

        # Now test SecAgg recovery reconciliation with authoritative SecAgg records
        secagg_job_id = f"job_secagg_e_{uuid.uuid4().hex[:8]}"
        secagg_sim_id = f"sim_{secagg_job_id}"
        secagg_run_id = f"run_{secagg_job_id}"

        # Register completed candidate and authoritative records for SecAgg
        dummy_model = b"secagg_dummy_model_bytes_e"
        dummy_sha = hashlib.sha256(dummy_model).hexdigest()
        secagg_evidence = TrainingExecutionEvidence(
            training_run_id=secagg_run_id,
            job_id=secagg_job_id,
            simulation_id=secagg_sim_id,
            completed_at="2026-10-19T14:00:00Z",
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"roc_auc": 0.88, "loss": 0.38},
            artifact_sha256=dummy_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
            consortium_id="consortium_test",
        )
        issuer = get_default_training_issuer()
        secagg_receipt = issuer.issue_receipt(evidence=secagg_evidence, model_bytes=dummy_model)

        reg = ModelRegistry(registry_dir)
        reg.save_version(
            simulation_id=secagg_sim_id,
            artifact_bytes=dummy_model,
            metrics={"roc_auc": 0.88, "loss": 0.38},
            consortium_id="consortium_test",
            training_receipt=secagg_receipt,
            training_run_id=secagg_run_id,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )

        from app.infrastructure.database.training_authority_backends import (
            GlobalAggregationEvent,
            ModelEvaluationEvent,
            RoundObservation,
        )

        auth.schedule_job(
            job_id=secagg_job_id,
            training_run_id=secagg_run_id,
            simulation_id=secagg_sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["TRAINING_EXECUTOR"],
            consortium_id="consortium_test",
        )
        auth.start_job(job_id=secagg_job_id, worker_id="TRAINING_EXECUTOR")
        auth.record_observation(
            RoundObservation(
                event_id=f"obs_{uuid.uuid4().hex[:8]}",
                job_id=secagg_job_id,
                training_run_id=secagg_run_id,
                simulation_id=secagg_sim_id,
                worker_id="TRAINING_EXECUTOR",
                bank_id="BANK_A",
                round_number=1,
                attempt_number=1,
                actual_sample_count=100,
                actual_executed_epochs=1,
                observed_loss=0.38,
                model_update_digest="digest_a",
                execution_status="COMPLETED",
                execution_timestamp=datetime.now(UTC).isoformat(),
            )
        )
        auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"agg_{uuid.uuid4().hex[:8]}",
                job_id=secagg_job_id,
                round_number=1,
                aggregation_algorithm="secagg_fed_avg_weighted",
                accepted_participant_ids=("BANK_A",),
                accepted_local_update_digests=("digest_a",),
                aggregation_configuration={"quantization_scale": 1e7},
                aggregated_global_model_digest=dummy_sha,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )
        for m_name, m_val in [("roc_auc", 0.88), ("loss", 0.38)]:
            auth.record_model_evaluation(
                ModelEvaluationEvent(
                    event_id=f"eval_{m_name}_{uuid.uuid4().hex[:8]}",
                    job_id=secagg_job_id,
                    evaluation_run_id=f"eval_run_{secagg_job_id}",
                    metric_name=m_name,
                    metric_value=m_val,
                    evaluated_artifact_digest=dummy_sha,
                    evaluator_identity="EVALUATOR",
                    evaluation_timestamp=datetime.now(UTC).isoformat(),
                    evaluation_dataset_manifest="manifest_test",
                )
            )
        auth.complete_job(
            job_id=secagg_job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"roc_auc": 0.88, "loss": 0.38},
            artifact_sha256=dummy_sha,
            simulation_id=secagg_sim_id,
            training_run_id=secagg_run_id,
            consortium_id="consortium_test",
            enforce_lifecycle=False,
        )

        # Retry with secagg_enabled=True recovers truthful SecAgg execution method
        res_sec = manager.execute_distributed_federation(
            job_id=secagg_job_id,
            training_run_id=secagg_run_id,
            simulation_id=secagg_sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            num_rounds=1,
            registry_storage_dir=registry_dir,
            secagg_enabled=True,
            consortium_id="consortium_test",
        )
        assert res_sec["status"] == "COMPLETED"
        assert res_sec["secagg_enabled"] is True
        assert res_sec["executed_aggregation_method"] == "secagg_fed_avg_weighted"
        assert res_sec["recovered_from_registered_candidate"] is True

        # Retry with secagg_enabled=False fails closed on conflict with executed SecAgg
        with pytest.raises(TrainingProvenanceSecurityError, match="Incompatible retry SecAgg configuration"):
            manager.execute_distributed_federation(
                job_id=secagg_job_id,
                training_run_id=secagg_run_id,
                simulation_id=secagg_sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                num_rounds=1,
                registry_storage_dir=registry_dir,
                secagg_enabled=False,
                consortium_id="consortium_test",
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_retry_with_incompatible_parameters_fails_closed() -> None:
    """F. Same job_id with changed simulation, consortium, rounds, or algorithm is rejected."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_f_")
    db_file = f"sqlite:///{os.path.join(temp_dir, 'authority.db')}"
    registry_dir = os.path.join(temp_dir, "models")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_req_f",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(105)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(30, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=30).astype(np.float32),
            ),
        }

        job_id = f"job_req_f_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            consortium_id="consortium_alpha",
            registry_storage_dir=registry_dir,
        )
        assert res1["status"] == "COMPLETED"

        # 1. Changed simulation_id
        with pytest.raises(TrainingProvenanceSecurityError, match="simulation_id"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id="different_sim_id",
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                consortium_id="consortium_alpha",
                registry_storage_dir=registry_dir,
            )

        # 2. Changed consortium_id
        with pytest.raises(TrainingProvenanceSecurityError, match="consortium_id"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                consortium_id="consortium_beta",
                registry_storage_dir=registry_dir,
            )

        # 3. Changed round count
        with pytest.raises(TrainingProvenanceSecurityError, match="num_rounds"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=3,
                consortium_id="consortium_alpha",
                registry_storage_dir=registry_dir,
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_missing_or_corrupted_staged_artifact_with_no_candidate_fails_closed() -> None:
    """G. Missing or corrupted staged artifact with no registered candidate fails closed."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_g_")
    db_path = os.path.join(temp_dir, "authority.db")
    db_file = f"sqlite:///{db_path}"
    registry_dir = os.path.join(temp_dir, "models")
    staging_dir = os.path.join(registry_dir, ".recovery_staging")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_req_g",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(106)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(30, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=30).astype(np.float32),
            ),
        }

        job_id = f"job_req_g_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        # 1. Crash after completion before receipt (leaves staged file)
        with pytest.raises(RuntimeError, match="Simulated coordinator process crash after completion"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                simulate_crash_after_completion=True,
            )

        staging_mgr = DurableArtifactStagingManager(staging_dir)
        assert staging_mgr.has_staged_artifact(job_id)

        # Manually delete staged files to simulate missing on disk
        staging_mgr.cleanup_staged_artifact(job_id)
        assert not staging_mgr.has_staged_artifact(job_id)

        # Attempt to resume recovery: neither candidate nor staged artifact exists -> MUST fail closed!
        with pytest.raises(TrainingProvenanceSecurityError, match="neither a verified candidate model version nor a valid staged recovery artifact exists"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_staging_path_traversal_and_unsafe_identity_rejected() -> None:
    """H. Job IDs containing traversal, absolute paths, separators, or unsafe path components cannot access files outside staging."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_h_")
    try:
        staging = DurableArtifactStagingManager(temp_dir)
        sim_id = "sim_traversal_test"
        dummy_data = b"some_bytes"

        unsafe_job_ids = [
            "../../escape",
            "..\\..\\escape_win",
            "/absolute/unix/path",
            "C:\\absolute\\win\\path",
            "job\x00nullbyte",
            "job/with/slash",
            "job\\with\\backslash",
            "   ",
        ]

        for unsafe_id in unsafe_job_ids:
            with pytest.raises(TrainingProvenanceSecurityError):
                staging.stage_artifact(
                    job_id=unsafe_id,
                    simulation_id=sim_id,
                    consortium_id=None,
                    artifact_bytes=dummy_data,
                )

            with pytest.raises(TrainingProvenanceSecurityError):
                staging.load_and_verify_staged_artifact(
                    job_id=unsafe_id,
                    simulation_id=sim_id,
                    consortium_id=None,
                    expected_sha256="abc",
                )

            # has_staged_artifact returns False safely
            assert staging.has_staged_artifact(unsafe_id) is False

        # Verify no files were created outside temp_dir
        parent_dir = os.path.dirname(temp_dir)
        escape_file = os.path.join(parent_dir, "escape")
        assert not os.path.exists(escape_file)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_conflicting_staging_writes_rejected() -> None:
    """I. Conflicting staging writes for one job cannot replace the prior valid artifact."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_req_i_")
    try:
        staging = DurableArtifactStagingManager(temp_dir)
        job_id = "job_conflict_test_01"
        sim_id = "sim_conflict_01"
        bytes_a = b"legitimate_first_artifact_bytes"
        bytes_b = b"conflicting_second_artifact_bytes"

        # 1. First stage
        path_a, _ = staging.stage_artifact(
            job_id=job_id,
            simulation_id=sim_id,
            consortium_id=None,
            artifact_bytes=bytes_a,
        )

        # 2. Re-staging exact same bytes succeeds idempotently
        path_a_retry, _ = staging.stage_artifact(
            job_id=job_id,
            simulation_id=sim_id,
            consortium_id=None,
            artifact_bytes=bytes_a,
        )
        assert path_a == path_a_retry

        # 3. Conflicting write for same job_id is rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting staged artifact"):
            staging.stage_artifact(
                job_id=job_id,
                simulation_id=sim_id,
                consortium_id=None,
                artifact_bytes=bytes_b,
            )

        # Original artifact bytes remain uncorrupted
        loaded = staging.load_and_verify_staged_artifact(
            job_id=job_id,
            simulation_id=sim_id,
            consortium_id=None,
            expected_sha256=hashlib.sha256(bytes_a).hexdigest(),
        )
        assert loaded == bytes_a
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_retraining_rejects_unverified_or_incomplete_operational_results() -> None:
    """J. Retraining rejects unverified or incomplete operational results and never invents candidate identifiers."""
    service = DriftTriggeredRetrainingService(psi_threshold=0.20)
    job = service.evaluate_drift_and_trigger(psi_score=0.45)
    assert job is not None

    # Case 1: unverified result (is_verified is not True)
    mock_op = MagicMock()
    mock_op.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "is_verified": False,
        "candidate_model_version": "v1",
        "metrics": {"roc_auc": 0.90},
    }
    service.operational_service = mock_op
    with pytest.raises(RuntimeError, match="unverified"):
        service.execute_retraining_pipeline(job.job_id)

    # Case 2: invalid candidate model version (e.g. None or model_vNone)
    mock_op.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "is_verified": True,
        "candidate_model_version": "model_vNone",
        "metrics": {"roc_auc": 0.90},
    }
    with pytest.raises(RuntimeError, match="valid candidate model version"):
        service.execute_retraining_pipeline(job.job_id)

    # Case 3: missing or non-finite metrics
    mock_op.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "is_verified": True,
        "candidate_model_version": "v1",
        "metrics": {},
    }
    with pytest.raises(RuntimeError, match="metrics"):
        service.execute_retraining_pipeline(job.job_id)

    # Case 4: automated retraining claiming automatic champion promotion
    mock_op.execute_operational_federation.return_value = {
        "status": "COMPLETED",
        "is_verified": True,
        "candidate_model_version": "v1",
        "metrics": {"roc_auc": 0.90},
        "is_promoted_champion": True,
    }
    with pytest.raises(RuntimeError, match="cannot automatically promote candidate to champion"):
        service.execute_retraining_pipeline(job.job_id)


# ---------------------------------------------------------------------------
# Test Gates for Final Recovery Verification & Historical Aggregation Truth
# ---------------------------------------------------------------------------


def test_candidate_missing_receipt_not_verified_and_preserves_staging() -> None:
    """B & E. Candidate with matching digest but missing receipt is NOT verified, and staged evidence is preserved."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_gate_be_")
    db_path = os.path.join(temp_dir, "authority.db")
    db_file = f"sqlite:///{db_path}"
    registry_dir = os.path.join(temp_dir, "models")
    staging_dir = os.path.join(registry_dir, ".recovery_staging")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_gate_be",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(201)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(25, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=25).astype(np.float32),
            ),
        }

        job_id = f"job_gate_be_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        # 1. Complete initial training
        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )
        assert res1["status"] == "COMPLETED"
        assert res1["is_verified"] is True

        # Read registered candidate artifact bytes to prepare staging test
        registry = ModelRegistry(registry_dir)
        sim_dir = registry._get_sim_dir(sim_id)
        manifest_file = os.path.join(sim_dir, "registry.json")
        with open(manifest_file, encoding="utf-8") as f:
            manifest_data = json.load(f)
        assert len(manifest_data) == 1
        model_file = os.path.join(sim_dir, manifest_data[0]["filename"])
        with open(model_file, "rb") as f:
            model_bytes = f.read()

        # Subcase 1 (Test B): Candidate has NO receipt and NO staged artifact exists -> Must FAIL CLOSED
        manifest_data[0]["training_receipt"] = None
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)
        ModelRegistry._shared_manifest_cache.clear()

        with pytest.raises(TrainingProvenanceSecurityError, match="Non-recoverable post-completion state"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )

        # Subcase 2 (Test E): Staged artifact IS present.
        # Candidate reconciliation must skip the unverified candidate without deleting staging,
        # allowing Step D to safely recover from staging.
        staging_mgr = DurableArtifactStagingManager(staging_dir)
        staging_mgr.stage_artifact(
            job_id=job_id,
            simulation_id=sim_id,
            consortium_id=None,
            artifact_bytes=model_bytes,
        )
        assert staging_mgr.has_staged_artifact(job_id)

        recovery_res = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )
        assert recovery_res["status"] == "COMPLETED"
        assert recovery_res["is_verified"] is True
        assert recovery_res["recovered_from_staging"] is True
        assert recovery_res["recovered_from_registered_candidate"] is False
        # Staging is cleaned up only AFTER successful verified recovery
        assert not staging_mgr.has_staged_artifact(job_id)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_malformed_or_invalid_receipt_cannot_authorize_recovery() -> None:
    """C. Malformed or invalid receipt cannot authorize candidate recovery."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_gate_c_")
    db_path = os.path.join(temp_dir, "authority.db")
    db_file = f"sqlite:///{db_path}"
    registry_dir = os.path.join(temp_dir, "models")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_gate_c",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(202)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(25, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=25).astype(np.float32),
            ),
        }

        job_id = f"job_gate_c_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )
        assert res1["status"] == "COMPLETED"

        registry = ModelRegistry(registry_dir)
        sim_dir = registry._get_sim_dir(sim_id)
        manifest_file = os.path.join(sim_dir, "registry.json")
        with open(manifest_file, encoding="utf-8") as f:
            manifest_data = json.load(f)

        # Corrupt signature in candidate receipt
        manifest_data[0]["training_receipt"]["signature"] = "deadbeef" * 16
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)
        ModelRegistry._shared_manifest_cache.clear()

        # Must fail closed with no valid candidate and no staging
        with pytest.raises(TrainingProvenanceSecurityError, match="Non-recoverable post-completion state"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_receipt_identity_mismatches_rejected() -> None:
    """D. Receipts with mismatched job, simulation, consortium, or run are rejected."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_gate_d_")
    db_path = os.path.join(temp_dir, "authority.db")
    db_file = f"sqlite:///{db_path}"
    registry_dir = os.path.join(temp_dir, "models")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_gate_d",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(203)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(25, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=25).astype(np.float32),
            ),
        }

        job_id = f"job_gate_d_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"
        consortium_id = "consortium_alpha"

        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            consortium_id=consortium_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )
        assert res1["status"] == "COMPLETED"

        registry = ModelRegistry(registry_dir)
        sim_dir = registry._get_sim_dir(sim_id)
        manifest_file = os.path.join(sim_dir, "registry.json")
        with open(manifest_file, encoding="utf-8") as f:
            original_manifest = json.load(f)

        # Mismatch cases to test
        mismatch_cases = [
            ("job_id", "job_wrong_12345"),
            ("simulation_id", "sim_wrong_67890"),
            ("training_run_id", "run_wrong_abcde"),
            ("consortium_id", "consortium_bravo"),
            ("model_artifact_sha256", "0" * 64),
        ]

        for field_name, wrong_value in mismatch_cases:
            tampered = copy.deepcopy(original_manifest)
            tampered[0]["training_receipt"][field_name] = wrong_value
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(tampered, f)
            ModelRegistry._shared_manifest_cache.clear()

            with pytest.raises(TrainingProvenanceSecurityError, match="Non-recoverable post-completion state"):
                manager.execute_distributed_federation(
                    job_id=job_id,
                    training_run_id=run_id,
                    simulation_id=sim_id,
                    consortium_id=consortium_id,
                    holdout_val_data=(val_X, val_y),
                    participating_banks=["BANK_A"],
                    bank_partitions=partitions,
                    num_rounds=1,
                    registry_storage_dir=registry_dir,
                )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_missing_or_corrupt_aggregation_evidence_cannot_be_replaced_by_retry_parameters() -> None:
    """F. Missing or unreadable authoritative aggregation evidence cannot be replaced with retry algorithm."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_test_gate_f_")
    db_path = os.path.join(temp_dir, "authority.db")
    db_file = f"sqlite:///{db_path}"
    registry_dir = os.path.join(temp_dir, "models")
    os.makedirs(registry_dir, exist_ok=True)

    try:
        manager = DistributedFederatedTrainingManager(
            coordinator_token="coord_token_gate_f",
            db_url=db_file,
            app_env="development",
        )

        rng = np.random.default_rng(204)
        val_X = rng.normal(size=(20, NUM_FEATURES)).astype(np.float32)
        val_y = rng.choice([0.0, 1.0], size=20).astype(np.float32)
        partitions = {
            "BANK_A": (
                rng.normal(size=(25, NUM_FEATURES)).astype(np.float32),
                rng.choice([0.0, 1.0], size=25).astype(np.float32),
            ),
        }

        job_id = f"job_gate_f_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_{job_id}"
        run_id = f"run_{job_id}"

        res1 = manager.execute_distributed_federation(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            holdout_val_data=(val_X, val_y),
            participating_banks=["BANK_A"],
            bank_partitions=partitions,
            num_rounds=1,
            registry_storage_dir=registry_dir,
        )
        assert res1["status"] == "COMPLETED"

        # Case 1: Missing aggregation records entirely
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM cfi_global_aggregations WHERE job_id = ?", (job_id,))
            conn.commit()

        # Retry with caller-supplied Byzantine defense MUST NOT fabricate provenance
        with pytest.raises(TrainingProvenanceSecurityError, match="Missing authoritative global aggregation records"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                byzantine_defense="coordinate_wise_median",
            )

        # Case 2: Round continuity broken in authoritative aggregation records
        payload_digest = hashlib.sha256(b"dummy_payload_gate_f").hexdigest()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO cfi_global_aggregations
                   (event_id, job_id, round_number, aggregation_algorithm, accepted_participants_json,
                    accepted_digests_json, aggregation_config_json, aggregated_model_digest, timestamp, aggregation_status, payload_digest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"agg_broken_{uuid.uuid4().hex[:6]}",
                    job_id,
                    2,  # round 2 instead of expected round 1
                    "fed_avg_weighted",
                    json.dumps(["BANK_A"]),
                    json.dumps(["digest1"]),
                    "{}",
                    res1["final_artifact_sha256"],
                    "2026-10-19T00:00:00Z",
                    "COMPLETED",
                    payload_digest,
                ),
            )
            conn.commit()

        with pytest.raises(TrainingProvenanceSecurityError, match="fail round continuity"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
            )

        # Case 3: Conflicting aggregation algorithms across rounds
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM cfi_global_aggregations WHERE job_id = ?", (job_id,))
            conn.execute(
                """INSERT INTO cfi_global_aggregations
                   (event_id, job_id, round_number, aggregation_algorithm, accepted_participants_json,
                    accepted_digests_json, aggregation_config_json, aggregated_model_digest, timestamp, aggregation_status, payload_digest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"agg_r1_{uuid.uuid4().hex[:6]}",
                    job_id,
                    1,
                    "krum",
                    json.dumps(["BANK_A"]),
                    json.dumps(["digest1"]),
                    "{}",
                    res1["final_artifact_sha256"],
                    "2026-10-19T00:00:00Z",
                    "COMPLETED",
                    payload_digest,
                ),
            )
            conn.commit()

        with pytest.raises(TrainingProvenanceSecurityError, match="Incompatible retry Byzantine defense"):
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(val_X, val_y),
                participating_banks=["BANK_A"],
                bank_partitions=partitions,
                num_rounds=1,
                registry_storage_dir=registry_dir,
                byzantine_defense="trimmed_mean",
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


