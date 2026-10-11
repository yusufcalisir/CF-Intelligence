"""Comprehensive Failure Injection and Adversarial Suite for Federated Learning (FI-01 through FI-08).

Verifies Module 01-A.1 Requirement P7:
- FI-01: Observation database failure (prevents completion, no receipt, model cannot acquire provenance).
- FI-02: Worker crash (terminating worker mid-round enforces quorum policy, missing worker cannot be treated as completed).
- FI-03: Coordinator crash / transaction abort (atomic rollback leaves no partial completion in PostgreSQL).
- FI-04: Artifact substitution (tampered global model bytes rejected before receipt issuance).
- FI-05: Forged bank event (Bank A token claiming Bank B rejected by identity boundary).
- FI-06: PostgreSQL unavailability (database outage fails closed, no SQLite or in-memory fallback).
- FI-07: Replay (replaying round 1 update in round 2 rejected).
- FI-08: Evaluation manipulation (altering AUC or validation loss rejected).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Generator

import numpy as np
import pytest

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    _serialize_model_state_dict,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingExecutionEvidence,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrustedTrainingJobRecordSource,
)
from app.config import get_settings
from app.infrastructure.database.training_authority_backends import PostgresTrainingJobAuthority

COORD_SECRET = "coord_secret_fi_fl_9941_production"
BANK_A_SECRET = "bank_a_secret_fi_fl_1192_alpha"
BANK_B_SECRET = "bank_b_secret_fi_fl_2283_beta"
BANK_C_SECRET = "bank_c_secret_fi_fl_3374_gamma"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
    """Start embedded real PostgreSQL 18.1 instance and yield connection URI."""
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


class TestFederatedLearningFailureInjection:
    """Rigorous failure injection test suite exercising real PostgreSQL and FL components."""

    def test_fi_01_observation_database_failure(self, real_pg_instance: str, tmp_path) -> None:
        """FI-01: During an actual federated round, missing or unpersisted worker observations prevent completion."""
        job_id = f"job_fi01_{uuid.uuid4().hex[:8]}"
        run_id = f"run_fi01_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_fi01_{uuid.uuid4().hex[:8]}"

        np.random.seed(101)
        bank_partitions = {
            "BANK_A": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
            "BANK_B": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
            "BANK_C": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
        }
        holdout_val = (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32))

        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            app_env="production",
        )

        # With fail_bank_b_observation=True, Bank B fails to persist its observation
        # As a result, the training job cannot be completed, failing closed.
        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                bank_partitions=bank_partitions,
                holdout_val_data=holdout_val,
                num_rounds=1,
                fail_bank_b_observation=True,
                registry_storage_dir=str(tmp_path / "registry"),
            )
        assert (
            "Sample count mismatch" in str(exc_info.value)
            or "Required participant" in str(exc_info.value)
            or "observation" in str(exc_info.value).lower()
        )

    def test_fi_02_worker_crash_quorum_policy(self, real_pg_instance: str, tmp_path) -> None:
        """FI-02: Terminating Bank B during an active round enforces quorum policy; crashed worker cannot be counted."""
        job_id = f"job_fi02_{uuid.uuid4().hex[:8]}"
        run_id = f"run_fi02_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_fi02_{uuid.uuid4().hex[:8]}"

        np.random.seed(102)
        bank_partitions = {
            "BANK_A": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
            "BANK_B": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
            "BANK_C": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
        }
        holdout_val = (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32))

        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            app_env="production",
        )

        # When Bank B crashes in round 2, the coordinator detects that Bank B is terminated
        # and does not accept a missing contribution as completed.
        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                bank_partitions=bank_partitions,
                holdout_val_data=holdout_val,
                num_rounds=2,
                crash_bank_b=True,
                registry_storage_dir=str(tmp_path / "registry"),
            )
        assert (
            "Sample count mismatch" in str(exc_info.value)
            or "Required participant" in str(exc_info.value)
            or "aborted" in str(exc_info.value).lower()
        )

    def test_fi_03_coordinator_crash_transaction_recovery(self, real_pg_instance: str) -> None:
        """FI-03: Transaction rollback leaves no partial completion in PostgreSQL authority."""
        authority = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            app_env="production",
        )
        job_id = f"job_fi03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_fi03_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_fi03_{uuid.uuid4().hex[:8]}"

        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B"],
        )

        # Simulate a crash/rollback inside a completion transaction
        assert isinstance(authority._db._backend, PostgresTrainingJobAuthority)
        with pytest.raises(RuntimeError), authority._db._backend._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE cfi_scheduled_jobs SET status = 'COMPLETED' WHERE job_id = %s",
                        (job_id,),
                    )
                raise RuntimeError("Simulated coordinator crash before commit")

        # Verify PostgreSQL rolled back the transaction completely
        job = authority.get_training_job(job_id)
        assert job is not None
        assert job.status == "SCHEDULED", "Job status must remain SCHEDULED after rollback"

    def test_fi_04_artifact_substitution(self, real_pg_instance: str) -> None:
        """FI-04: Replacing final aggregated model bytes with a different artifact is rejected."""
        authority = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            app_env="production",
        )
        job_id = f"job_fi04_{uuid.uuid4().hex[:8]}"
        run_id = f"run_fi04_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_fi04_{uuid.uuid4().hex[:8]}"

        model_service = ModelService(get_settings())
        model_real = model_service.create_model(input_dim=NUM_FEATURES)
        real_bytes = _serialize_model_state_dict(model_real)
        real_sha = hashlib.sha256(real_bytes).hexdigest()

        # Complete job with legitimate artifact SHA-256
        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        authority.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        authority.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.45,
            worker_token=BANK_A_SECRET,
            model_update_digest="update_fi04",
        )
        rec = authority.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.45},
            artifact_sha256=real_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=False,
        )

        # Attacker substitutes bytes
        tampered_model = model_service.create_model(input_dim=NUM_FEATURES)
        tampered_bytes = _serialize_model_state_dict(tampered_model)
        assert tampered_bytes != real_bytes

        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=rec.completed_at,
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.45},
            artifact_sha256=real_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        priv = ed25519.Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex()

        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=authority,
            enforce_job_records=True,
        )

        with pytest.raises(ValueError) as exc_info:
            issuer.issue_receipt(evidence=evidence, model_bytes=tampered_bytes)
        assert "SHA-256 digest" in str(exc_info.value) and "does not match evidence artifact_sha256" in str(exc_info.value)

    def test_fi_05_forged_bank_event(self, real_pg_instance: str) -> None:
        """FI-05: Using Bank A's credential to submit an event claiming Bank B's identity is rejected."""
        authority = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            app_env="production",
        )
        job_id = f"job_fi05_{uuid.uuid4().hex[:8]}"
        run_id = f"run_fi05_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_fi05_{uuid.uuid4().hex[:8]}"

        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B"],
        )
        authority.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Bank A token attempting to submit observation claiming to be Bank B
        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            authority.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=50,
                observed_loss=0.40,
                bank_id="BANK_B",  # Forgery attempt
                worker_token=BANK_A_SECRET,
            )
        assert "Identity spoofing rejected" in str(exc_info.value) or "BANK_A" in str(exc_info.value)

    def test_fi_06_postgresql_unavailability(self) -> None:
        """FI-06: Database outage fails closed without fallback to SQLite or in-memory."""
        # Intentionally unreachable PostgreSQL port
        dead_pg_url = "postgresql://postgres:postgres@127.0.0.1:54399/nonexistent"

        with pytest.raises(Exception) as exc_info:
            authority = TrustedTrainingJobRecordSource(
                coordinator_token=COORD_SECRET,
                bank_tokens=BANK_TOKENS,
                db_url=dead_pg_url,
                app_env="production",
            )
            authority.schedule_training_job(
                job_id="job_fi06",
                training_run_id="run_fi06",
                simulation_id="sim_fi06",
                coordinator_token=COORD_SECRET,
            )

        # Fails closed with connection/database error, never silently creates SQLite database
        assert "connection" in str(exc_info.value).lower() or "postgresql" in str(exc_info.value).lower() or "database" in str(exc_info.value).lower()
        assert not os.path.exists("nonexistent")
        assert not os.path.exists("job_fi06.db")

    def test_fi_07_replay_update_rejected(self, real_pg_instance: str) -> None:
        """FI-07: Replaying a previously accepted bank update in a later global round is rejected."""
        authority = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            app_env="production",
        )
        job_id = f"job_fi07_{uuid.uuid4().hex[:8]}"
        run_id = f"run_fi07_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_fi07_{uuid.uuid4().hex[:8]}"

        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B"],
        )
        authority.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        replayed_digest = hashlib.sha256(b"replayed_digest_update_r1").hexdigest()
        sha256_b_r1 = hashlib.sha256(b"sha256_b_r1").hexdigest()
        sha256_b_r2 = hashlib.sha256(b"sha256_b_r2").hexdigest()
        global_model_r1 = hashlib.sha256(b"global_model_r1").hexdigest()
        global_model_r2 = hashlib.sha256(b"global_model_r2").hexdigest()

        # Record genuine round 1 observations
        authority.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.30,
            bank_id="BANK_A",
            worker_token=BANK_A_SECRET,
            model_update_digest=replayed_digest,
        )
        authority.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.30,
            bank_id="BANK_B",
            worker_token=BANK_B_SECRET,
            model_update_digest=sha256_b_r1,
        )

        # Record round 1 aggregation with replayed_digest
        authority.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A", "BANK_B"],
            accepted_local_update_digests=[replayed_digest, sha256_b_r1],
            aggregated_global_model_digest=global_model_r1,
            coordinator_token=COORD_SECRET,
        )

        # Record round 2 observations with Bank A replaying round 1 digest
        authority.record_round_observation(
            job_id=job_id,
            round_number=2,
            sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.30,
            bank_id="BANK_A",
            worker_token=BANK_A_SECRET,
            model_update_digest=replayed_digest,
        )
        authority.record_round_observation(
            job_id=job_id,
            round_number=2,
            sample_count=50,
            actual_executed_epochs=1,
            observed_loss=0.30,
            bank_id="BANK_B",
            worker_token=BANK_B_SECRET,
            model_update_digest=sha256_b_r2,
        )

        # Attacker attempts to replay the same update digest in round 2
        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            authority.record_global_aggregation(
                job_id=job_id,
                round_number=2,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A", "BANK_B"],
                accepted_local_update_digests=[replayed_digest, sha256_b_r2],
                aggregated_global_model_digest=global_model_r2,
                coordinator_token=COORD_SECRET,
            )
        assert "Replay of earlier round update digest" in str(exc_info.value) and "rejected" in str(exc_info.value)

    def test_fi_08_evaluation_manipulation(self, real_pg_instance: str) -> None:
        """FI-08: Altering AUC or validation loss after genuine evaluation is rejected."""
        authority = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            app_env="production",
        )
        job_id = f"job_fi08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_fi08_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_fi08_{uuid.uuid4().hex[:8]}"

        authority.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        authority.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        update_fi08 = hashlib.sha256(b"update_fi08").hexdigest()
        digest_fi08 = hashlib.sha256(b"digest_fi08").hexdigest()

        authority.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.35,
            worker_token=BANK_A_SECRET,
            model_update_digest=update_fi08,
        )

        authority.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[update_fi08],
            aggregated_global_model_digest=digest_fi08,
            coordinator_token=COORD_SECRET,
        )

        # Genuinely record evaluation observation: AUC = 0.81
        authority.record_evaluation_observation(
            job_id=job_id,
            metric_name="auc_roc",
            metric_value=0.81,
            evaluated_artifact_digest=digest_fi08,
            coordinator_token=COORD_SECRET,
        )

        # Attacker tries to complete job claiming AUC = 0.99
        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            authority.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.35, "auc_roc": 0.99},
                artifact_sha256=digest_fi08,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )
        assert "Evaluation metric 'auc_roc' value mismatch" in str(exc_info.value) and "0.99" in str(exc_info.value)
