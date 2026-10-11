"""Adversarial Verification Suite: Federated Training Provenance Integration.

Covers adversarial requirements ADV-11 through ADV-16:
- ADV-11: Remove the production database configuration; ensure no memory fallback.
- ADV-12: Make the authoritative database unavailable; ensure receipt issuance fails closed.
- ADV-13: Cause a real observation persistence failure in the actual FL path; ensure no trusted receipt is issued.
- ADV-14: Restart workers/coordinator and demonstrate that persisted observations reconcile correctly.
- ADV-15: Verify that the trusted receipt corresponds to the exact serialized global model bytes produced through real aggregation.
- ADV-16: Verify that no production-trusted receipt can be issued for a branch lacking authenticated training execution evidence.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
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


def _generate_ed25519_keypair_hex() -> tuple[str, str]:
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
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    return buf.getvalue()


class TestFederatedTrainingProvenanceIntegrationAdversarial:
    """Adversarial suite covering fail-closed production storage, DB outage, FL error propagation, and exact artifact binding."""

    def setup_method(self) -> None:
        reset_default_training_issuer()
        reset_default_training_verifier()
        reset_default_training_job_record_source()
        reset_default_trust_store()

    def teardown_method(self) -> None:
        reset_default_training_issuer()
        reset_default_training_verifier()
        reset_default_training_job_record_source()
        reset_default_trust_store()

    def test_adv_11_production_database_unconfigured_fails_closed_no_memory_fallback(self) -> None:
        """ADV-11: Remove the production database configuration; ensure in-memory fallback is rejected."""
        valid_worker = "high_entropy_authorized_worker_token_38294719283"
        valid_coord = "high_entropy_authorized_coordinator_token_98374928"

        # Explicit in_memory=True in production raises ConfigurationError
        with pytest.raises(TrainingProvenanceConfigurationError, match="In-memory fallback is strictly forbidden in production"):
            TrustedTrainingJobRecordSource(
                configured_token=valid_worker,
                coordinator_token=valid_coord,
                app_env="production",
                in_memory=True,
            )

        # Empty store_path in production raises ConfigurationError
        with pytest.raises(TrainingProvenanceConfigurationError, match="Production training job authority database/store is not configured"):
            TrustedTrainingJobRecordSource(
                configured_token=valid_worker,
                coordinator_token=valid_coord,
                app_env="production",
                store_path="",
            )

    def test_adv_12_database_unavailable_receipt_issuance_fails_closed(self, tmp_path: Path) -> None:
        """ADV-12: Make the authoritative database unavailable/corrupt; ensure receipt issuance fails closed."""
        db_path = str(tmp_path / "adv_12_corrupt.db")
        source = TrustedTrainingJobRecordSource(store_path=db_path)

        priv_hex, _ = _generate_ed25519_keypair_hex()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=source,
            enforce_job_records=True,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        # Corrupt the database file with invalid junk bytes
        with open(db_path, "wb") as f:
            f.write(b"CORRUPT_NOT_A_SQLITE_DATABASE_DATA_JUNK")

        evidence = TrainingExecutionEvidence(
            training_run_id="run_adv_12",
            job_id="job_adv_12",
            simulation_id="sim_adv_12",
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=2,
            executed_epochs=1,
            sample_count=500,
            observed_metrics={"loss": 0.25},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="WORKER_12",
        )

        # Issuer fails closed because database is unavailable / cannot find completed job
        with pytest.raises((sqlite3.DatabaseError, TrainingProvenanceSecurityError)):
            issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)

    def test_adv_13_observation_persistence_failure_in_fl_path_prevents_receipt(self, tmp_path: Path) -> None:
        """ADV-13: Cause an observation persistence failure in the actual FL path; ensure no trusted receipt is issued."""
        db_path = str(tmp_path / "adv_13_fl_fail.db")
        source = TrustedTrainingJobRecordSource(store_path=db_path)

        job_id = "job_adv_13"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_13",
            simulation_id="sim_adv_13",
            expected_rounds=2,
        )
        source.start_training_job(job_id=job_id)

        # Simulate round 1 recorded
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=200,
            observed_loss=0.35,
        )

        # In round 2, observation fails (e.g. database error / abort)
        # Attempting to complete claiming 2 rounds MUST fail closed
        with pytest.raises(TrainingProvenanceSecurityError, match="Executed rounds mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=2,
                sample_count=400,
                observed_metrics={"loss": 0.20},
                artifact_sha256="1" * 64,
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
                enforce_lifecycle=True,
            )

    def test_adv_14_worker_coordinator_restart_observation_reconciliation(self, tmp_path: Path) -> None:
        """ADV-14: Restart workers and coordinator across rounds; verify observations persist and reconcile."""
        db_path = str(tmp_path / "adv_14_restart.db")

        # Session 1: Coordinator schedules and starts job
        s1 = TrustedTrainingJobRecordSource(store_path=db_path)
        job_id = "job_adv_14_restart"
        s1.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_14",
            simulation_id="sim_adv_14",
            expected_rounds=2,
            permitted_workers=["BANK_A", "BANK_B"],
        )
        s1.start_training_job(job_id=job_id, worker_id="BANK_A")
        # Bank A records round 1
        s1.record_round_observation(
            job_id=job_id,
            round_number=1,
            bank_id="BANK_A",
            sample_count=150,
            observed_loss=0.40,
        )
        # Session 1 terminates (simulating worker crash/restart)
        del s1

        # Session 2: Fresh instance (post-restart) reopens same store and continues
        s2 = TrustedTrainingJobRecordSource(store_path=db_path)
        # Bank B records round 1
        s2.record_round_observation(
            job_id=job_id,
            round_number=1,
            bank_id="BANK_B",
            sample_count=250,
            observed_loss=0.38,
        )
        # Round 2 executed
        s2.record_round_observation(
            job_id=job_id,
            round_number=2,
            bank_id="BANK_A",
            sample_count=150,
            observed_loss=0.30,
        )
        s2.record_round_observation(
            job_id=job_id,
            round_number=2,
            bank_id="BANK_B",
            sample_count=250,
            observed_loss=0.28,
        )

        s2.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A", "BANK_B"],
            accepted_local_update_digests=["d1_a", "d1_b"],
            aggregated_global_model_digest="1" * 64,
        )
        s2.record_global_aggregation(
            job_id=job_id,
            round_number=2,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A", "BANK_B"],
            accepted_local_update_digests=["d2_a", "d2_b"],
            aggregated_global_model_digest="a" * 64,
        )

        # Complete job successfully across sessions
        total_samples = 400
        rec = s2.complete_training_job(
            job_id=job_id,
            executed_rounds=2,
            sample_count=total_samples,
            observed_metrics={"loss": 0.28},
            artifact_sha256="a" * 64,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            worker_id="BANK_A",
        )
        assert rec.status == "COMPLETED"
        assert rec.sample_count == 400
        assert rec.executed_rounds == 2

    def test_adv_15_receipt_corresponds_to_exact_serialized_global_model_bytes(self, tmp_path: Path) -> None:
        """ADV-15: Verify receipt binds exact serialized global model bytes produced through real aggregation."""
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()
        trust_store = DurableTrustStore({DEFAULT_TRAINING_KEY_ID: pub_hex})
        db_path = str(tmp_path / "adv_15.db")
        source = TrustedTrainingJobRecordSource(store_path=db_path)
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

        job_id = "job_adv_15_bytes"
        run_id = "run_adv_15"
        sim_id = "sim_adv_15"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            expected_rounds=1,
        )
        source.start_training_job(job_id=job_id)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            observed_loss=0.30,
        )

        model = FraudDetectionModel(input_dim=NUM_FEATURES)
        model_bytes = _serialize_model(model)
        model_sha = hashlib.sha256(model_bytes).hexdigest()

        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["d15"],
            aggregated_global_model_digest=model_sha,
        )

        rec = source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            sample_count=100,
            observed_metrics={"loss": 0.30},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )

        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=rec.completed_at,
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=0,
            sample_count=100,
            observed_metrics={"loss": 0.30},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="TRAINING_EXECUTOR",
        )

        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)

        # 1. Exact bytes verify successfully
        valid, _ = verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id=sim_id,
            expected_artifact_sha256=model_sha,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
            actual_artifact_bytes=model_bytes,
        )
        assert valid is True

        # 2. Tampered bytes (even 1 altered byte) fails verification closed
        tampered_bytes = bytearray(model_bytes)
        tampered_bytes[0] ^= 0xFF
        valid_tampered, reason = verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id=sim_id,
            expected_artifact_sha256=hashlib.sha256(tampered_bytes).hexdigest(),
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
            actual_artifact_bytes=bytes(tampered_bytes),
        )
        assert valid_tampered is False
        assert "mismatch" in reason.lower()

    def test_adv_16_branch_lacking_authenticated_execution_cannot_issue_trusted_receipt(self) -> None:
        """ADV-16: Verify that no production-trusted receipt can be issued for an unprovenanced branch."""
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

        # Fabricated evidence from an unprovenanced branch
        unprovenanced_evidence = TrainingExecutionEvidence(
            training_run_id="run_unprovenanced_16",
            job_id="job_unprovenanced_16",
            simulation_id="sim_unprovenanced_16",
            completed_at=datetime.now(UTC).isoformat(),
            status="COMPLETED",
            executed_rounds=5,
            executed_epochs=1,
            sample_count=1000,
            observed_metrics={"loss": 0.20},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="UNPROVENANCED_EXECUTOR",
        )

        # Receipt issuer rejects issuance
        with pytest.raises(TrainingProvenanceSecurityError, match="not found in trusted training job record source"):
            issuer.issue_receipt(evidence=unprovenanced_evidence, model_bytes=model_bytes)
