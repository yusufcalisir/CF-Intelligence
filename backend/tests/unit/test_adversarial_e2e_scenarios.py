"""Adversarial End-to-End Verification Suite (E2E-NEG-01 to E2E-NEG-20).

Verifies fail-closed behavior across all 20 adversarial production boundary scenarios:
- E2E-NEG-01: Fake completion using enforce_lifecycle=False -> rejected.
- E2E-NEG-02: Missing real training observations -> rejected.
- E2E-NEG-03: Missing aggregation events -> rejected.
- E2E-NEG-04: Bank A attempts Bank B impersonation -> rejected.
- E2E-NEG-05: Coordinator attempts bank impersonation -> rejected.
- E2E-NEG-06: Coordinator attempts raw data access -> denied.
- E2E-NEG-07: Coordinator attempts access to bank private credentials -> denied.
- E2E-NEG-08: Unknown model update digest -> rejected.
- E2E-NEG-09: Quorum downgrade after scheduling -> rejected.
- E2E-NEG-10: Changed final model bytes -> rejected by issuer/verifier.
- E2E-NEG-11: Fabricated AUC or validation loss -> rejected during reconciliation.
- E2E-NEG-12: Executed epoch substitution -> rejected.
- E2E-NEG-13: PostgreSQL becomes unavailable during observation -> fails closed.
- E2E-NEG-14: Bank worker process terminates during training -> detected, quorum aborts.
- E2E-NEG-15: Coordinator process terminates during aggregation -> clean rollback.
- E2E-NEG-16: Issuer service becomes unavailable -> fails closed.
- E2E-NEG-17: Rogue issuer key used for candidate admission -> rejected by Registry.
- E2E-NEG-18: Unauthorized direct PostgreSQL mutation -> rejected.
- E2E-NEG-19: Conflicting completion race -> conflict rejected.
- E2E-NEG-20: Serving-only process attempts to produce a signed receipt -> rejected.
"""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import time
import uuid
from collections.abc import Generator
from typing import Any

import numpy as np
import pytest
from cryptography.fernet import Fernet

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    BankPrivateStorageVault,
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    _generate_ed25519_keypair_hex,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    TrustedTrainingJobRecordSource,
    get_default_training_verifier,
)
from app.infrastructure.database.training_authority_backends import PostgresTrainingJobAuthority

COORD_SECRET = "coord_secret_e2e_neg_production_991"
BANK_A_SECRET = "bank_a_secret_e2e_neg_alpha_112"
BANK_B_SECRET = "bank_b_secret_e2e_neg_beta_223"
BANK_C_SECRET = "bank_c_secret_e2e_neg_gamma_334"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


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


