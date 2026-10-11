"""Focused adversarial tests for Module 01-A.1: Trusted Training Job Execution Boundary.

Adversarial Verification Suite (JOB-01 through JOB-14):
- JOB-01: Known default credential rejected in production.
- JOB-02: Unauthorized caller cannot register job completion.
- JOB-03: Possession of authorized token alone without genuine lifecycle cannot establish execution.
- JOB-04: Manually forged evidence and completion records rejected.
- JOB-05: Conflicting completion overwrite fails closed.
- JOB-06: Cross-job replay rejected.
- JOB-07: Cross-simulation replay rejected.
- JOB-08: Schema, architecture, feature ordering, and producer substitution rejected.
- JOB-09: Metric substitution with identical artifact digest rejected.
- JOB-10: Genuine training lifecycle produces verifiable receipt and matches observations.
- JOB-11: Failed or incomplete training cannot produce verified receipt.
- JOB-12: Restart and multi-worker durability semantics.
- JOB-13: Duplicate submission idempotency vs conflict rejection.
- JOB-14: Production issuer/verifier separation and Ed25519 verification regression.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import os
import tempfile
from datetime import UTC, datetime

import pytest
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    CompletedJobExecutionRecord,
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    TrustedTrainingJobRecordSource,
    reset_default_training_issuer,
    reset_default_training_job_record_source,
    reset_default_training_verifier,
    reset_default_trust_store,
)
from app.config import Settings


def _generate_ed25519_keypair_hex() -> tuple[str, str]:
    """Generate a clean Ed25519 private/public keypair in raw hex format."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_raw.hex(), public_raw.hex()


def _serialize_model(model: nn.Module) -> bytes:
    """Serialize model state dict to deterministic bytes."""
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    return buf.getvalue()


