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
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from typing import Any, Generator

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


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
    """Provide embedded real PostgreSQL 18.1 instance."""
    try:
        import pg0
    except ImportError:
        pytest.skip("pg0 is not installed (PostgreSQL 18.1 embedded authority unavailable)")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pytest.skip("psycopg is not installed (PostgreSQL driver unavailable)")

    pg = pg0.Pg0()
    pg.start()
    try:
        assert pg.uri is not None
        yield pg.uri
    finally:
        pg.stop()


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
    def test_job_02_unauthorized_completion_rejected(self, real_pg_instance: str) -> None:
        """JOB-02: Attempt to register a completed job using an untrusted caller. Verify rejection."""
        authorized_worker_token = "high_entropy_authorized_worker_token_38294719283"
        authorized_coordinator_token = "high_entropy_authorized_coordinator_token_98374928"
        source = TrustedTrainingJobRecordSource(
            configured_token=authorized_worker_token,
            coordinator_token=authorized_coordinator_token,
            db_url=real_pg_instance,
            app_env="production",
        )

        job_id = "job_sec_02"
        run_id = "run_sec_02"
        sim_id = "sim_sec_02"

        # Worker credential cannot authorize job scheduling; coordinator role required
        with pytest.raises(TrainingProvenanceSecurityError, match="Worker credential cannot authorize job scheduling"):
            source.schedule_training_job(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                coordinator_token=authorized_worker_token,
            )

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=authorized_coordinator_token,
        )

        # Coordinator token cannot authorize worker execution
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker"):
            source.start_training_job(
                job_id=job_id,
                worker_token=authorized_coordinator_token,
            )

        source.start_training_job(
            job_id=job_id,
            worker_token=authorized_worker_token,
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
    def test_job_03_authorized_token_nonexistent_execution_rejected(self, real_pg_instance: str) -> None:
        """JOB-03: Present a valid worker credential but no genuine scheduled/started training job. Verify rejection."""
        authorized_worker_token = "high_entropy_authorized_worker_token_38294719283"
        authorized_coordinator_token = "high_entropy_authorized_coordinator_token_98374928"
        source = TrustedTrainingJobRecordSource(
            configured_token=authorized_worker_token,
            coordinator_token=authorized_coordinator_token,
            db_url=real_pg_instance,
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
                worker_token=authorized_worker_token,
                enforce_lifecycle=True,
            )

        # Attempt to complete a job that was scheduled but never started by worker
        source.schedule_training_job(
            job_id="job_scheduled_only",
            training_run_id="run_scheduled_only",
            simulation_id="sim_scheduled_only",
            coordinator_token=authorized_coordinator_token,
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
                worker_token=authorized_worker_token,
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
        """JOB-10: Genuine 3-node federated training orchestration, real PyTorch optimization, measured AUC, receipt, and registry."""
        from sklearn.metrics import roc_auc_score

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

        # 1. Schedule federated training job with 3 participating banks and 2 expected global rounds
        participating_banks = ["BANK_A", "BANK_B", "BANK_C"]
        scheduled = source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=2,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=participating_banks,
        )
        assert scheduled.job_id == job_id

        # 2. Worker pool starts job
        started = source.start_training_job(job_id=job_id, worker_id="BANK_A")
        assert started.job_id == job_id

        # 3. Create isolated local datasets for 3 independent bank nodes (zero pooled raw data)
        torch.manual_seed(42)
        # Bank A: 120 samples
        X_bank_a = torch.randn(120, NUM_FEATURES)
        y_bank_a = torch.randint(0, 2, (120,)).float()
        # Bank B: 150 samples
        X_bank_b = torch.randn(150, NUM_FEATURES)
        y_bank_b = torch.randint(0, 2, (150,)).float()
        # Bank C: 180 samples
        X_bank_c = torch.randn(180, NUM_FEATURES)
        y_bank_c = torch.randint(0, 2, (180,)).float()

        # Holdout validation/test set: 100 samples
        X_test = torch.randn(100, NUM_FEATURES)
        y_test = torch.randint(0, 2, (100,)).float()

        client_data = {
            "BANK_A": (X_bank_a, y_bank_a),
            "BANK_B": (X_bank_b, y_bank_b),
            "BANK_C": (X_bank_c, y_bank_c),
        }

        # Initialize global model
        global_model = FraudDetectionModel(input_dim=NUM_FEATURES)
        executed_rounds = 2
        total_unique_samples = sum(len(x) for x, _ in client_data.values())  # 450 unique samples
        criterion = nn.BCELoss()

        # 4. Execute 2 global federated rounds with local PyTorch training and FedAvg aggregation
        last_round_local_losses: dict[str, float] = {}
        for r in range(1, executed_rounds + 1):
            local_weights = {}
            local_sample_counts = {}

            # Each bank trains locally on its private partition
            for bank_id, (X_local, y_local) in client_data.items():
                local_model = FraudDetectionModel(input_dim=NUM_FEATURES)
                local_model.load_state_dict(global_model.state_dict())
                local_model.train()
                optimizer = torch.optim.Adam(local_model.parameters(), lr=0.01)

                # Local optimization step
                optimizer.zero_grad()
                preds = local_model(X_local)
                loss = criterion(preds, y_local)
                loss.backward()
                optimizer.step()
                local_loss = float(loss.item())
                last_round_local_losses[bank_id] = local_loss

                local_weights[bank_id] = {k: v.clone() for k, v in local_model.state_dict().items()}
                local_sample_counts[bank_id] = len(X_local)

                # Record authoritative round observation for this bank
                source.record_round_observation(
                    job_id=job_id,
                    round_number=r,
                    bank_id=bank_id,
                    sample_count=len(X_local),
                    actual_executed_epochs=1,
                    observed_loss=local_loss,
                    metrics={"loss": local_loss, "round": float(r)},
                )

            # Coordinator aggregates model updates via FedAvg (weighted average)
            # Coordinator verifies zero leakage: it receives only state dicts, not raw data
            new_global_state = {}
            for param_key in global_model.state_dict():
                weighted_sum = sum(
                    local_weights[b][param_key].float() * local_sample_counts[b]
                    for b in participating_banks
                )
                new_global_state[param_key] = weighted_sum / total_unique_samples
            global_model.load_state_dict(new_global_state)

            round_model_bytes = _serialize_model(global_model)
            round_model_sha = hashlib.sha256(round_model_bytes).hexdigest()
            source.record_global_aggregation(
                job_id=job_id,
                round_number=r,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=participating_banks,
                accepted_local_update_digests=[hashlib.sha256(f"update_{b}_{r}".encode()).hexdigest() for b in participating_banks],
                aggregated_global_model_digest=round_model_sha,
            )

        # 5. Measure test evaluation metrics on global holdout set (actual measured loss and AUC)
        global_model.eval()
        with torch.no_grad():
            test_preds = global_model(X_test)
            test_loss = float(criterion(test_preds, y_test).item())
            measured_auc = float(roc_auc_score(y_test.numpy(), test_preds.numpy()))

        assert math.isfinite(test_loss)
        assert 0.0 <= measured_auc <= 1.0

        model_bytes = _serialize_model(global_model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="val_loss",
            metric_value=test_loss,
            evaluated_artifact_digest=model_sha,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="auc_roc",
            metric_value=measured_auc,
            evaluated_artifact_digest=model_sha,
        )

        weighted_final_training_loss = sum(
            last_round_local_losses[b] * len(client_data[b][0]) for b in participating_banks
        ) / total_unique_samples
        observed_metrics = {
            "loss": weighted_final_training_loss,
            "val_loss": test_loss,
            "auc_roc": measured_auc,
        }

        # 6. Worker completes training job reconciled against observations
        completed_rec = source.complete_training_job(
            job_id=job_id,
            executed_rounds=executed_rounds,
            executed_epochs=executed_rounds,
            sample_count=total_unique_samples,
            observed_metrics=observed_metrics,
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            worker_id="BANK_A",
        )
        assert completed_rec.status == "COMPLETED"
        assert completed_rec.artifact_sha256 == model_sha
        assert completed_rec.sample_count == 450
        assert completed_rec.executed_rounds == 2

        # 7. Issue authentic Ed25519 receipt
        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=completed_rec.completed_at,
            status="COMPLETED",
            executed_rounds=executed_rounds,
            executed_epochs=executed_rounds,
            sample_count=total_unique_samples,
            observed_metrics=observed_metrics,
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="BANK_A",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)
        assert receipt.model_artifact_sha256 == model_sha
        assert receipt.signature is not None

        # 8. Verify receipt independently with public key only
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

        # 9. ModelRegistry admission succeeds and verifies provenance
        reset_default_training_verifier()
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("CFI_TRAINING_RECEIPT_PUBLIC_KEY", pub_hex)
            try:
                tmp_dir = tempfile.mkdtemp()
                registry = ModelRegistry(storage_dir=tmp_dir)
                registry.save_version(
                    simulation_id=sim_id,
                    state_dict=global_model.state_dict(),
                    metrics=observed_metrics,
                    is_promoted=False,
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
                assert version["status"] == "candidate"
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
    def test_job_12_restart_and_multi_worker_durability(self, tmp_path: Any) -> None:
        """JOB-12: Verify multi-process persistence, independent PIDs, durable restart, and conflict rejection."""
        import subprocess
        import sys

        # 1. When store_path is empty, is_durable is truthfully False
        in_memory_source = TrustedTrainingJobRecordSource(store_path="")
        assert in_memory_source.is_durable is False

        # 2. Test durable SQLite persistence across independent OS processes with distinct PIDs
        db_file = str(tmp_path / "job_durability_12.db")
        job_id = "job_durable_12"
        run_id = "run_durable_12"
        sim_id = "sim_durable_12"

        backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        # Worker 1 runs in independent OS process A
        worker1_code = f"""
import os, sys, json
if {backend_dir!r} not in sys.path:
    sys.path.insert(0, {backend_dir!r})
from app.application.services.training_provenance_service import TrustedTrainingJobRecordSource
from app.application.services.data_generator import FEATURE_NAMES

source = TrustedTrainingJobRecordSource(store_path={db_file!r})
source.schedule_training_job(
    job_id={job_id!r},
    training_run_id={run_id!r},
    simulation_id={sim_id!r},
    expected_rounds=3,
)
source.start_training_job(job_id={job_id!r})
for r in range(1, 4):
    source.record_round_observation(
        job_id={job_id!r},
        round_number=r,
        sample_count=400,
        actual_executed_epochs=1,
        observed_loss=0.28,
    )
    source.record_global_aggregation(
        job_id={job_id!r},
        round_number=r,
        aggregation_algorithm="fed_avg_weighted",
        accepted_participant_ids=["BANK_A"],
        accepted_local_update_digests=[f"digest_{{r}}"],
        aggregated_global_model_digest="e" * 64,
    )
rec = source.complete_training_job(
    job_id={job_id!r},
    executed_rounds=3,
    executed_epochs=3,
    sample_count=400,
    observed_metrics={{"loss": 0.28}},
    artifact_sha256="e" * 64,
    architecture="FraudDetectionNN",
    feature_schema_version="v1.0",
    feature_names=list(FEATURE_NAMES),
)
print(json.dumps({{"pid": os.getpid(), "status": rec.status, "rounds": rec.executed_rounds}}))
"""
        res1 = subprocess.run(
            [sys.executable, "-c", worker1_code],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.getcwd(),
        )
        out1 = json.loads(res1.stdout.strip().splitlines()[-1])
        pid1 = out1["pid"]
        assert out1["status"] == "COMPLETED"
        assert out1["rounds"] == 3
        assert pid1 != os.getpid()

        # Worker 2 runs in independent OS process B (restarts / opens existing store)
        worker2_code = f"""
import os, sys, json
if {backend_dir!r} not in sys.path:
    sys.path.insert(0, {backend_dir!r})
from app.application.services.training_provenance_service import (
    TrustedTrainingJobRecordSource,
    TrainingProvenanceSecurityError,
)
from app.application.services.data_generator import FEATURE_NAMES

source = TrustedTrainingJobRecordSource(store_path={db_file!r})
rec = source.get_completed_job_by_id({job_id!r})
assert rec is not None
assert rec.job_id == {job_id!r}
assert rec.sample_count == 400
assert rec.executed_rounds == 3
assert rec.artifact_sha256 == "e" * 64

# Attempt conflicting completion
conflict_failed = False
try:
    source.complete_training_job(
        job_id={job_id!r},
        executed_rounds=3,
        executed_epochs=3,
        sample_count=9999,
        observed_metrics={{"loss": 0.28}},
        artifact_sha256="e" * 64,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=list(FEATURE_NAMES),
    )
except TrainingProvenanceSecurityError:
    conflict_failed = True

print(json.dumps({{"pid": os.getpid(), "reloaded_id": rec.job_id, "conflict_rejected": conflict_failed}}))
"""
        res2 = subprocess.run(
            [sys.executable, "-c", worker2_code],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.getcwd(),
        )
        out2 = json.loads(res2.stdout.strip().splitlines()[-1])
        pid2 = out2["pid"]
        assert out2["reloaded_id"] == job_id
        assert out2["conflict_rejected"] is True
        assert pid2 != pid1
        assert pid2 != os.getpid()

        # 3. Parent process also validates persisted database state
        parent_source = TrustedTrainingJobRecordSource(store_path=db_file)
        rec_parent = parent_source.get_completed_job_by_id(job_id)
        assert rec_parent is not None
        assert rec_parent.job_id == job_id
        assert rec_parent.sample_count == 400

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