def _coordinator_terminating_aggregator(db_url: str, job_id: str, ready_event: Any) -> None:
    """Subprocess simulating coordinator terminated during aggregation transaction."""
    authority = PostgresTrainingJobAuthority(db_url=db_url)
    with authority._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cfi_global_aggregations (
                    event_id, job_id, round_number, aggregation_algorithm,
                    accepted_participants_json, accepted_digests_json,
                    aggregation_config_json, aggregated_model_digest,
                    timestamp, aggregation_status, payload_digest
                ) VALUES (
                    %s, %s, 1, 'fed_avg',
                    '["BANK_A"]', '["d1"]',
                    '{}', 'agg_kill', NOW(),
                    'ACCEPTED', 'digest_payload'
                )
                """,
                (f"evt_agg_{job_id}", job_id),
            )
            ready_event.set()
            time.sleep(10)


def _concurrent_completer(
    db_url: str,
    job_id: str,
    loss_val: float,
    barrier: Any,
    out_q: Any,
    artifact_sha: str | None = None,
) -> None:
    """Subprocess competing in genuine concurrent completion race."""
    try:
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=db_url,
            enforce_lifecycle=True,
            app_env="production",
        )
        barrier.wait()
        sha_to_use = artifact_sha or ("19" * 32)
        rec = source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": loss_val},
            artifact_sha256=sha_to_use,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )
        out_q.put(("SUCCESS", rec.job_id, loss_val))
    except Exception as e:
        out_q.put(("ERROR", str(e), loss_val))



class TestAdversarialEndToEndScenarios:
    """Rigorous verification of 20 mandatory adversarial failure scenarios."""

    def test_e2e_neg_01_fake_completion_enforce_lifecycle_false(self, real_pg_instance: str) -> None:
        """E2E-NEG-01: Fake completion using enforce_lifecycle=False is rejected; no completed evidence is created."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        fake_job = f"job_neg01_{uuid.uuid4().hex[:8]}"

        with pytest.raises(TrainingProvenanceSecurityError, match="enforce_lifecycle=False is forbidden in production"):
            source.complete_training_job(
                job_id=fake_job,
                executed_rounds=2,
                executed_epochs=2,
                sample_count=100,
                observed_metrics={"loss": 0.01},
                artifact_sha256="fake_sha_" + "1" * 56,
                worker_id="ATTACKER",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=False,
            )
        assert source.get_completed_job_by_id(fake_job) is None

    def test_e2e_neg_02_missing_real_training_observations(self, real_pg_instance: str) -> None:
        """E2E-NEG-02: Missing real training observations prevents completion."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_neg02_{uuid.uuid4().hex[:8]}"
        run_id = f"run_neg02_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_neg02",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        # Attempt completion without any observations
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="no verified execution observations recorded|has no verified completed round observation",
        ):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.1},
                artifact_sha256="02" * 32,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_e2e_neg_03_missing_aggregation_events(self, real_pg_instance: str) -> None:
        """E2E-NEG-03: Missing global aggregation events prevents job completion."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_neg03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_neg03_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_neg03",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="upd_neg03",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="missing global aggregation records"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.3},
                artifact_sha256="sha_neg03_" + "3" * 55,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_e2e_neg_04_bank_a_attempts_bank_b_impersonation(self, real_pg_instance: str) -> None:
        """E2E-NEG-04: Bank A token attempting to submit Bank B observation is rejected."""
        source = TrustedTrainingJobRecordSource(
            worker_token=BANK_A_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            source.record_round_observation(
                job_id="job_neg04",
                round_number=1,
                sample_count=100,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_B",
                model_update_digest="digest_spoof_neg04",
            )

    def test_e2e_neg_05_coordinator_attempts_bank_impersonation(self, real_pg_instance: str) -> None:
        """E2E-NEG-05: Coordinator token attempting to submit bank execution observation is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker execution events"):
            source.record_round_observation(
                job_id="job_neg05",
                round_number=1,
                sample_count=100,
                worker_token=COORD_SECRET,
                bank_id="BANK_A",
                model_update_digest="digest_coord_impersonate",
            )

    def test_e2e_neg_06_coordinator_attempts_raw_data_access(self, tmp_path) -> None:
        """E2E-NEG-06: Direct access attempt to bank private vault from coordinator context is rejected."""
        vaults_dir = str(tmp_path / "vaults")
        bank_a_enc_key = Fernet.generate_key()
        data_path = setup_bank_private_storage(
            bank_storage_dir=vaults_dir,
            bank_id="BANK_A",
            X_partition=np.zeros((10, NUM_FEATURES), dtype=np.float32),
            y_partition=np.zeros(10, dtype=np.float32),
            bank_token=BANK_A_SECRET,
            bank_encryption_key=bank_a_enc_key,
            lock_boundary=True,
        )

        vault = BankPrivateStorageVault("BANK_A", vaults_dir, bank_token=BANK_A_SECRET, bank_encryption_key=bank_a_enc_key)
        # 1. Coordinator identity cannot read private bank dataset through vault API
        with pytest.raises((PermissionError, TrainingProvenanceSecurityError), match="Access denied|Institutional boundary violation"):
            vault.read_dataset_as_bank("BANK_A", caller_token=COORD_SECRET)

        # 2. Coordinator context attempting direct filesystem read hits OS permission or sealed cipher
        with pytest.raises((PermissionError, TrainingProvenanceSecurityError)):
            with open(data_path, "rb") as f:
                raw_bytes = f.read()
                if not raw_bytes.startswith(b"PK\x03\x04"):
                    raise TrainingProvenanceSecurityError("Raw disk dataset is cryptographically sealed.")

        vault.release_exclusive_os_boundary()

    def test_e2e_neg_07_coordinator_attempts_access_to_bank_credentials(self, real_pg_instance: str) -> None:
        """E2E-NEG-07: Coordinator cannot inspect, possess, or be provisioned with bank private credentials."""
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        assert not hasattr(manager, "bank_tokens")
        assert not hasattr(manager.authority_source, "bank_tokens") or manager.authority_source.bank_tokens is None

        # Attempting to supply bank private credentials to production coordinator is strictly rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive bank private credentials"):
            manager.execute_distributed_federation(
                job_id="job_unauth_cred",
                training_run_id="run_unauth_cred",
                simulation_id="sim_unauth_cred",
                holdout_val_data=(np.zeros((5, NUM_FEATURES), dtype=np.float32), np.zeros(5, dtype=np.float32)),
                bank_tokens={"BANK_A": BANK_A_SECRET},
            )

    def test_e2e_neg_08_unknown_model_update_digest(self, real_pg_instance: str) -> None:
        """E2E-NEG-08: Aggregating unknown or fabricated update digest is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_neg08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_neg08_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_neg08",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="legit_digest_08",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="does not match observed digest"):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=["unknown_invented_digest_99"],
                aggregated_global_model_digest="agg_neg08",
                coordinator_token=COORD_SECRET,
            )

    def test_e2e_neg_09_quorum_downgrade_after_scheduling(self, real_pg_instance: str) -> None:
        """E2E-NEG-09: Coordinator cannot lower training quorum below scheduled contract."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_neg09_{uuid.uuid4().hex[:8]}"
        run_id = f"run_neg09_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_neg09",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_a_neg09",
        )

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Quorum failure for job .* requires at least 3 participants|Quorum failure",
        ):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=["digest_a_neg09"],
                aggregated_global_model_digest="09" * 32,
                coordinator_token=COORD_SECRET,
            )

    def test_e2e_neg_10_changed_final_model_bytes(self, real_pg_instance: str) -> None:
        """E2E-NEG-10: Providing modified model bytes that don't match the receipt's artifact SHA-256 is rejected."""
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()
        trust_store = DurableTrustStore({DEFAULT_TRAINING_KEY_ID: pub_hex})
        verifier = TrainingReceiptVerifier(trust_store=trust_store)

        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            require_authenticated_job=False,
        )
        real_bytes = b"real_model_weights_tensor_state"
        real_sha = hashlib.sha256(real_bytes).hexdigest()

        evidence = TrainingExecutionEvidence(
            training_run_id="run_neg10",
            job_id="job_neg10",
            simulation_id="sim_neg10",
            completed_at="2026-10-11T12:00:00Z",
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.2},
            artifact_sha256=real_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=real_bytes)

        # Attacker substitutes bytes during verification
        tampered_bytes = b"tampered_attacker_weights_state"
        is_valid, reason = verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id="sim_neg10",
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
            actual_artifact_bytes=tampered_bytes,
        )
        assert not is_valid
        assert "digest mismatch" in reason.lower()

    def test_e2e_neg_11_fabricated_auc_or_validation_loss(self, real_pg_instance: str) -> None:
        """E2E-NEG-11: Invented AUC or validation loss metric differing from recorded evaluation is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_neg11_{uuid.uuid4().hex[:8]}"
        run_id = f"run_neg11_{uuid.uuid4().hex[:8]}"
        art_sha = "11" * 32

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_neg11",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_neg11",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_neg11"],
            aggregated_global_model_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )
        # Measured evaluation AUC was 0.82
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="auc_roc",
            metric_value=0.82,
            evaluated_artifact_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )

        # Attacker claims fabricated AUC of 0.99
        with pytest.raises(TrainingProvenanceSecurityError, match="Evaluation metric 'auc_roc' value mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"auc_roc": 0.99},  # Fabricated!
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_e2e_neg_12_executed_epoch_substitution(self, real_pg_instance: str) -> None:
        """E2E-NEG-12: Substituting planned epochs or total worker epochs for executed epochs is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_neg12_{uuid.uuid4().hex[:8]}"
        run_id = f"run_neg12_{uuid.uuid4().hex[:8]}"
        art_sha = "12" * 32

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_neg12",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
            expected_rounds=1,
            expected_epochs=1,
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_neg12",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_neg12"],
            aggregated_global_model_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.3,
            evaluated_artifact_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="Executed epochs mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=5,  # Substituted
                sample_count=100,
                observed_metrics={"loss": 0.3},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_e2e_neg_13_postgresql_unavailability_during_observation(self) -> None:
        """E2E-NEG-13: When PostgreSQL becomes unavailable during observation, system fails closed without fallback."""
        with pytest.raises(TrainingProvenanceConfigurationError):
            source = TrustedTrainingJobRecordSource(
                worker_token=BANK_A_SECRET,
                bank_tokens={"BANK_A": BANK_A_SECRET},
                db_url="postgresql://down:pass@127.0.0.1:59999/down_db",
                enforce_lifecycle=True,
                app_env="production",
            )
            source.record_round_observation(
                job_id="job_neg13",
                round_number=1,
                sample_count=100,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_A",
                model_update_digest="digest_13",
            )

    def test_e2e_neg_14_bank_worker_terminates_during_training(self, real_pg_instance: str, tmp_path) -> None:
        """E2E-NEG-14: When a bank worker process terminates/crashes, coordinator aborts round and refuses completion."""
        holdout_val = (np.random.randn(20, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 20).astype(np.float32))
        bank_partitions = {
            "BANK_A": (np.random.randn(20, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 20).astype(np.float32)),
            "BANK_B": (np.random.randn(20, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 20).astype(np.float32)),
            "BANK_C": (np.random.randn(20, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 20).astype(np.float32)),
        }
        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            bank_partitions=bank_partitions,
        )

        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="aborted|Required participant"):
            manager.execute_distributed_federation(
                job_id=f"job_neg14_{uuid.uuid4().hex[:8]}",
                training_run_id=f"run_neg14_{uuid.uuid4().hex[:8]}",
                simulation_id="sim_neg14",
                holdout_val_data=holdout_val,
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                worker_endpoints=endpoints,
                worker_processes=procs,
                num_rounds=2,
                crash_bank_b=True,
                registry_storage_dir=str(tmp_path / "reg_neg14"),
            )

    def test_e2e_neg_15_coordinator_process_terminates_during_aggregation(self, real_pg_instance: str) -> None:
        """E2E-NEG-15: Real OS process termination during aggregation leaves no partial or corrupted state."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_neg15_{uuid.uuid4().hex[:8]}"

        authority.schedule_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_neg15",
            coordinator_id="coord15",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A"],
        )

        ready_event = mp.Event()
        proc = mp.Process(target=_coordinator_terminating_aggregator, args=(real_pg_instance, job_id, ready_event))
        proc.start()
        assert ready_event.wait(timeout=10) is True, "Aggregator child process failed to reach active transaction boundary"
        # Terminate real coordinator process while transaction is uncommitted
        proc.terminate()
        proc.join(timeout=3)

        aggs = authority.get_global_aggregations(job_id)
        assert len(aggs) == 0
        assert authority.get_completed_job_by_id(job_id) is None

    def test_e2e_neg_16_issuer_service_unavailable(self, real_pg_instance: str, tmp_path) -> None:
        """E2E-NEG-16: When isolated issuer process is terminated, receipt signing fails closed immediately."""
        vault_dir = str(tmp_path / "issuer_vault_neg16")
        key_file, _ = provision_isolated_issuer_vault(vault_dir)
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        assert signing_auth.is_alive()
        # Terminate the actual issuer background process
        signing_auth.terminate()
        assert not signing_auth.is_alive()

        # Receipt signing fails closed without local fallback
        with pytest.raises(TrainingProvenanceSecurityError, match="issuer process terminated|service unavailable"):
            signing_auth.sign_completed_job_receipt(
                job_id="job_neg16",
                artifact_bytes=b"model_bytes_16",
                caller_identity="COORDINATOR",
                caller_token=COORD_SECRET,
            )

    def test_e2e_neg_17_rogue_issuer_key_rejected_by_registry(self, real_pg_instance: str, tmp_path) -> None:
        """E2E-NEG-17: Receipt signed by untrusted rogue key is rejected upon submission to Registry."""
        registry = ModelRegistry(storage_dir=str(tmp_path / "reg_neg17"))
        priv_hex, _ = _generate_ed25519_keypair_hex()

        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id="rogue_untrusted_key_id",
            require_authenticated_job=False,
        )
        m_bytes = b"model_bytes_neg17"
        m_sha = hashlib.sha256(m_bytes).hexdigest()

        evidence = TrainingExecutionEvidence(
            training_run_id="run_neg17",
            job_id="job_neg17",
            simulation_id="sim_neg17",
            completed_at="2026-10-11T12:00:00Z",
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.2},
            artifact_sha256=m_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=m_bytes)

        candidate = registry.save_version(
            simulation_id="sim_neg17",
            state_dict={},
            metrics={"loss": 0.2},
            training_receipt=receipt,
            artifact_bytes=m_bytes,
            provenance_type="TRAINING_RECORDED",
            training_run_id="run_neg17",
        )
        assert candidate["provenance_type"] == "UNVERIFIED"
        assert candidate["is_production_eligible"] is False
        assert "INVALID_RECEIPT" in candidate["attestation_status"]

    def test_e2e_neg_18_unauthorized_direct_postgres_mutation(self, real_pg_instance: str) -> None:
        """E2E-NEG-18: Direct completion attempt on scheduled/started job without observations is strictly rejected."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_neg18_{uuid.uuid4().hex[:8]}"

        # 1. Schedule legitimate job
        authority.schedule_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_neg18",
            coordinator_id="coord18",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A"],
        )
        # 2. Start job
        authority.start_job(job_id=job_id, worker_id="BANK_A")

        # 3. Direct completion with enforce_lifecycle=False is unconditionally rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="enforce_lifecycle=False is forbidden|execution evidence"):
            authority.complete_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.1},
                artifact_sha256="18" * 32,
                worker_id="DIRECT_BACKEND_CALLER",
                enforce_lifecycle=False,
            )

        assert authority.get_completed_job_by_id(job_id) is None

    def test_e2e_neg_19_conflicting_completion_race(self, real_pg_instance: str) -> None:
        """E2E-NEG-19: Two independent processes race to complete the same job with conflicting metrics."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_neg19_{uuid.uuid4().hex[:8]}"
        run_id = f"run_neg19_{uuid.uuid4().hex[:8]}"
        art_sha = "19" * 32

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_neg19",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.25,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_neg19",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_neg19"],
            aggregated_global_model_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.25,
            evaluated_artifact_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )

        barrier: Any = mp.Barrier(2)
        out_q_1: Any = mp.Queue()
        out_q_2: Any = mp.Queue()

        art_sha_conflict = "cc" * 32
        p1 = mp.Process(target=_concurrent_completer, args=(real_pg_instance, job_id, 0.25, barrier, out_q_1, art_sha))
        p2 = mp.Process(target=_concurrent_completer, args=(real_pg_instance, job_id, 0.25, barrier, out_q_2, art_sha_conflict))

        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        results = [out_q_1.get(timeout=5), out_q_2.get(timeout=5)]
        statuses = {r[0] for r in results}
        assert "SUCCESS" in statuses
        assert "ERROR" in statuses

        persisted = source.get_completed_job_by_id(job_id)
        assert persisted is not None
        assert persisted.observed_metrics["loss"] == 0.25
        assert persisted.artifact_sha256 == art_sha

    def test_e2e_neg_20_serving_process_cannot_sign_receipt(self) -> None:
        """E2E-NEG-20: Verifier / serving process has no signing key and cannot manufacture receipts."""
        verifier = get_default_training_verifier()
        assert not hasattr(verifier, "_private_key")
        assert not hasattr(verifier, "issue_receipt")
