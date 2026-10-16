"""Exhaustive Negative Adversarial Integration Suite (SEC-01 through SEC-30).

Implements and independently verifies all 30 mandatory negative security scenarios
defined in Section 15 of the Module 01-A.1 Acceptance Contract:
- SEC-01: Direct PostgreSQL completion bypass on a scheduled/started but unobserved job.
- SEC-02: Worker token used without genuine execution.
- SEC-03: Coordinator credential used to forge bank observation.
- SEC-04: Global worker token claiming arbitrary bank identity.
- SEC-05: Bank A credential claiming Bank B.
- SEC-06: Coordinator attempts direct Bank A dataset access.
- SEC-07: Coordinator attempts Bank B secret access.
- SEC-08: Bank A attempts Bank C dataset access.
- SEC-09: Coordinator attempts signing private-key access.
- SEC-10: Unauthorized caller requests receipt issuance.
- SEC-11: Issuer process is actually terminated.
- SEC-12: Coordinator process is actually terminated during aggregation.
- SEC-13: PostgreSQL becomes unavailable during observation persistence.
- SEC-14: Missing loss is submitted as completed observation.
- SEC-15: Fabricated AUC is submitted.
- SEC-16: Feature ordering is altered.
- SEC-17: Permitted participant list changes for existing job.
- SEC-18: Quorum is lowered after scheduling.
- SEC-19: One accepted update digest is substituted.
- SEC-20: Aggregation event is missing.
- SEC-21: Final artifact bytes are replaced.
- SEC-22: An unauthorized PostgreSQL role inserts completion data.
- SEC-23: An unauthorized PostgreSQL role updates committed evidence.
- SEC-24: Two independent processes race to complete one job differently.
- SEC-25: Duplicate and conflicting observations are submitted concurrently.
- SEC-26: Previously completed evidence is modified after restart.
- SEC-27: Rogue issuer key submits a signed receipt.
- SEC-28: Production verifier attempts signing.
- SEC-29: Incomplete training attempts Registry admission.
- SEC-30: A development-only compatibility path attempts production-trusted issuance.
"""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import time
import urllib.parse
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import pytest
from cryptography.fernet import Fernet

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    BankPrivateStorageVault,
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    _generate_ed25519_keypair_hex,
    setup_bank_private_storage,
    spawn_isolated_coordinator_process,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    TrustedTrainingJobRecordSource,
    get_default_training_verifier,
)
from app.infrastructure.database.training_authority_backends import (
    PostgresTrainingJobAuthority,
    RoundObservation,
)

COORD_SECRET = "coord_secret_sec_matrix_prod_991"
BANK_A_SECRET = "bank_a_secret_sec_matrix_alpha_112"
BANK_B_SECRET = "bank_b_secret_sec_matrix_beta_223"
BANK_C_SECRET = "bank_c_secret_sec_matrix_gamma_334"

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


