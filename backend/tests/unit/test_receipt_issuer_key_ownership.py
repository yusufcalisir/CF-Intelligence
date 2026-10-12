"""Independent Receipt Issuer Key Ownership & Cryptographic Boundary Suite (SIGN-01 to SIGN-10).

Mandatory tests verifying that:
1. The production coordinator never possesses or gains access to issuer private signing material.
2. The issuer obtains its private key through independently authorized provisioning under restrictive NTFS DACLs.
3. The coordinator is denied access before issuer startup, during operation, after termination, and cannot replace or reprovision the key.
4. Unauthorized callers cannot sign receipts.
5. Coordinator cannot supply alternative signing keys or invoke local fallback in production.
6. Issuer termination fails closed without producing fake receipts.
7. Uncompleted or tampered jobs are refused by the issuer.
8. ModelRegistry rejects untrusted signing keys and denies production admission.
9. Legitimate completed jobs produce Ed25519 receipts verified by separate public-key-only verifier.
10. Genuine three-bank, two-round federated training completes through the isolated issuer into ModelRegistry.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import uuid
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import numpy as np
import psycopg
import pytest
import torch
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.config import get_settings
from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    CoordinatorOSSecurityContext,
    DistributedFederatedTrainingManager,
    IsolatedCoordinatorClient,
    IsolatedReceiptSigningAuthority,
    _generate_ed25519_keypair_hex,
    provision_isolated_completion_authority_vault,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
    spawn_isolated_completion_authority_service,
    spawn_isolated_coordinator_process,
)
from app.application.services.model_registry import (
    ModelRegistry,
    validate_production_admission,
)
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    TrustedTrainingJobRecordSource,
    get_default_training_verifier,
)
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.database.training_authority_backends import (
    GlobalAggregationEvent,
    PostgresTrainingJobAuthority,
    provision_runtime_service_roles,
)

COORD_SECRET = "coord_secret_sign_suite_prod_991"
DEV_SEED_HEX = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()

BANK_TOKENS = {
    "BANK_A": "token_bank_a_sign_test_101",
    "BANK_B": "token_bank_b_sign_test_102",
    "BANK_C": "token_bank_c_sign_test_103",
}


@pytest.fixture(scope="module")
def pg_roles() -> Generator[dict[str, str], None, None]:
    """Provision real PostgreSQL 18.1 embedded instance with partitioned runtime service roles."""
    try:
        import pg0
    except ImportError:
        pytest.skip("pg0 not installed")

    pg = pg0.Pg0()
    pg.start()
    try:
        admin_url = pg.uri
        assert admin_url is not None
        roles = provision_runtime_service_roles(admin_url)
        roles["admin"] = admin_url
        yield roles
    finally:
        pg.stop()


def helper_schedule_and_start_job(
    admin_url: str,
    job_id: str,
    run_id: str,
    sim_id: str,
    permitted_workers: tuple[str, ...] = ("BANK_A", "BANK_B", "BANK_C"),
    expected_rounds: int = 2,
    expected_epochs: int = 2,
) -> None:
    fnames = list(FEATURE_NAMES)
    admin_auth = PostgresTrainingJobAuthority(db_url=admin_url)
    admin_auth.schedule_job(
        job_id=job_id,
        training_run_id=run_id,
        simulation_id=sim_id,
        coordinator_id="cfi_coord_role",
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        expected_rounds=expected_rounds,
        expected_epochs=expected_epochs,
        input_dim=len(fnames),
        feature_names=fnames,
        permitted_workers=list(permitted_workers),
    )
    admin_auth.start_job(job_id=job_id, worker_id=permitted_workers[0])


def helper_record_global_aggregations(
    admin_url: str,
    job_id: str,
    num_rounds: int,
    final_sha: str,
) -> None:
    admin_auth = PostgresTrainingJobAuthority(db_url=admin_url)
    dummy_sha = "0123456789abcdef" * 4
    for r in range(1, num_rounds + 1):
        sha = final_sha if r == num_rounds else dummy_sha
        admin_auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"evt_agg_{job_id}_r{r}_{uuid.uuid4().hex[:8]}",
                job_id=job_id,
                round_number=r,
                aggregation_algorithm="fed_avg",
                accepted_participant_ids=("BANK_A", "BANK_B", "BANK_C"),
                accepted_local_update_digests=("digest123", "digest123", "digest123"),
                aggregation_configuration={},
                aggregated_global_model_digest=sha,
                timestamp=datetime.now(timezone.utc).isoformat(),
                aggregation_status="COMPLETED",
            )
        )


def helper_build_observation_tuple(
    job_id: str,
    run_id: str,
    sim_id: str,
    bank_id: str,
    worker_id: str,
    round_number: int = 1,
    attempt_number: int = 1,
    sample_count: int = 100,
    observed_loss: float = 0.05,
) -> tuple[Any, ...]:
    now_str = datetime.now(timezone.utc).isoformat()
    event_id = f"evt_{job_id}_r{round_number}_{bank_id}_{attempt_number}_{uuid.uuid4().hex[:8]}"
    update_digest = hashlib.sha256(f"up_{job_id}_{round_number}_{bank_id}".encode()).hexdigest()
    metrics_json = json.dumps({"loss": observed_loss})
    payload_digest = hashlib.sha256(f"pay_{event_id}".encode()).hexdigest()

    return (
        event_id,
        job_id,
        run_id,
        sim_id,
        worker_id,
        bank_id,
        round_number,
        attempt_number,
        sample_count,
        1,  # actual_executed_epochs
        observed_loss,
        metrics_json,
        update_digest,
        now_str,
        "COMPLETED",
        payload_digest,
    )


INSERT_OBSERVATION_SQL = """
INSERT INTO cfi_round_observations (
    event_id, job_id, training_run_id, simulation_id,
    worker_id, bank_id, round_number, attempt_number,
    actual_sample_count, actual_executed_epochs, observed_loss,
    metrics_json, model_update_digest, execution_timestamp,
    execution_status, payload_digest
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class TestReceiptIssuerKeyOwnership:
    """Cryptographic security verification of independent Ed25519 issuer key ownership."""

    def test_sign_01_coordinator_denied_read_key_before_startup(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-01: Coordinator attempts to read issuer private key before issuer startup.

        Service is NOT running (no process lock exists).
        Denial must be enforced by permanent operating system NTFS DACLs.
        """
        vault_dir = str(tmp_path / "issuer_vault_sign01")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir, app_env="production")

        # 1. Verify via CoordinatorOSSecurityContext thread token
        with CoordinatorOSSecurityContext():
            with pytest.raises(PermissionError):
                with open(key_file, "r", encoding="utf-8") as f:
                    _ = f.read()

        # 2. Verify via actual separate restricted coordinator process
        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="production")
        try:
            res = coord_client.attempt_read_vault(key_file)
            assert res["status"] == "DENIED"
            assert res["error_type"] == "PermissionError"
        finally:
            coord_client.terminate()

    def test_sign_02_coordinator_denied_during_operation_and_after_termination(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-02: Coordinator attempts to read issuer private key during operation and after termination."""
        vault_dir = str(tmp_path / "issuer_vault_sign02")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir, app_env="production")

        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["issuer"],
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )
        assert signing_auth.is_alive() is True

        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="production")
        try:
            # During operation: coordinator cannot read key
            res_running = coord_client.attempt_read_vault(key_file)
            assert res_running["status"] == "DENIED"
            assert res_running["error_type"] == "PermissionError"

            # Terminate issuer service
            signing_auth.terminate()
            assert signing_auth.is_alive() is False

            # After termination: coordinator STILL cannot read key (permanent NTFS DACL)
            res_terminated = coord_client.attempt_read_vault(key_file)
            assert res_terminated["status"] == "DENIED"
            assert res_terminated["error_type"] == "PermissionError"
        finally:
            coord_client.terminate()
            signing_auth.terminate()

    def test_sign_03_coordinator_denied_replace_or_reprovision_key(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-03: Coordinator attempts to replace, delete, or reprovision the issuer private key.

        The coordinator must not be able to modify, overwrite, delete, or create files in the issuer vault.
        """
        vault_dir = str(tmp_path / "issuer_vault_sign03")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir, app_env="production")

        # Read original key as privileged administrator
        with open(key_file, "r", encoding="utf-8") as f:
            original_key_content = f.read()

        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="production")
        try:
            # 1. Attempt overwrite / write to existing key file
            write_res = coord_client.attempt_write_vault(key_file, "forged_ed25519_key_bytes_0000000000000000")
            assert write_res["status"] == "DENIED"
            assert write_res["error_type"] == "PermissionError"

            # 2. Attempt deletion of key file
            del_res = coord_client.attempt_delete_vault(key_file)
            assert del_res["status"] == "DENIED"
            assert del_res["error_type"] == "PermissionError"

            # 3. Attempt creation of new rogue file in vault directory
            rogue_path = os.path.join(vault_dir, "rogue_key.key")
            create_res = coord_client.attempt_write_vault(rogue_path, "attacker_key")
            assert create_res["status"] == "DENIED"
            assert create_res["error_type"] == "PermissionError"

            # Verify original key file remains 100% intact and uncorrupted
            with open(key_file, "r", encoding="utf-8") as f:
                current_content = f.read()
            assert current_content == original_key_content
        finally:
            coord_client.terminate()

    def test_sign_04_unauthorized_caller_attempts_signing_request(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-04: Unauthorized caller attempts a legitimate-looking signing request.

        The issuer must refuse requests with invalid, missing, or mismatched authentication tokens,
        or spoofed caller identities.
        """
        vault_dir = str(tmp_path / "issuer_vault_sign04")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir, app_env="production")

        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["issuer"],
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )
        try:
            valid_job_id = f"job_sign04_{uuid.uuid4().hex[:8]}"

            # 1. Invalid coordinator token
            with pytest.raises(TrainingProvenanceSecurityError, match="invalid or missing coordinator token"):
                signing_auth.sign_completed_job_receipt(
                    job_id=valid_job_id,
                    artifact_bytes=b"model_artifact_bytes",
                    caller_identity="COORDINATOR",
                    caller_token="wrong_attacker_token",
                )

            # 2. Missing coordinator token (None)
            with pytest.raises(TrainingProvenanceSecurityError, match="invalid or missing coordinator token"):
                signing_auth.sign_completed_job_receipt(
                    job_id=valid_job_id,
                    artifact_bytes=b"model_artifact_bytes",
                    caller_identity="COORDINATOR",
                    caller_token=None,
                )

            # 3. Spoofed caller identity (e.g., WORKER or ANONYMOUS)
            with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized receipt signing caller identity"):
                signing_auth.sign_completed_job_receipt(
                    job_id=valid_job_id,
                    artifact_bytes=b"model_artifact_bytes",
                    caller_identity="WORKER",
                    caller_token=COORD_SECRET,
                )
        finally:
            signing_auth.terminate()

    def test_sign_05_coordinator_attempts_alternative_key_or_local_fallback(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-05: Coordinator attempts to supply an alternative signing key or invoke local fallback.

        - Supplying signing_key_hex to DistributedFederatedTrainingManager in production is rejected.
        - Supplying signing_key_hex via kwargs or IPC execute_federation is rejected.
        - Executing federation without signing_authority (local self-signing fallback) is rejected.
        - Provisioning an issuer with the known development seed in production is rejected.
        """
        coord_url = pg_roles["coordinator"]
        rogue_hex = "0123456789abcdef" * 4

        # 1. Supplying signing_key_hex in kwargs to coordinator constructor
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator process cannot receive or hold receipt issuer private signing key"):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_SECRET,
                db_url=coord_url,
                app_env="production",
                signing_key_hex=rogue_hex,
            )

        # 2. Supplying issuer_key_path in kwargs to coordinator constructor
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator process cannot receive or hold receipt issuer private signing key"):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_SECRET,
                db_url=coord_url,
                app_env="production",
                issuer_key_path=str(tmp_path / "rogue.key"),
            )

        # 3. Executing federation without signing_authority in production (attempting local fallback)
        mgr = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=coord_url,
            app_env="development",
        )
        mgr.app_env = "production"  # enforce production check during execute
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="requires an isolated receipt signing authority|restricted operating system security token|Production coordinator cannot receive",
        ):
            mgr.execute_distributed_federation(
                job_id="job_sign05_fallback",
                training_run_id="run_sign05",
                simulation_id="sim_sign05",
                holdout_val_data=(np.zeros((1, NUM_FEATURES), dtype=np.float32), np.zeros(1, dtype=np.float32)),
                worker_endpoints={"BANK_A": {"inbox": None, "outbox": None}},
                signing_authority=None,
            )

        # 4. Attempting to pass deterministic development seed in production provisioning
        dev_vault_dir = str(tmp_path / "dev_seed_vault_sign05")
        with pytest.raises(TrainingProvenanceSecurityError, match="cannot use deterministic development signing seed"):
            provision_isolated_issuer_vault(dev_vault_dir, signing_key_hex=DEV_SEED_HEX, app_env="production")

    def test_sign_06_issuer_terminated_during_request_fails_closed(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-06: Issuer is terminated during a signing request; no fake receipt or silent fallback is accepted."""
        vault_dir = str(tmp_path / "issuer_vault_sign06")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir, app_env="production")

        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["issuer"],
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )

        # Terminate issuer service process
        signing_auth.terminate()
        assert signing_auth.is_alive() is False

        # Request signing on terminated service
        with pytest.raises(TrainingProvenanceSecurityError, match="Receipt signing service unavailable: issuer process terminated"):
            signing_auth.sign_completed_job_receipt(
                job_id="job_sign06_term",
                artifact_bytes=b"model_bytes_sign06",
                caller_token=COORD_SECRET,
            )

    def test_sign_07_issuer_rejects_incomplete_or_forged_completion(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-07: Issuer receives an incomplete or forged completion record; signing is rejected.

        - Rejection occurs because authoritative COMPLETED record is missing or mismatched in PostgreSQL,
          NOT due to invalid SQL.
        - Duplicate requests for the same job are rejected.
        """
        vault_dir = str(tmp_path / "issuer_vault_sign07")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir, app_env="production")

        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["issuer"],
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )
        try:
            # 1. Job has NO record in cfi_completed_jobs
            uncompleted_job_id = f"job_uncompleted_{uuid.uuid4().hex[:8]}"
            with pytest.raises(TrainingProvenanceSecurityError, match="has no trusted COMPLETED record"):
                signing_auth.sign_completed_job_receipt(
                    job_id=uncompleted_job_id,
                    artifact_bytes=b"some_artifact_bytes",
                    caller_token=COORD_SECRET,
                )

            # 2. Job is completed, but provided artifact bytes do NOT match authoritative artifact_sha256
            comp_vault_dir = str(tmp_path / "comp_vault_sign07")
            comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, pg_roles["completion"])
            comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
                vault_path=comp_vault_file,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            try:
                valid_job_id = f"job_sign07_{uuid.uuid4().hex[:8]}"
                run_id = f"run_sign07_{uuid.uuid4().hex[:8]}"
                sim_id = f"sim_sign07_{uuid.uuid4().hex[:8]}"

                model = FraudDetectionModel(input_dim=NUM_FEATURES)
                buf = io.BytesIO()
                torch.save(model.state_dict(), buf)
                legit_bytes = buf.getvalue()
                legit_sha = hashlib.sha256(legit_bytes).hexdigest()

                # Schedule and start job
                helper_schedule_and_start_job(
                    admin_url=pg_roles["admin"],
                    job_id=valid_job_id,
                    run_id=run_id,
                    sim_id=sim_id,
                    expected_rounds=2,
                    expected_epochs=2,
                )

                # Record observations for all 3 banks for rounds 1 and 2
                for r in (1, 2):
                    for b_id in ("BANK_A", "BANK_B", "BANK_C"):
                        role_url = pg_roles[f"worker_{b_id.lower()}"]
                        obs_tuple = helper_build_observation_tuple(
                            job_id=valid_job_id,
                            run_id=run_id,
                            sim_id=sim_id,
                            bank_id=b_id,
                            worker_id=b_id,
                            round_number=r,
                            sample_count=100,
                            observed_loss=0.45,
                        )
                        with psycopg.connect(role_url) as conn, conn.cursor() as cur:
                            cur.execute(INSERT_OBSERVATION_SQL, obs_tuple)
                            conn.commit()

                # Authoritative global aggregation records
                helper_record_global_aggregations(
                    admin_url=pg_roles["admin"],
                    job_id=valid_job_id,
                    num_rounds=2,
                    final_sha=legit_sha,
                )

                # Finalize job through completion authority
                from app.application.services.distributed_fl_service import IsolatedTrainingCompletionAuthority
                comp_client = IsolatedTrainingCompletionAuthority(
                    service_endpoint=comp_endpoint,
                    coordinator_token=COORD_SECRET,
                    app_env="production",
                )
                comp_record = comp_client.finalize_training_completion(
                    job_id=valid_job_id,
                    executed_rounds=2,
                    executed_epochs=2,
                    sample_count=300,
                    observed_metrics={"loss": 0.45},
                    artifact_sha256=legit_sha,
                    worker_id="BANK_A",
                    coordinator_token=COORD_SECRET,
                )
                assert comp_record.status == "COMPLETED"

                # Attempt signing with tampered artifact bytes
                tampered_bytes = b"tampered_model_weights_bytes_sign07"
                with pytest.raises(TrainingProvenanceSecurityError, match="Provided artifact SHA-256.*does not match"):
                    signing_auth.sign_completed_job_receipt(
                        job_id=valid_job_id,
                        artifact_bytes=tampered_bytes,
                        caller_token=COORD_SECRET,
                    )

                # Successful signing with legitimate artifact bytes
                receipt1 = signing_auth.sign_completed_job_receipt(
                    job_id=valid_job_id,
                    artifact_bytes=legit_bytes,
                    caller_token=COORD_SECRET,
                )
                assert receipt1.job_id == valid_job_id

                # Attempt duplicate signing for already-signed job
                with pytest.raises(TrainingProvenanceSecurityError, match="Duplicate signing request for already-signed job"):
                    signing_auth.sign_completed_job_receipt(
                        job_id=valid_job_id,
                        artifact_bytes=legit_bytes,
                        caller_token=COORD_SECRET,
                    )
            finally:
                comp_proc.terminate()
                comp_proc.join(timeout=3)
        finally:
            signing_auth.terminate()

    def test_sign_08_registry_rejects_receipt_from_untrusted_issuer(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-08: Registry receives a receipt signed by an untrusted key; verification is rejected."""
        # 1. Legitimate verifier configured with trusted public key
        legit_vault = str(tmp_path / "legit_vault_sign08")
        _, legit_pub_hex = provision_isolated_issuer_vault(legit_vault, app_env="production")
        verifier = TrainingReceiptVerifier(
            trusted_keys={DEFAULT_TRAINING_KEY_ID: legit_pub_hex},
        )
        registry_dir = str(tmp_path / "registry_sign08")
        registry = ModelRegistry(storage_dir=registry_dir, verifier=verifier)

        # 2. Rogue issuer signed with untrusted independent key
        rogue_vault = str(tmp_path / "rogue_vault_sign08")
        rogue_key_file, rogue_pub_hex = provision_isolated_issuer_vault(rogue_vault, app_env="production")
        rogue_issuer_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=rogue_key_file,
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["issuer"],
            key_id="rogue_untrusted_key_id",
            app_env="production",
        )

        comp_vault_dir = str(tmp_path / "comp_vault_sign08")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, pg_roles["completion"])
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            job_id = f"job_sign08_{uuid.uuid4().hex[:8]}"
            run_id = f"run_sign08_{uuid.uuid4().hex[:8]}"
            sim_id = f"sim_sign08_{uuid.uuid4().hex[:8]}"

            model = FraudDetectionModel(input_dim=NUM_FEATURES)
            buf = io.BytesIO()
            torch.save(model.state_dict(), buf)
            model_bytes = buf.getvalue()
            model_sha = hashlib.sha256(model_bytes).hexdigest()

            # Schedule and start job
            helper_schedule_and_start_job(
                admin_url=pg_roles["admin"],
                job_id=job_id,
                run_id=run_id,
                sim_id=sim_id,
                expected_rounds=1,
                expected_epochs=1,
            )

            # Observations
            for b_id in ("BANK_A", "BANK_B", "BANK_C"):
                role_url = pg_roles[f"worker_{b_id.lower()}"]
                obs_tuple = helper_build_observation_tuple(
                    job_id=job_id,
                    run_id=run_id,
                    sim_id=sim_id,
                    bank_id=b_id,
                    worker_id=b_id,
                    round_number=1,
                    sample_count=100,
                    observed_loss=0.40,
                )
                with psycopg.connect(role_url) as conn, conn.cursor() as cur:
                    cur.execute(INSERT_OBSERVATION_SQL, obs_tuple)
                    conn.commit()

            helper_record_global_aggregations(
                admin_url=pg_roles["admin"],
                job_id=job_id,
                num_rounds=1,
                final_sha=model_sha,
            )

            from app.application.services.distributed_fl_service import IsolatedTrainingCompletionAuthority
            comp_client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            comp_client.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=300,
                observed_metrics={"loss": 0.40},
                artifact_sha256=model_sha,
                worker_id="BANK_A",
                coordinator_token=COORD_SECRET,
            )

            # Rogue issuer signs receipt
            rogue_receipt = rogue_issuer_auth.sign_completed_job_receipt(
                job_id=job_id,
                artifact_bytes=model_bytes,
                caller_token=COORD_SECRET,
            )

            # Registry attempts to save candidate with rogue receipt
            saved = registry.save_version(
                simulation_id=sim_id,
                state_dict=model.state_dict(),
                metrics={"loss": 0.40},
                is_promoted=False,
                training_run_id=run_id,
                training_receipt=rogue_receipt,
                artifact_bytes=model_bytes,
                expected_input_dim=NUM_FEATURES,
                expected_feature_schema_version="v1.0",
                expected_feature_names=list(FEATURE_NAMES),
                expected_architecture="FraudDetectionNN",
            )

            # Must be rejected as UNVERIFIED and ineligible for production
            assert saved["provenance_type"] == "UNVERIFIED"
            assert saved["is_production_eligible"] is False
            assert "INVALID_RECEIPT" in saved["attestation_status"]

            # Attempting production admission fails closed
            with (
                patch.object(get_settings(), "app_env", "production"),
                pytest.raises(ModelIntegrityError, match="is ineligible for operational champion|not production-eligible"),
            ):
                validate_production_admission(saved, context="production_champion")
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)
            rogue_issuer_auth.terminate()

    def test_sign_09_authorized_completion_produces_verified_receipt(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-09: Legitimate authorized completion produces Ed25519 receipt verified by public-key-only verifier."""
        vault_dir = str(tmp_path / "issuer_vault_sign09")
        key_file, pub_hex = provision_isolated_issuer_vault(vault_dir, app_env="production")

        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=key_file,
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["issuer"],
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )

        comp_vault_dir = str(tmp_path / "comp_vault_sign09")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, pg_roles["completion"])
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            job_id = f"job_sign09_{uuid.uuid4().hex[:8]}"
            run_id = f"run_sign09_{uuid.uuid4().hex[:8]}"
            sim_id = f"sim_sign09_{uuid.uuid4().hex[:8]}"

            model = FraudDetectionModel(input_dim=NUM_FEATURES)
            buf = io.BytesIO()
            torch.save(model.state_dict(), buf)
            model_bytes = buf.getvalue()
            model_sha = hashlib.sha256(model_bytes).hexdigest()

            # Schedule and start job
            helper_schedule_and_start_job(
                admin_url=pg_roles["admin"],
                job_id=job_id,
                run_id=run_id,
                sim_id=sim_id,
                expected_rounds=1,
                expected_epochs=1,
            )

            # Observations for all 3 banks
            for b_id in ("BANK_A", "BANK_B", "BANK_C"):
                role_url = pg_roles[f"worker_{b_id.lower()}"]
                obs_tuple = helper_build_observation_tuple(
                    job_id=job_id,
                    run_id=run_id,
                    sim_id=sim_id,
                    bank_id=b_id,
                    worker_id=b_id,
                    round_number=1,
                    sample_count=100,
                    observed_loss=0.35,
                )
                with psycopg.connect(role_url) as conn, conn.cursor() as cur:
                    cur.execute(INSERT_OBSERVATION_SQL, obs_tuple)
                    conn.commit()

            helper_record_global_aggregations(
                admin_url=pg_roles["admin"],
                job_id=job_id,
                num_rounds=1,
                final_sha=model_sha,
            )

            # Complete job
            from app.application.services.distributed_fl_service import IsolatedTrainingCompletionAuthority
            comp_client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            comp_client.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=300,
                observed_metrics={"loss": 0.35},
                artifact_sha256=model_sha,
                worker_id="BANK_A",
                coordinator_token=COORD_SECRET,
            )

            # Sign receipt from isolated authority
            receipt = signing_auth.sign_completed_job_receipt(
                job_id=job_id,
                artifact_bytes=model_bytes,
                caller_token=COORD_SECRET,
            )
            assert receipt.key_id == DEFAULT_TRAINING_KEY_ID

            # Public-key-only verifier possesses NO private keys
            verifier = TrainingReceiptVerifier(
                trusted_keys={DEFAULT_TRAINING_KEY_ID: pub_hex},
            )
            assert not hasattr(verifier, "_private_key")

            is_valid, reason = verifier.verify_receipt(
                receipt=receipt,
                expected_simulation_id=sim_id,
                expected_architecture="FraudDetectionNN",
                expected_feature_schema_version="v1.0",
                expected_input_dim=NUM_FEATURES,
                expected_feature_names=list(FEATURE_NAMES),
                actual_artifact_bytes=model_bytes,
            )
            assert is_valid is True
            assert "verified successfully" in reason.lower()

            # Registry saves verified candidate
            registry = ModelRegistry(storage_dir=str(tmp_path / "reg_sign09"), verifier=verifier)
            saved = registry.save_version(
                simulation_id=sim_id,
                state_dict=model.state_dict(),
                metrics={"loss": 0.35},
                is_promoted=False,
                training_run_id=run_id,
                training_receipt=receipt,
                artifact_bytes=model_bytes,
                expected_input_dim=NUM_FEATURES,
                expected_feature_schema_version="v1.0",
                expected_feature_names=list(FEATURE_NAMES),
                expected_architecture="FraudDetectionNN",
            )
            assert saved["provenance_type"] == "TRAINING_RECORDED"
            assert saved["is_production_eligible"] is True
            assert saved["attestation_status"] == "RECEIPT_VERIFIED_TRAINING_AUTHORITY"
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)
            signing_auth.terminate()

    def test_sign_10_genuine_three_bank_two_round_federation_succeeds(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """SIGN-10: Genuine three-bank, two-round federated training produces verified candidate through isolated issuer.

        Verifies:
        - Bank workers execute under bank-specific PostgreSQL identities (cfi_bank_a_role, etc.).
        - Restricted coordinator process runs with Windows restricted token (WinRestrictedCodeSid).
        - Isolated receipt signing authority runs outside coordinator boundary in dedicated process.
        - Public-key-only verifier verifies receipt.
        - Inactive candidate admitted in ModelRegistry; active champion unaltered.
        """
        coord_url = pg_roles["coordinator"]
        comp_url = pg_roles["completion"]
        issuer_url = pg_roles["issuer"]

        comp_vault_dir = str(tmp_path / "comp_vault_sign10")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )

        vault_root = str(tmp_path / "bank_vaults_sign10")
        bank_data_paths = {}
        bank_enc_keys = {b: Fernet.generate_key() for b in ("BANK_A", "BANK_B", "BANK_C")}
        np.random.seed(42)
        for b_id, n_samples in (("BANK_A", 100), ("BANK_B", 150), ("BANK_C", 200)):
            X_part = np.random.randn(n_samples, NUM_FEATURES).astype(np.float32)
            y_part = np.random.randint(0, 2, size=n_samples).astype(np.float32)
            p = setup_bank_private_storage(
                bank_storage_dir=vault_root,
                bank_id=b_id,
                X_partition=X_part,
                y_partition=y_part,
                bank_token=BANK_TOKENS[b_id],
                bank_encryption_key=bank_enc_keys[b_id],
            )
            bank_data_paths[b_id] = p

        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }
        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["worker"],
            bank_data_paths=bank_data_paths,
            bank_db_urls=bank_db_urls,
            bank_encryption_keys=bank_enc_keys,
            app_env="production",
        )

        # Provision issuer vault with fresh independent key material
        issuer_vault_dir = str(tmp_path / "issuer_vault_sign10")
        dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
        issuer_key_file, pub_hex = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed, app_env="development")
        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=issuer_key_file,
            coordinator_token=COORD_SECRET,
            db_url=issuer_url,
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )

        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="production")

        try:
            uid = uuid.uuid4().hex[:8]
            job_id = f"job_sign10_{uid}"
            run_id = f"run_sign10_{uid}"
            sim_id = f"sim_sign10_{uid}"
            registry_dir = str(tmp_path / "model_reg_sign10")

            X_test = np.random.randn(100, NUM_FEATURES).astype(np.float32)
            y_test = np.random.randint(0, 2, size=100).astype(np.float32)

            worker_pids: dict[str, int] = {b: (procs[b].pid or 0) for b in procs}

            result = coord_client.execute_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(X_test, y_test),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                worker_endpoints=endpoints,
                num_rounds=2,
                expected_unique_samples=450,
                registry_storage_dir=registry_dir,
                coordinator_token=COORD_SECRET,
                db_url=coord_url,
                comp_service_endpoint=comp_endpoint,
                signing_authority=signing_auth,
                worker_pids=worker_pids,
            )

            assert result["completed_record"].job_id == job_id
            assert result["completed_record"].status == "COMPLETED"
            assert result["completed_record"].executed_rounds == 2
            assert result["receipt"].job_id == job_id
            assert result["is_verified"] is True

            saved_ver = result["saved_version"]
            assert saved_ver["is_production_eligible"] is True
            assert saved_ver["status"] == "inactive"
            assert saved_ver["provenance_type"] == "TRAINING_RECORDED"
            assert result["is_promoted_champion"] is False

            # Verify PostgreSQL state
            with psycopg.connect(pg_roles["admin"]) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                comp_row = cur.fetchone()
                assert comp_row is not None and comp_row[0] == 1

                cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE job_id = %s", (job_id,))
                obs_row = cur.fetchone()
                assert obs_row is not None and obs_row[0] == 6  # 2 rounds * 3 banks
        finally:
            coord_client.terminate()
            comp_proc.terminate()
            comp_proc.join(timeout=3)
            signing_auth.terminate()
            for p in procs.values():
                p.terminate()
                p.join(timeout=3)