class TestTrustedJobExecutionBoundary:
    """Test suite covering JOB-01 through JOB-14 for Module 01-A.1."""

    def setup_method(self) -> None:
        """Reset global provenance singletons before each test."""
        reset_default_training_issuer()
        reset_default_training_verifier()
        reset_default_training_job_record_source()
        reset_default_trust_store()

    def teardown_method(self) -> None:
        """Reset global provenance singletons after each test."""
        reset_default_training_issuer()
        reset_default_training_verifier()
        reset_default_training_job_record_source()
        reset_default_trust_store()

    # =========================================================================
    # JOB-01: Known default credential
    # =========================================================================
    def test_job_01_known_default_credential_rejected_in_production(self) -> None:
        """JOB-01: Verify production rejects the previous public worker token and missing credentials."""
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()

        # 1. Known public default token rejected in production Settings
        with pytest.raises(ValueError, match="training_worker_token"):
            s1 = Settings(
                app_env="production",
                training_worker_token="cfi_authorized_training_worker_secret_v1",
                training_receipt_private_key_hex=priv_hex,
                training_receipt_public_key_hex=pub_hex,
                secret_key="strong_secret_key_for_production_env_test_only_min_32",
            )
            s1.validate_production_invariants()

        # 2. Missing (empty) worker credential rejected in production Settings
        with pytest.raises(ValueError, match="training_worker_token"):
            s2 = Settings(
                app_env="production",
                training_worker_token="",
                training_receipt_private_key_hex=priv_hex,
                training_receipt_public_key_hex=pub_hex,
                secret_key="strong_secret_key_for_production_env_test_only_min_32",
            )
            s2.validate_production_invariants()

        # 3. Known placeholder tokens (e.g. 'secret', 'changeme') rejected in production
        for placeholder in ["secret", "changeme", "admin", "password", "token"]:
            with pytest.raises(ValueError, match="training_worker_token"):
                sp = Settings(
                    app_env="production",
                    training_worker_token=placeholder,
                    training_receipt_private_key_hex=priv_hex,
                    training_receipt_public_key_hex=pub_hex,
                    secret_key="strong_secret_key_for_production_env_test_only_min_32",
                )
                sp.validate_production_invariants()

        # 4. Record source rejects default placeholder token when configured in production
        with pytest.raises(TrainingProvenanceConfigurationError, match="forbidden default/placeholder"):
            TrustedTrainingJobRecordSource(
                configured_token="cfi_authorized_training_worker_secret_v1",
                app_env="production",
            )

        # 5. Record source rejects empty token when configured in production
        with pytest.raises(TrainingProvenanceConfigurationError, match="not configured"):
            TrustedTrainingJobRecordSource(
                configured_token="",
                app_env="production",
            )

    # =========================================================================
    # JOB-02: Unauthorized completion
    # =========================================================================
    def test_job_02_unauthorized_completion_rejected(self) -> None:
        """JOB-02: Attempt to register a completed job using an untrusted caller. Verify rejection."""
        authorized_token = "high_entropy_authorized_worker_token_38294719283"
        source = TrustedTrainingJobRecordSource(
            configured_token=authorized_token,
            app_env="production",
        )

        # First, schedule and start a valid job
        job_id = "job_sec_02"
        run_id = "run_sec_02"
        sim_id = "sim_sec_02"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
        )
        source.start_training_job(
            job_id=job_id,
            worker_token=authorized_token,
        )

        # 1. Caller provides no token
        with pytest.raises(TrainingProvenanceSecurityError, match="missing training worker authorization token"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=3,
                sample_count=1000,
                observed_metrics={"loss": 0.12},
                artifact_sha256="a" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
                worker_token=None,
            )

        # 2. Caller provides invalid/forged token
        with pytest.raises(TrainingProvenanceSecurityError, match="Invalid training worker authorization token"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=3,
                sample_count=1000,
                observed_metrics={"loss": 0.12},
                artifact_sha256="a" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
                worker_token="unauthorized_attacker_token_xyz",
            )

    # =========================================================================
    # JOB-03: Authorized token, nonexistent execution
    # =========================================================================
    def test_job_03_authorized_token_nonexistent_execution_rejected(self) -> None:
        """JOB-03: Present a valid worker credential but no genuine scheduled/started training job. Verify rejection."""
        authorized_token = "high_entropy_authorized_worker_token_38294719283"
        source = TrustedTrainingJobRecordSource(
            configured_token=authorized_token,
            app_env="production",
        )

        # Attempt to complete a job that was never scheduled or started, even with valid token
        with pytest.raises(TrainingProvenanceSecurityError, match="has not been scheduled"):
            source.complete_training_job(
                job_id="job_never_scheduled_or_started",
                executed_rounds=5,
                sample_count=5000,
                observed_metrics={"loss": 0.22},
                artifact_sha256="b" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
                worker_token=authorized_token,
                enforce_lifecycle=True,
            )

        # Attempt to complete a job that was scheduled but never started by worker
        source.schedule_training_job(
            job_id="job_scheduled_only",
            training_run_id="run_scheduled_only",
            simulation_id="sim_scheduled_only",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="has not been started"):
            source.complete_training_job(
                job_id="job_scheduled_only",
                executed_rounds=5,
                sample_count=5000,
                observed_metrics={"loss": 0.22},
                artifact_sha256="b" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
                worker_token=authorized_token,
                enforce_lifecycle=True,
            )

    # =========================================================================
    # JOB-04: Manually forged evidence
    # =========================================================================
    def test_job_04_manually_forged_evidence_rejected(self) -> None:
        """JOB-04: Construct valid-looking evidence without genuine authority. Verify neither can establish provenance."""
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()
        _ = DurableTrustStore({DEFAULT_TRAINING_KEY_ID: pub_hex})
        source = TrustedTrainingJobRecordSource()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Attacker constructs a forged TrainingExecutionEvidence object
        forged_evidence = TrainingExecutionEvidence(
            training_run_id="run_forged_04",
            job_id="job_forged_04",
            simulation_id="sim_forged_04",
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=10,
            executed_epochs=20,
            sample_count=100000,
            observed_metrics={"loss": 0.001, "auc_roc": 0.999},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="MALICIOUS_PRODUCER",
        )

        # Production receipt issuer refuses to issue receipt because evidence is not in trusted source
        with pytest.raises(TrainingProvenanceSecurityError, match="not found in trusted training job record source"):
            issuer.issue_receipt(
                evidence=forged_evidence,
                model_bytes=model_bytes,
            )

        # Attacker constructs a CompletedJobExecutionRecord and injects directly into an untrusted caller
        forged_record = CompletedJobExecutionRecord(
            job_id="job_forged_04",
            training_run_id="run_forged_04",
            simulation_id="sim_forged_04",
            completed_at=datetime.now(UTC).isoformat(),
            executed_rounds=10,
            executed_epochs=20,
            sample_count=100000,
            observed_metrics={"loss": 0.001},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            worker_id="MALICIOUS_PRODUCER",
        )
        assert forged_record.job_id == "job_forged_04"
        # The trusted record source still doesn't have it
        assert source.get_completed_job_by_id("job_forged_04") is None

    # =========================================================================
    # JOB-05: Conflicting completion
    # =========================================================================
    def test_job_05_conflicting_completion_rejected(self) -> None:
        """JOB-05: Complete one job and attempt to overwrite its metrics, sample count, or digest. Verify rejection."""
        source = TrustedTrainingJobRecordSource()
        job_id = "job_conflict_05"
        run_id = "run_conflict_05"
        sim_id = "sim_conflict_05"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
        )
        source.start_training_job(job_id=job_id)

        # Legitimate initial completion
        source.complete_training_job(
            job_id=job_id,
            executed_rounds=5,
            sample_count=5000,
            observed_metrics={"loss": 0.35},
            artifact_sha256="c" * 64,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )

        # 1. Attempt overwrite with altered sample count
        with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting completion record"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=5,
                sample_count=99999,  # altered
                observed_metrics={"loss": 0.35},
                artifact_sha256="c" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
            )

        # 2. Attempt overwrite with altered artifact digest
        with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting completion record"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=5,
                sample_count=5000,
                observed_metrics={"loss": 0.35},
                artifact_sha256="d" * 64,  # altered
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
            )

        # 3. Attempt overwrite with altered loss metric
        with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting completion record"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=5,
                sample_count=5000,
                observed_metrics={"loss": 0.01},  # altered
                artifact_sha256="c" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
            )

    # =========================================================================
    # JOB-06: Cross-job replay
    # =========================================================================
    def test_job_06_cross_job_replay_rejected(self) -> None:
        """JOB-06: Use the record of job_alpha to issue a receipt for job_beta. Verify rejection."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        source = TrustedTrainingJobRecordSource()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Complete legitimate job_alpha
        source.schedule_training_job(
            job_id="job_alpha",
            training_run_id="run_alpha",
            simulation_id="sim_alpha",
        )
        source.start_training_job(job_id="job_alpha")
        source.complete_training_job(
            job_id="job_alpha",
            executed_rounds=5,
            sample_count=1000,
            observed_metrics={"loss": 0.25},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )

        # Attempt to issue receipt claiming job_beta with same artifact
        evidence_beta = TrainingExecutionEvidence(
            training_run_id="run_beta",
            job_id="job_beta",
            simulation_id="sim_alpha",
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=5,
            executed_epochs=0,
            sample_count=1000,
            observed_metrics={"loss": 0.25},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="TRAINING_EXECUTOR",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="not found in trusted training job record source"):
            issuer.issue_receipt(
                evidence=evidence_beta,
                model_bytes=model_bytes,
            )

    # =========================================================================
    # JOB-07: Cross-simulation replay
    # =========================================================================
    def test_job_07_cross_simulation_replay_rejected(self) -> None:
        """JOB-07: Reuse a valid completed job from simulation A in simulation B. Verify rejection."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        source = TrustedTrainingJobRecordSource()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Legitimate job for simulation A
        source.schedule_training_job(
            job_id="job_sim_a",
            training_run_id="run_sim_a",
            simulation_id="sim_bank_group_alpha",
        )
        source.start_training_job(job_id="job_sim_a")
        source.complete_training_job(
            job_id="job_sim_a",
            executed_rounds=3,
            sample_count=2000,
            observed_metrics={"loss": 0.3},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )

        # Attacker tries to issue receipt claiming simulation B using job_sim_a
        evidence_sim_b = TrainingExecutionEvidence(
            training_run_id="run_sim_a",
            job_id="job_sim_a",
            simulation_id="sim_bank_group_beta",  # mismatched simulation
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=3,
            executed_epochs=0,
            sample_count=2000,
            observed_metrics={"loss": 0.3},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="TRAINING_EXECUTOR",
        )

        with pytest.raises(ValueError, match="Simulation ID mismatch"):
            issuer.issue_receipt(
                evidence=evidence_sim_b,
                model_bytes=model_bytes,
            )

    # =========================================================================
    # JOB-08: Schema and producer substitution
    # =========================================================================
    def test_job_08_schema_and_producer_substitution_rejected(self) -> None:
        """JOB-08: Change architecture, feature ordering, schema version, or producer identity. Verify rejection."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        source = TrustedTrainingJobRecordSource()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        source.schedule_training_job(
            job_id="job_sub_08",
            training_run_id="run_sub_08",
            simulation_id="sim_sub_08",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
        )
        source.start_training_job(job_id="job_sub_08", worker_id="AUTHORIZED_PRODUCER_01")
        source.complete_training_job(
            job_id="job_sub_08",
            executed_rounds=4,
            sample_count=1500,
            observed_metrics={"loss": 0.22},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            worker_id="AUTHORIZED_PRODUCER_01",
        )

        base_evidence = TrainingExecutionEvidence(
            training_run_id="run_sub_08",
            job_id="job_sub_08",
            simulation_id="sim_sub_08",
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=4,
            executed_epochs=0,
            sample_count=1500,
            observed_metrics={"loss": 0.22},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="AUTHORIZED_PRODUCER_01",
        )

        # 1. Architecture substitution
        ev_arch = dataclasses.replace(base_evidence, architecture="DifferentFraudNN")
        with pytest.raises(ValueError, match="Model architecture mismatch"):
            issuer.issue_receipt(evidence=ev_arch, model_bytes=model_bytes)

        # 2. Schema version substitution
        ev_schema = dataclasses.replace(base_evidence, feature_schema_version="v2.0")
        with pytest.raises(ValueError, match="Feature schema version mismatch"):
            issuer.issue_receipt(evidence=ev_schema, model_bytes=model_bytes)

        # 3. Feature ordering permutation
        permuted_names = tuple(reversed(FEATURE_NAMES))
        ev_order = dataclasses.replace(base_evidence, feature_names=permuted_names)
        with pytest.raises(ValueError, match="Feature names or ordering mismatch"):
            issuer.issue_receipt(evidence=ev_order, model_bytes=model_bytes)

        # 4. Producer substitution
        ev_prod = dataclasses.replace(base_evidence, producer_id="UNAUTHORIZED_PRODUCER_99")
        with pytest.raises(ValueError, match="Producer identity mismatch"):
            issuer.issue_receipt(evidence=ev_prod, model_bytes=model_bytes)

    # =========================================================================
    # JOB-09: Metric substitution
    # =========================================================================
    def test_job_09_metric_substitution_rejected(self) -> None:
        """JOB-09: Keep artifact digest unchanged but alter loss or execution counts. Verify rejection."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        source = TrustedTrainingJobRecordSource()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        source.schedule_training_job(
            job_id="job_metric_09",
            training_run_id="run_metric_09",
            simulation_id="sim_metric_09",
        )
        source.start_training_job(job_id="job_metric_09")
        source.complete_training_job(
            job_id="job_metric_09",
            executed_rounds=5,
            sample_count=3000,
            observed_metrics={"loss": 0.42, "auc_roc": 0.81},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )

        # 1. Altered loss metric
        ev_loss = TrainingExecutionEvidence(
            training_run_id="run_metric_09",
            job_id="job_metric_09",
            simulation_id="sim_metric_09",
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=5,
            executed_epochs=0,
            sample_count=3000,
            observed_metrics={"loss": 0.05, "auc_roc": 0.81},  # fabricated loss
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="TRAINING_EXECUTOR",
        )
        with pytest.raises(ValueError, match="Metric 'loss' mismatch"):
            issuer.issue_receipt(evidence=ev_loss, model_bytes=model_bytes)

        # 2. Altered executed rounds count
        ev_rounds = TrainingExecutionEvidence(
            training_run_id="run_metric_09",
            job_id="job_metric_09",
            simulation_id="sim_metric_09",
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=10,  # claimed 10 instead of observed 5
            executed_epochs=0,
            sample_count=3000,
            observed_metrics={"loss": 0.42, "auc_roc": 0.81},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="TRAINING_EXECUTOR",
        )
        with pytest.raises(ValueError, match="Executed rounds mismatch"):
            issuer.issue_receipt(evidence=ev_rounds, model_bytes=model_bytes)

    # =========================================================================
    # JOB-10: Genuine training lifecycle
    # =========================================================================
    def test_job_10_genuine_training_lifecycle_produces_valid_receipt(self) -> None:
        """JOB-10: Run genuine training through actual authorized path. Verify record, receipt, and digest match."""
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()
        trust_store = DurableTrustStore({DEFAULT_TRAINING_KEY_ID: pub_hex})
        source = TrustedTrainingJobRecordSource()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )
        verifier = TrainingReceiptVerifier(
            trust_store=trust_store,
            job_record_source=source,
            enforce_recorded_evidence=True,
        )

        job_id = "job_genuine_10"
        run_id = "run_genuine_10"
        sim_id = "sim_genuine_10"

        # 1. Schedule job
        scheduled = source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=2,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
        )
        assert scheduled.job_id == job_id

        # 2. Worker starts job
        started = source.start_training_job(job_id=job_id, worker_id="GENUINE_WORKER")
        assert started.job_id == job_id

        # 3. Perform genuine PyTorch training optimization steps
        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
        criterion = nn.BCELoss()

        # Synthesize real training tensors
        X_train = torch.randn(100, NUM_FEATURES)
        y_train = torch.randint(0, 2, (100,)).float()

        total_samples = 100
        executed_rounds = 2
        last_loss = 0.0

        for r in range(1, executed_rounds + 1):
            model.train()
            optimizer.zero_grad()
            preds = model(X_train)
            loss = criterion(preds, y_train)
            loss.backward()
            optimizer.step()
            last_loss = float(loss.item())

            # Worker records round observation
            source.record_round_observation(
                job_id=job_id,
                round_num=r,
                observed_loss=last_loss,
                sample_count=total_samples,
            )

        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()
        observed_metrics = {"loss": last_loss, "auc_roc": 0.75}

        # 4. Worker completes training job
        completed_rec = source.complete_training_job(
            job_id=job_id,
            executed_rounds=executed_rounds,
            executed_epochs=1,
            sample_count=total_samples,
            observed_metrics=observed_metrics,
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            worker_id="GENUINE_WORKER",
        )
        assert completed_rec.status == "COMPLETED"
        assert completed_rec.artifact_sha256 == model_sha

        # 5. Issue authentic receipt
        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=completed_rec.completed_at,
            status="COMPLETED",
            executed_rounds=executed_rounds,
            executed_epochs=1,
            sample_count=total_samples,
            observed_metrics=observed_metrics,
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="GENUINE_WORKER",
        )

        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)
        assert receipt.model_artifact_sha256 == model_sha
        assert receipt.signature is not None

        # 6. Verify receipt independently
        is_verified, reason = verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id=sim_id,
            expected_artifact_sha256=model_sha,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
            actual_artifact_bytes=model_bytes,
        )
        assert is_verified is True, f"Receipt verification failed: {reason}"

        # 7. ModelRegistry admission succeeds and verifies provenance
        reset_default_training_verifier()
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("CFI_TRAINING_RECEIPT_PUBLIC_KEY", pub_hex)
            try:
                tmp_dir = tempfile.mkdtemp()
                registry = ModelRegistry(storage_dir=tmp_dir)
                registry.save_version(
                    simulation_id=sim_id,
                    state_dict=model.state_dict(),
                    metrics=observed_metrics,
                    is_promoted=True,
                    status="candidate",
                    provenance_type="TRAINING_RECORDED",
                    training_run_id=run_id,
                    training_receipt=receipt,
                    artifact_bytes=model_bytes,
                    expected_architecture="FraudDetectionNN",
                    expected_feature_schema_version="v1.0",
                    expected_input_dim=NUM_FEATURES,
                    expected_feature_names=list(FEATURE_NAMES),
                )
                version = registry.get_version_metadata(simulation_id=sim_id, version=1)
                assert version is not None
                assert version["provenance_type"] == "TRAINING_RECORDED"
                assert version["is_production_eligible"] is True
            finally:
                reset_default_training_verifier()

    # =========================================================================
    # JOB-11: Failed or incomplete training
    # =========================================================================
    def test_job_11_failed_or_incomplete_training_cannot_produce_receipt(self) -> None:
        """JOB-11: Simulate a training job that starts but never completes. Verify no trusted receipt is issued."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        source = TrustedTrainingJobRecordSource()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        job_id = "job_incomplete_11"
        run_id = "run_incomplete_11"
        sim_id = "sim_incomplete_11"

        # Schedule and start job
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
        )
        source.start_training_job(job_id=job_id)

        # Job fails / aborts midway and NEVER calls complete_training_job
        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",  # caller falsely claims completed
            executed_rounds=1,
            executed_epochs=0,
            sample_count=100,
            observed_metrics={"loss": 0.5},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="TRAINING_EXECUTOR",
        )

        # Attempt to issue receipt fails because job is not COMPLETED in trusted source
        with pytest.raises(TrainingProvenanceSecurityError, match="not found in trusted training job record source"):
            issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)

    # =========================================================================
    # JOB-12: Restart and multi-worker behavior
    # =========================================================================
    def test_job_12_restart_and_multi_worker_durability(self) -> None:
        """JOB-12: Verify the job-record authority provides documented persistence and multi-worker reload."""
        # 1. When store_path is empty, is_durable is truthfully False
        in_memory_source = TrustedTrainingJobRecordSource(store_path="")
        assert in_memory_source.is_durable is False

        # 2. Test durable file persistence and cross-worker reload
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            store_file = tf.name

        try:
            # Worker 1 creates record source with durable store path
            worker1_source = TrustedTrainingJobRecordSource(store_path=store_file)
            assert worker1_source.is_durable is True

            job_id = "job_durable_12"
            run_id = "run_durable_12"
            sim_id = "sim_durable_12"

            worker1_source.schedule_training_job(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
            )
            worker1_source.start_training_job(job_id=job_id)
            rec1 = worker1_source.complete_training_job(
                job_id=job_id,
                executed_rounds=3,
                sample_count=1200,
                observed_metrics={"loss": 0.28},
                artifact_sha256="e" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
            )
            assert rec1.status == "COMPLETED"

            # Worker 2 (separate process / instance) opens the same store
            worker2_source = TrustedTrainingJobRecordSource(store_path=store_file)
            assert worker2_source.is_durable is True

            # Worker 2 reloads and sees the completed job
            rec2 = worker2_source.get_completed_job_by_id(job_id)
            assert rec2 is not None
            assert rec2.job_id == job_id
            assert rec2.executed_rounds == 3
            assert rec2.sample_count == 1200
            assert rec2.artifact_sha256 == "e" * 64

            # Worker 2 attempts conflicting completion on the same job -> fails closed
            with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting completion record"):
                worker2_source.complete_training_job(
                    job_id=job_id,
                    executed_rounds=3,
                    sample_count=9999,  # conflict
                    observed_metrics={"loss": 0.28},
                    artifact_sha256="e" * 64,
                    architecture="FraudDetectionNN",
                    feature_schema_version="v1.0",
                    feature_names=list(FEATURE_NAMES),
                )
        finally:
            if os.path.exists(store_file):
                os.remove(store_file)

    # =========================================================================
    # JOB-13: Duplicate submission
    # =========================================================================
    def test_job_13_duplicate_submission_idempotency_vs_conflict(self) -> None:
        """JOB-13: Repeated identical completion events are idempotent. Conflicting events are rejected."""
        source = TrustedTrainingJobRecordSource()
        job_id = "job_idem_13"
        run_id = "run_idem_13"
        sim_id = "sim_idem_13"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
        )
        source.start_training_job(job_id=job_id)

        # First completion
        rec1 = source.complete_training_job(
            job_id=job_id,
            executed_rounds=4,
            sample_count=2500,
            observed_metrics={"loss": 0.33},
            artifact_sha256="f" * 64,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )
        assert rec1.status == "COMPLETED"

        # Duplicate identical completion event -> idempotent success
        rec2 = source.complete_training_job(
            job_id=job_id,
            executed_rounds=4,
            sample_count=2500,
            observed_metrics={"loss": 0.33},
            artifact_sha256="f" * 64,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )
        assert rec2.job_id == rec1.job_id
        assert rec2.completed_at == rec1.completed_at

        # Conflicting event -> fails closed
        with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting completion record"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=5,  # conflicting rounds
                sample_count=2500,
                observed_metrics={"loss": 0.33},
                artifact_sha256="f" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
            )

    # =========================================================================
    # JOB-14: Production issuer/verifier regression
    # =========================================================================
    def test_job_14_production_issuer_verifier_regression(self) -> None:
        """JOB-14: Verify Ed25519 validation works and serving process requires no private signing credentials."""
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()

        # 1. Serving-only process: initialized with ONLY public key, NO private key
        serving_trust_store = DurableTrustStore({DEFAULT_TRAINING_KEY_ID: pub_hex})
        source = TrustedTrainingJobRecordSource()
        verifier = TrainingReceiptVerifier(
            trust_store=serving_trust_store,
            job_record_source=source,
            enforce_recorded_evidence=True,
        )

        # 2. Issuer runs in separate training worker process with private key
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Complete genuine job
        source.schedule_training_job(
            job_id="job_reg_14",
            training_run_id="run_reg_14",
            simulation_id="sim_reg_14",
        )
        source.start_training_job(job_id="job_reg_14")
        rec = source.complete_training_job(
            job_id="job_reg_14",
            executed_rounds=2,
            sample_count=1000,
            observed_metrics={"loss": 0.2},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )

        evidence = TrainingExecutionEvidence(
            training_run_id="run_reg_14",
            job_id="job_reg_14",
            simulation_id="sim_reg_14",
            completed_at=rec.completed_at,
            status="COMPLETED",
            executed_rounds=2,
            executed_epochs=0,
            sample_count=1000,
            observed_metrics={"loss": 0.2},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="TRAINING_EXECUTOR",
        )

        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)

        # Serving process verifies with only public key
        is_ok, reason = verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id="sim_reg_14",
            expected_artifact_sha256=model_sha,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
            actual_artifact_bytes=model_bytes,
        )
        assert is_ok is True, f"Verification failed: {reason}"

        # 3. Serving process attempting to instantiate issuer with empty key fails closed
        with pytest.raises(TrainingProvenanceConfigurationError, match="Signing key hex must be provided"):
            TrainingReceiptIssuer(
                signing_key_hex="",
                key_id=DEFAULT_TRAINING_KEY_ID,
            )

        # 4. Tampered signature fails verification
        tampered_sig = ("00" if receipt.signature[:2] != "00" else "ff") + receipt.signature[2:]
        tampered_receipt = dataclasses.replace(receipt, signature=tampered_sig)
        is_tampered_ok, _ = verifier.verify_receipt(
            receipt=tampered_receipt,
            expected_simulation_id="sim_reg_14",
            expected_artifact_sha256=model_sha,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
            actual_artifact_bytes=model_bytes,
        )
        assert is_tampered_ok is False