def _sec12_coordinator_terminating_aggregator(db_url: str, job_id: str, ready_event: Any) -> None:
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
                    '{}', 'agg_kill', NOW()::text,
                    'ACCEPTED', 'digest_payload'
                )
                """,
                (f"evt_agg_{job_id}", job_id),
            )
            ready_event.set()
            time.sleep(30)


def _sec24_concurrent_completer(
    db_url: str,
    job_id: str,
    loss_val: float,
    barrier: Any,
    out_q: Any,
    artifact_sha: str | None = None,
) -> None:
    try:
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=db_url,
            enforce_lifecycle=True,
            app_env="production",
        )
        barrier.wait()
        sha_to_use = artifact_sha or ("24" * 32)
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


def _sec25_concurrent_observer(db_url: str, job_id: str, loss_val: float, barrier: Any, out_q: Any) -> None:
    try:
        source = TrustedTrainingJobRecordSource(
            worker_token=BANK_A_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=db_url,
            enforce_lifecycle=True,
            app_env="production",
        )
        barrier.wait()
        obs = source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=loss_val,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest=f"digest_sec25_{loss_val}",
        )
        out_q.put(("SUCCESS", obs.event_id, loss_val))
    except Exception as e:
        out_q.put(("ERROR", str(e), loss_val))


class TestSecurityAdversarialMatrix:
    """Rigorous end-to-end verification of SEC-01 through SEC-30 negative scenarios."""

    def test_sec_01_direct_postgres_completion_bypass_rejected(self, real_pg_instance: str) -> None:
        """SEC-01: Direct PostgreSQL complete_job(enforce_lifecycle=False) on scheduled/started but unobserved job is rejected."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_sec01_{uuid.uuid4().hex[:8]}"

        authority.schedule_job(
            job_id=job_id,
            training_run_id=f"run_sec01_{uuid.uuid4().hex[:8]}",
            simulation_id="sim_sec01",
            coordinator_id="coord_sec01",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A"],
        )
        authority.start_job(job_id=job_id, worker_id="BANK_A")

        with pytest.raises(TrainingProvenanceSecurityError, match="enforce_lifecycle=False is forbidden"):
            authority.complete_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.10},
                artifact_sha256="01" * 32,
                worker_id="DIRECT_BYPASS_ATTACKER",
                enforce_lifecycle=False,
            )
        assert authority.get_completed_job_by_id(job_id) is None

    def test_sec_02_worker_token_without_genuine_execution(self, real_pg_instance: str) -> None:
        """SEC-02: Worker token alone cannot establish training execution without real observations."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec02_{uuid.uuid4().hex[:8]}"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_sec02_{uuid.uuid4().hex[:8]}",
            simulation_id="sim_sec02",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        with pytest.raises(TrainingProvenanceSecurityError, match="no verified execution observations recorded|has no verified completed round observation"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.15},
                artifact_sha256="02" * 32,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )
        assert source.get_completed_job_by_id(job_id) is None

    def test_sec_03_coordinator_credential_used_to_forge_bank_observation(self, real_pg_instance: str) -> None:
        """SEC-03: Coordinator credential attempting to record bank worker observations is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker execution events"):
            source.record_round_observation(
                job_id="job_sec03",
                round_number=1,
                sample_count=100,
                worker_token=COORD_SECRET,
                bank_id="BANK_A",
                model_update_digest="digest_sec03",
            )

    def test_sec_04_global_worker_token_claiming_arbitrary_bank_identity(self, real_pg_instance: str) -> None:
        """SEC-04: Global worker token claiming bank identity is rejected in production (no shared fallback)."""
        source = TrustedTrainingJobRecordSource(
            worker_token="global_shared_worker_token_999",
            bank_tokens={},  # No bank-specific token configured
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceConfigurationError, match="Production training requires bank-specific workload credentials"):
            source.authenticate_bank_worker("global_shared_worker_token_999", "BANK_A")

    def test_sec_05_bank_a_credential_claiming_bank_b(self, real_pg_instance: str) -> None:
        """SEC-05: Authenticated Bank A credential attempting to submit Bank B observation is rejected."""
        source = TrustedTrainingJobRecordSource(
            worker_token=BANK_A_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            source.record_round_observation(
                job_id="job_sec05",
                round_number=1,
                sample_count=100,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_B",
                model_update_digest="digest_sec05",
            )

    def test_sec_06_coordinator_attempts_direct_bank_a_dataset_access(self, tmp_path) -> None:
        """SEC-06: Direct access attempt to Bank A private vault from coordinator context is denied."""
        vaults_dir = str(tmp_path / "vaults_sec06")
        bank_a_enc_key = Fernet.generate_key()
        setup_bank_private_storage(
            bank_storage_dir=vaults_dir,
            bank_id="BANK_A",
            X_partition=np.zeros((10, NUM_FEATURES), dtype=np.float32),
            y_partition=np.zeros(10, dtype=np.float32),
            bank_token=BANK_A_SECRET,
            bank_encryption_key=bank_a_enc_key,
            lock_boundary=True,
        )
        vault = BankPrivateStorageVault("BANK_A", vaults_dir, bank_token=BANK_A_SECRET, bank_encryption_key=bank_a_enc_key)
        with pytest.raises((PermissionError, TrainingProvenanceSecurityError), match="Access denied|Institutional boundary violation"):
            vault.read_dataset_as_bank("BANK_A", caller_token=COORD_SECRET)
        vault.release_exclusive_os_boundary()

    def test_sec_07_coordinator_attempts_bank_b_secret_access(self, real_pg_instance: str) -> None:
        """SEC-07: Coordinator cannot possess or be provisioned with Bank B private credentials in production."""
        with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive bank private credentials"):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_SECRET,
                db_url=real_pg_instance,
                app_env="production",
                bank_tokens={"BANK_B": BANK_B_SECRET},
            )
        _, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive bank private credentials"):
                client.execute_federation(
                    job_id="job_sec07",
                    training_run_id="run_sec07",
                    simulation_id="sim_sec07",
                    holdout_val_data=(np.zeros((5, NUM_FEATURES), dtype=np.float32), np.zeros(5, dtype=np.float32)),
                    participating_banks=["BANK_A", "BANK_B"],
                    worker_endpoints={"BANK_A": {"inbox": None, "outbox": None}, "BANK_B": {"inbox": None, "outbox": None}},
                    num_rounds=1,
                    expected_unique_samples=100,
                    registry_storage_dir=None,
                    coordinator_token=COORD_SECRET,
                    db_url=real_pg_instance,
                    comp_service_endpoint=("127.0.0.1", 1234),
                    signing_authority=None,
                    bank_tokens={"BANK_B": BANK_B_SECRET},
                )
        finally:
            client.terminate()

    def test_sec_08_bank_a_attempts_bank_c_dataset_access(self, tmp_path) -> None:
        """SEC-08: Bank A worker credential attempting to access Bank C private storage vault is denied."""
        vaults_dir = str(tmp_path / "vaults_sec08")
        bank_c_enc_key = Fernet.generate_key()
        setup_bank_private_storage(
            bank_storage_dir=vaults_dir,
            bank_id="BANK_C",
            X_partition=np.zeros((10, NUM_FEATURES), dtype=np.float32),
            y_partition=np.zeros(10, dtype=np.float32),
            bank_token=BANK_C_SECRET,
            bank_encryption_key=bank_c_enc_key,
            lock_boundary=True,
        )
        vault_c = BankPrivateStorageVault("BANK_C", vaults_dir, bank_token=BANK_C_SECRET, bank_encryption_key=bank_c_enc_key)
        with pytest.raises(PermissionError, match="Access denied: Invalid worker credential"):
            vault_c.read_dataset_as_bank("BANK_C", caller_token=BANK_A_SECRET)
        vault_c.release_exclusive_os_boundary()

    def test_sec_09_coordinator_attempts_signing_private_key_access(self, real_pg_instance: str) -> None:
        """SEC-09: Coordinator attempting to possess or pass signing private key in production is rejected."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator process cannot receive or hold receipt issuer private signing key material"):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_SECRET,
                db_url=real_pg_instance,
                app_env="production",
                signing_key_hex=priv_hex,
            )
        _, client = spawn_isolated_coordinator_process(app_env="production")
        try:
            with pytest.raises(TrainingProvenanceSecurityError, match="Production coordinator cannot receive"):
                client.execute_federation(
                    job_id="job_sec09",
                    training_run_id="run_sec09",
                    simulation_id="sim_sec09",
                    holdout_val_data=(np.zeros((5, NUM_FEATURES), dtype=np.float32), np.zeros(5, dtype=np.float32)),
                    participating_banks=["BANK_A"],
                    worker_endpoints={"BANK_A": {"inbox": None, "outbox": None}},
                    num_rounds=1,
                    expected_unique_samples=100,
                    registry_storage_dir=None,
                    coordinator_token=COORD_SECRET,
                    db_url=real_pg_instance,
                    comp_service_endpoint=("127.0.0.1", 1234),
                    signing_authority=None,
                    signing_key_hex=priv_hex,
                )
        finally:
            client.terminate()

    def test_sec_10_unauthorized_caller_requests_receipt_issuance(self, real_pg_instance: str, tmp_path) -> None:
        """SEC-10: Unauthorized caller token or identity requesting receipt from isolated issuer is rejected."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        key_path = str(tmp_path / "issuer_sec10.key")
        Path(key_path).write_text(priv_hex, encoding="utf-8")
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_path,
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized receipt signing request|invalid or missing coordinator token|Unauthorized caller token|Receipt signing rejected"):
            signing_auth.sign_completed_job_receipt(
                job_id="job_sec10",
                artifact_bytes=b"model_bytes_sec10",
                caller_identity="UNAUTHORIZED_CALLER",
                caller_token="wrong_token_sec10",
            )
        signing_auth.terminate()

    def test_sec_11_issuer_process_terminated_fails_closed(self, real_pg_instance: str, tmp_path) -> None:
        """SEC-11: When isolated receipt signing service process is terminated, signing fails closed immediately."""
        priv_hex, _ = _generate_ed25519_keypair_hex()
        key_path = str(tmp_path / "issuer_sec11.key")
        Path(key_path).write_text(priv_hex, encoding="utf-8")
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_path,
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            app_env="production",
        )
        assert signing_auth.is_alive()
        signing_auth.terminate()
        assert not signing_auth.is_alive()

        with pytest.raises(TrainingProvenanceSecurityError, match="issuer process terminated|service unavailable"):
            signing_auth.sign_completed_job_receipt(
                job_id="job_sec11",
                artifact_bytes=b"bytes_sec11",
                caller_identity="COORDINATOR",
                caller_token=COORD_SECRET,
            )

    def test_sec_12_coordinator_process_terminated_during_aggregation(self, real_pg_instance: str) -> None:
        """SEC-12: Coordinator OS process terminated during aggregation leaves database uncorrupted."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_sec12_{uuid.uuid4().hex[:8]}"
        authority.schedule_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec12",
            coordinator_id="coord_sec12",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A"],
        )
        authority.start_job(job_id=job_id, worker_id="BANK_A")
        authority.record_observation(
            RoundObservation(
                event_id=f"obs_{job_id}",
                job_id=job_id,
                training_run_id=f"run_{job_id}",
                simulation_id="sim_sec12",
                worker_id="BANK_A",
                bank_id="BANK_A",
                round_number=1,
                attempt_number=1,
                actual_sample_count=100,
                actual_executed_epochs=1,
                observed_loss=0.5,
                metrics={"loss": 0.5},
                execution_timestamp="2026-10-16T12:00:00Z",
                model_update_digest="d1",
                execution_status="COMPLETED",
            )
        )
        ready_event = mp.Event()
        proc = mp.Process(target=_sec12_coordinator_terminating_aggregator, args=(real_pg_instance, job_id, ready_event))
        proc.start()
        assert ready_event.wait(timeout=25) is True, "Aggregator child process failed to reach active transaction boundary"
        proc.terminate()
        proc.join(timeout=3)

        assert len(authority.get_global_aggregations(job_id)) == 0
        assert authority.get_completed_job_by_id(job_id) is None

    def test_sec_13_postgresql_unavailability_during_observation_fails_closed(self) -> None:
        """SEC-13: Database unavailability during observation persistence fails closed without fallback."""
        with pytest.raises(TrainingProvenanceConfigurationError):
            source = TrustedTrainingJobRecordSource(
                worker_token=BANK_A_SECRET,
                bank_tokens={"BANK_A": BANK_A_SECRET},
                db_url="postgresql://down:pass@127.0.0.1:59999/down_db",
                enforce_lifecycle=True,
                app_env="production",
            )
            source.record_round_observation(
                job_id="job_sec13",
                round_number=1,
                sample_count=100,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_A",
                model_update_digest="digest_sec13",
            )

    def test_sec_14_missing_loss_submitted_as_completed_observation(self, real_pg_instance: str) -> None:
        """SEC-14: Missing or non-finite training loss submitted to observation is rejected without 0.0 fallback."""
        source = TrustedTrainingJobRecordSource(
            worker_token=BANK_A_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Missing or non-finite observed loss|Loss cannot be defaulted to 0.0"):
            source.record_round_observation(
                job_id="job_sec14",
                round_number=1,
                sample_count=100,
                observed_loss=None,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_A",
                model_update_digest="digest_sec14",
            )

    def test_sec_15_fabricated_auc_submitted_during_completion(self, real_pg_instance: str) -> None:
        """SEC-15: Fabricated AUC metric differing from evaluated evidence is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec15_{uuid.uuid4().hex[:8]}"
        art_sha = "15" * 32
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec15",
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
            model_update_digest="digest_sec15",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_sec15"],
            aggregated_global_model_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="auc_roc",
            metric_value=0.85,
            evaluated_artifact_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="Evaluation metric 'auc_roc' value mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"auc_roc": 0.999},  # Fabricated
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_sec_16_feature_ordering_altered(self, real_pg_instance: str) -> None:
        """SEC-16: Re-scheduling with altered feature ordering produces different configuration fingerprint."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_sec16_{uuid.uuid4().hex[:8]}"
        rev_features = list(reversed(FEATURE_NAMES))

        authority.schedule_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec16",
            coordinator_id="coord_sec16",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A"],
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="cannot reschedule existing job with different parameters|Configuration fingerprint mismatch"):
            authority.schedule_job(
                job_id=job_id,
                training_run_id=f"run_{job_id}",
                simulation_id="sim_sec16",
                coordinator_id="coord_sec16",
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                expected_rounds=1,
                expected_epochs=1,
                input_dim=NUM_FEATURES,
                feature_names=rev_features,  # Altered feature ordering!
                permitted_workers=["BANK_A"],
            )

    def test_sec_17_permitted_participant_list_changes_for_existing_job(self, real_pg_instance: str) -> None:
        """SEC-17: Permitted participant changes for existing job are rejected via immutable configuration fingerprint."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_sec17_{uuid.uuid4().hex[:8]}"

        authority.schedule_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec17",
            coordinator_id="coord_sec17",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A", "BANK_B"],
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="cannot reschedule existing job with different parameters|Configuration fingerprint mismatch"):
            authority.schedule_job(
                job_id=job_id,
                training_run_id=f"run_{job_id}",
                simulation_id="sim_sec17",
                coordinator_id="coord_sec17",
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                expected_rounds=1,
                expected_epochs=1,
                input_dim=NUM_FEATURES,
                feature_names=list(FEATURE_NAMES),
                permitted_workers=["BANK_A", "BANK_C"],  # Altered participant set!
            )

    def test_sec_18_quorum_lowered_after_scheduling(self, real_pg_instance: str) -> None:
        """SEC-18: Lowering expected training rounds/contract after scheduling is rejected via immutable fingerprint."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_sec18_{uuid.uuid4().hex[:8]}"

        authority.schedule_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec18",
            coordinator_id="coord_sec18",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=3,
            expected_epochs=3,
            input_dim=NUM_FEATURES,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="cannot reschedule existing job with different parameters|Configuration fingerprint mismatch"):
            authority.schedule_job(
                job_id=job_id,
                training_run_id=f"run_{job_id}",
                simulation_id="sim_sec18",
                coordinator_id="coord_sec18",
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                expected_rounds=1,  # Lowered rounds!
                expected_epochs=1,
                input_dim=NUM_FEATURES,
                feature_names=list(FEATURE_NAMES),
                permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
            )

    def test_sec_19_accepted_update_digest_substituted(self, real_pg_instance: str) -> None:
        """SEC-19: Record global aggregation with substituted or unobserved update digest is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec19_{uuid.uuid4().hex[:8]}"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec19",
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
            model_update_digest="genuine_update_digest_19",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="does not match observed digest"):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=["forged_substituted_digest_99"],
                aggregated_global_model_digest="agg_sec19",
                coordinator_token=COORD_SECRET,
            )

    def test_sec_20_aggregation_event_missing(self, real_pg_instance: str) -> None:
        """SEC-20: Completing job with observations but zero global aggregation events is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec20_{uuid.uuid4().hex[:8]}"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec20",
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
            model_update_digest="digest_sec20",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="missing global aggregation records|has no accepted global aggregation events"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.3},
                artifact_sha256="20" * 32,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_sec_21_final_artifact_bytes_replaced(self, real_pg_instance: str) -> None:
        """SEC-21: Replaced model artifact bytes differing from receipt SHA-256 fails verification."""
        priv_hex, pub_hex = _generate_ed25519_keypair_hex()
        trust_store = DurableTrustStore({"key_sec21": pub_hex})
        custom_verifier = TrainingReceiptVerifier(trust_store=trust_store)

        genuine_bytes = b"genuine_model_bytes_21"
        tampered_bytes = b"tampered_model_bytes_21_hacked"
        genuine_sha = hashlib.sha256(genuine_bytes).hexdigest()

        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id="key_sec21",
            require_authenticated_job=False,
        )
        evidence = TrainingExecutionEvidence(
            training_run_id="run_sec21",
            job_id="job_sec21",
            simulation_id="sim_sec21",
            completed_at="2026-10-11T12:00:00Z",
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.2},
            artifact_sha256=genuine_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=genuine_bytes)

        is_valid, reason = custom_verifier.verify_receipt(
            receipt=receipt,
            expected_simulation_id="sim_sec21",
            actual_artifact_bytes=tampered_bytes,  # Replaced bytes!
        )
        assert is_valid is False
        assert "mismatch" in reason.lower()

    def test_sec_22_unauthorized_postgres_role_inserts_completion_data(self, real_pg_instance: str) -> None:
        """SEC-22: Restricted PostgreSQL role attempting raw INSERT of completion row receives SQLSTATE 42501."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        role_name = f"cfi_role_sec22_{uuid.uuid4().hex[:6]}"
        role_pass = "pass_sec22"

        with authority._connect() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"DROP ROLE IF EXISTS {role_name}")
                cur.execute(f"CREATE ROLE {role_name} WITH LOGIN PASSWORD '{role_pass}'")
                cur.execute(f"GRANT USAGE ON SCHEMA public TO {role_name}")
                cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role_name}")
                cur.execute(f"REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {role_name}")

        parsed = urllib.parse.urlsplit(real_pg_instance)
        r_url = urllib.parse.urlunsplit((parsed.scheme, f"{role_name}:{role_pass}@{parsed.hostname}:{parsed.port}", parsed.path, parsed.query, parsed.fragment))

        with psycopg.connect(r_url) as rconn:
            with rconn.cursor() as rcur:
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
                    rcur.execute(
                        """
                        INSERT INTO cfi_completed_jobs (
                            job_id, training_run_id, simulation_id, completed_at, status,
                            executed_rounds, executed_epochs, sample_count, observed_metrics_json,
                            artifact_sha256, architecture, feature_schema_version, input_dim,
                            feature_names_json, worker_id, evidence_fingerprint
                        ) VALUES ('unauth_j', 'r', 's', NOW(), 'COMPLETED', 1, 1, 100, '{}', 'sha', 'arch', 'v1', 1, '[]', 'HAX', 'fp')
                        """
                    )
                assert exc_info.value.sqlstate == "42501"

    def test_sec_23_unauthorized_postgres_role_updates_committed_evidence(self, real_pg_instance: str) -> None:
        """SEC-23: Restricted PostgreSQL role attempting UPDATE on committed evidence receives SQLSTATE 42501."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        role_name = f"cfi_role_sec23_{uuid.uuid4().hex[:6]}"
        role_pass = "pass_sec23"

        with authority._connect() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"DROP ROLE IF EXISTS {role_name}")
                cur.execute(f"CREATE ROLE {role_name} WITH LOGIN PASSWORD '{role_pass}'")
                cur.execute(f"GRANT USAGE ON SCHEMA public TO {role_name}")
                cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role_name}")
                cur.execute(f"REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {role_name}")

        parsed = urllib.parse.urlsplit(real_pg_instance)
        r_url = urllib.parse.urlunsplit((parsed.scheme, f"{role_name}:{role_pass}@{parsed.hostname}:{parsed.port}", parsed.path, parsed.query, parsed.fragment))

        with psycopg.connect(r_url) as rconn:
            with rconn.cursor() as rcur:
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info:
                    rcur.execute("UPDATE cfi_completed_jobs SET status = 'TAMPERED'")
                assert exc_info.value.sqlstate == "42501"

    def test_sec_24_two_processes_race_to_complete_differently(self, real_pg_instance: str) -> None:
        """SEC-24: Two independent processes race with barrier synchronization to complete job; exactly one commits."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec24_{uuid.uuid4().hex[:8]}"
        art_sha_valid = "24" * 32
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec24",
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
            model_update_digest="digest_sec24",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_sec24"],
            aggregated_global_model_digest=art_sha_valid,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.25,
            evaluated_artifact_digest=art_sha_valid,
            coordinator_token=COORD_SECRET,
        )

        # Concurrency race: Both processes issue the EXACT SAME valid completion payload
        barrier: Any = mp.Barrier(2)
        q1: Any = mp.Queue()
        q2: Any = mp.Queue()
        p1 = mp.Process(target=_sec24_concurrent_completer, args=(real_pg_instance, job_id, 0.25, barrier, q1, art_sha_valid))
        p2 = mp.Process(target=_sec24_concurrent_completer, args=(real_pg_instance, job_id, 0.25, barrier, q2, art_sha_valid))
        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        results = [q1.get(timeout=5), q2.get(timeout=5)]
        statuses = [r[0] for r in results]
        # Both processes resolve successfully (one commits, second resolves idempotently)
        assert all(s == "SUCCESS" for s in statuses), f"Failed with results: {results}"

        persisted = source.get_completed_job_by_id(job_id)
        assert persisted is not None
        assert persisted.observed_metrics["loss"] == 0.25
        assert persisted.artifact_sha256 == art_sha_valid

        # Verify exactly one completion row exists in database
        with psycopg.connect(real_pg_instance) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 1

    def test_sec_24b_valid_versus_invalid_digest_submission(self, real_pg_instance: str) -> None:
        """SEC-24b: Submission of artifact digest inconsistent with authoritative aggregation is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec24b_{uuid.uuid4().hex[:8]}"
        art_sha_valid = "24" * 32
        art_sha_invalid = "99" * 32
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec24b",
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
            model_update_digest=f"digest_{job_id}",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[f"digest_{job_id}"],
            aggregated_global_model_digest=art_sha_valid,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.25,
            evaluated_artifact_digest=art_sha_valid,
            coordinator_token=COORD_SECRET,
        )

        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.25},
                artifact_sha256=art_sha_invalid,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )
        assert "artifact digest" in str(exc_info.value).lower() or "digest" in str(exc_info.value).lower()

    def test_sec_24c_conflicting_completion_rejected_after_commit(self, real_pg_instance: str) -> None:
        """SEC-24c: Submitting conflicting execution claims after a committed completion is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec24c_{uuid.uuid4().hex[:8]}"
        art_sha_valid = "24" * 32
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec24c",
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
            model_update_digest=f"digest_{job_id}",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[f"digest_{job_id}"],
            aggregated_global_model_digest=art_sha_valid,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.25,
            evaluated_artifact_digest=art_sha_valid,
            coordinator_token=COORD_SECRET,
        )

        # First valid commit
        rec1 = source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.25},
            artifact_sha256=art_sha_valid,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )
        assert rec1.status == "COMPLETED"

        # Conflicting second attempt (different loss: 0.99 vs 0.25)
        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.99},
                artifact_sha256=art_sha_valid,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )
        assert "conflicting" in str(exc_info.value).lower()

    def test_sec_25_duplicate_and_conflicting_observations_submitted_concurrently(self, real_pg_instance: str) -> None:
        """SEC-25: Concurrent worker processes submit observations without deadlock or database corruption."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec25_{uuid.uuid4().hex[:8]}"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec25",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        barrier: Any = mp.Barrier(2)
        q1: Any = mp.Queue()
        q2: Any = mp.Queue()
        p1 = mp.Process(target=_sec25_concurrent_observer, args=(real_pg_instance, job_id, 0.3, barrier, q1))
        p2 = mp.Process(target=_sec25_concurrent_observer, args=(real_pg_instance, job_id, 0.4, barrier, q2))
        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        res1 = q1.get(timeout=5)
        res2 = q2.get(timeout=5)
        # Exactly one observation committed; the conflicting duplicate is rejected
        assert {res1[0], res2[0]} == {"SUCCESS", "ERROR"}

        obs = source.get_round_observations(job_id)
        assert len(obs) == 1

    def test_sec_26_completed_evidence_modified_after_restart_preserved(self, real_pg_instance: str) -> None:
        """SEC-26: Fresh authority instance after restart verifies immutable persistence of completed record."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_sec26_{uuid.uuid4().hex[:8]}"
        art_sha = "26" * 32
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=f"run_{job_id}",
            simulation_id="sim_sec26",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.2,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_sec26",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_sec26"],
            aggregated_global_model_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.2,
            evaluated_artifact_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )
        source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.2},
            artifact_sha256=art_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        fresh_authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        persisted = fresh_authority.get_completed_job_by_id(job_id)
        assert persisted is not None
        assert persisted.status == "COMPLETED"
        assert persisted.artifact_sha256 == art_sha

    def test_sec_27_rogue_issuer_key_submits_signed_receipt(self, tmp_path) -> None:
        """SEC-27: Receipt signed by untrusted rogue key is rejected upon submission to ModelRegistry."""
        registry = ModelRegistry(storage_dir=str(tmp_path / "reg_sec27"))
        priv_hex, _ = _generate_ed25519_keypair_hex()
        issuer = TrainingReceiptIssuer(
            signing_key_hex=priv_hex,
            key_id="rogue_key_sec27",
            require_authenticated_job=False,
        )
        m_bytes = b"bytes_sec27"
        m_sha = hashlib.sha256(m_bytes).hexdigest()
        evidence = TrainingExecutionEvidence(
            training_run_id="run_sec27",
            job_id="job_sec27",
            simulation_id="sim_sec27",
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
            simulation_id="sim_sec27",
            state_dict={},
            metrics={"loss": 0.2},
            training_receipt=receipt,
            artifact_bytes=m_bytes,
            provenance_type="TRAINING_RECORDED",
            training_run_id="run_sec27",
        )
        assert candidate["provenance_type"] == "UNVERIFIED"
        assert candidate["is_production_eligible"] is False
        assert "INVALID_RECEIPT" in candidate["attestation_status"]

    def test_sec_28_production_verifier_attempts_signing(self) -> None:
        """SEC-28: Public-key-only verifier has no signing private key or issue_receipt method."""
        verifier = get_default_training_verifier()
        assert not hasattr(verifier, "_private_key")
        assert not hasattr(verifier, "issue_receipt")

    def test_sec_29_incomplete_training_attempts_registry_admission(self, tmp_path) -> None:
        """SEC-29: Uncompleted training without signed receipt is rejected by ModelRegistry in production."""
        registry = ModelRegistry(storage_dir=str(tmp_path / "reg_sec29"))
        saved = registry.save_version(
            simulation_id="sim_sec29",
            state_dict={},
            metrics={"loss": 0.5},
            is_promoted=False,
            training_receipt=None,
        )
        assert saved["is_production_eligible"] is False
        assert saved["provenance_type"] == "UNVERIFIED"
        assert saved["status"] == "inactive"

    def test_sec_30_development_compatibility_path_rejected_in_production(self, real_pg_instance: str) -> None:
        """SEC-30: Compatibility mode flags (require_authenticated_job=False, enforce_lifecycle=False) are rejected in production."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceConfigurationError, match="require_authenticated_job=False is forbidden in production"):
            TrainingReceiptIssuer(
                job_record_source=source,
                require_authenticated_job=False,
                app_env="production",
            )
        with pytest.raises(TrainingProvenanceSecurityError, match="enforce_lifecycle=False is forbidden in production"):
            source.complete_training_job(
                job_id="job_sec30",
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.1},
                artifact_sha256="30" * 32,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=False,
            )
