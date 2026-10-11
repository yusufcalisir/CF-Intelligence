"""Executable Security Verification Suite for Independent, Atomic Training Completion Authority.

Verifies the mandatory proof matrix against a real PostgreSQL 18.1 instance:
- COMP-R01: Coordinator process cannot read completion credentials (memory, env, argv, and OS-exclusive vault boundary denial).
- COMP-R02: Coordinator cannot create a privileged completion connection (SET ROLE and direct INSERT denied with SQLSTATE 42501).
- COMP-R03: Unauthorized completion IPC/service request is rejected (handshake and token verification fail closed).
- COMP-R04: Authorized completion request succeeds (independent service reconciles evidence and atomically inserts completed record).
- COMP-R05: Real concurrent evidence mutation cannot invalidate finalization (advisory xact lock serializes; post-finalization write rejected).
- COMP-R06: Worker is terminated inside an open transaction (in-transaction checkpoint reached; killed process rolls back cleanly; 0 partial rows).
- COMP-R07: Post-crash retry preserves correct completion state (restarted service safely completes job; exactly 1 completed row).
- COMP-R08: Concurrent independent clients resolve safely (independent connection channels serialized via PostgreSQL; no mismatched responses).
- COMP-R09: Forged completion produces no trusted receipt or Registry candidate (uncompleted job rejected by issuer and Registry).
- COMP-R10: Genuine three-bank training still completes through the independent boundary (end-to-end 3-bank federated execution).
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from typing import Any
import uuid

from cryptography.fernet import Fernet
import numpy as np
import psycopg
import psycopg.conninfo
import pytest

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    IsolatedTrainingCompletionAuthority,
    _generate_ed25519_keypair_hex,
    provision_isolated_completion_authority_vault,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
    spawn_isolated_completion_authority_service,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrustedTrainingJobRecordSource,
    get_default_training_verifier,
)
from app.infrastructure.database.training_authority_backends import (
    GlobalAggregationEvent,
    ModelEvaluationEvent,
    PostgresTrainingJobAuthority,
    RoundObservation,
    compute_evidence_fingerprint,
    provision_runtime_service_roles,
)

COORD_TOKEN = "coordinator_secret_token_comp_suite_9921"
BANK_A_TOKEN = "bank_a_secret_token_comp_suite_1101"
BANK_B_TOKEN = "bank_b_secret_token_comp_suite_2202"
BANK_C_TOKEN = "bank_c_secret_token_comp_suite_3303"

BANK_TOKENS = {
    "BANK_A": BANK_A_TOKEN,
    "BANK_B": BANK_B_TOKEN,
    "BANK_C": BANK_C_TOKEN,
}


@pytest.fixture(scope="module")
def real_pg_instance() -> Any:
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


@pytest.fixture(scope="module")
def pg_roles(real_pg_instance: str) -> dict[str, str]:
    """Provision isolated PostgreSQL service roles for coordinator, completion, workers, issuer, and serving."""
    return provision_runtime_service_roles(real_pg_instance, app_env="production")


def helper_setup_valid_observed_job(
    admin_url: str,
    job_id: str,
    run_id: str,
    sim_id: str,
    artifact_sha: str,
    num_rounds: int = 1,
    participating_banks: tuple[str, ...] = ("BANK_A", "BANK_B", "BANK_C"),
    samples_per_bank: int = 150,
    observed_loss: float = 0.05,
    auc_roc: float = 0.94,
) -> dict[str, Any]:
    """Populate PostgreSQL authority with genuine scheduled, started, observation, aggregation, and evaluation events."""
    now_str = datetime.now(timezone.utc).isoformat()
    fnames = list(FEATURE_NAMES)
    admin_auth = PostgresTrainingJobAuthority(db_url=admin_url)

    # 1. Schedule job
    admin_auth.schedule_job(
        job_id=job_id,
        training_run_id=run_id,
        simulation_id=sim_id,
        coordinator_id="cfi_coord_role",
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        expected_rounds=num_rounds,
        expected_epochs=num_rounds,
        input_dim=len(fnames),
        feature_names=fnames,
        permitted_workers=list(participating_banks),
    )

    # 2. Start job
    admin_auth.start_job(job_id=job_id, worker_id=participating_banks[0])

    # 3. Record round observations for each bank
    bank_sample_counts = {
        "BANK_A": 100,
        "BANK_B": 150,
        "BANK_C": 200,
    }
    total_samples = sum(bank_sample_counts[b] for b in participating_banks)

    for r in range(1, num_rounds + 1):
        digests: list[str] = []
        for b in participating_banks:
            up_digest = hashlib.sha256(f"update_{job_id}_r{r}_{b}".encode()).hexdigest()
            digests.append(up_digest)
            admin_auth.record_observation(
                RoundObservation(
                    event_id=f"obs_{job_id}_r{r}_{b}",
                    job_id=job_id,
                    training_run_id=run_id,
                    simulation_id=sim_id,
                    worker_id=b,
                    bank_id=b,
                    round_number=r,
                    attempt_number=1,
                    actual_sample_count=bank_sample_counts[b],
                    actual_executed_epochs=1,
                    observed_loss=observed_loss,
                    metrics={"loss": observed_loss},
                    execution_timestamp=now_str,
                    model_update_digest=up_digest,
                    execution_status="COMPLETED",
                )
            )

        # 4. Record global aggregation
        admin_auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"agg_{job_id}_r{r}",
                job_id=job_id,
                round_number=r,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=participating_banks,
                accepted_local_update_digests=tuple(digests),
                aggregation_configuration={"weighting": "sample_count"},
                aggregated_global_model_digest=artifact_sha,
                timestamp=now_str,
                aggregation_status="COMPLETED",
            )
        )

    # 5. Record model evaluation observations
    admin_auth.record_model_evaluation(
        ModelEvaluationEvent(
            event_id=f"eval_auc_{job_id}",
            job_id=job_id,
            evaluation_run_id=run_id,
            metric_name="auc_roc",
            metric_value=auc_roc,
            evaluated_artifact_digest=artifact_sha,
            evaluator_identity="cfi_coord_role",
            evaluation_timestamp=now_str,
        )
    )
    admin_auth.record_model_evaluation(
        ModelEvaluationEvent(
            event_id=f"eval_loss_{job_id}",
            job_id=job_id,
            evaluation_run_id=run_id,
            metric_name="loss",
            metric_value=observed_loss,
            evaluated_artifact_digest=artifact_sha,
            evaluator_identity="cfi_coord_role",
            evaluation_timestamp=now_str,
        )
    )

    return {
        "job_id": job_id,
        "run_id": run_id,
        "sim_id": sim_id,
        "artifact_sha": artifact_sha,
        "total_samples": total_samples,
    }


class TestCompletionAuthoritySecurity:
    """Security verification test suite enforcing genuinely independent, atomic training completion authority."""

    def test_comp_r01_coordinator_cannot_read_completion_credentials(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R01: Prove coordinator runtime cannot obtain completion database credentials.

        Verifies:
        - Coordinator object graph has zero attributes holding completion credentials or URLs.
        - Process environment has no CFI_TRAINING_JOB_COMPLETION_DB_URL.
        - Process arguments (sys.argv) contain no completion credentials.
        - Genuine OS-level access denial: Attempting to read the completion authority vault
          fails with PermissionError due to the kernel-level exclusive file lock.
        """
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]

        # 1. Privileged bootstrap provisions vault
        vault_dir = str(tmp_path / "comp_vault_r01")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)

        # 2. Spawn isolated completion service owning the vault
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )
        try:
            # 3. Coordinator is initialized with service_endpoint only
            comp_auth = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_TOKEN,
                app_env="production",
            )
            coordinator = DistributedFederatedTrainingManager(
                coordinator_token=COORD_TOKEN,
                db_url=coord_url,
                app_env="production",
            )

            # Assert coordinator object graph contains NO completion URL
            assert not hasattr(coordinator, "completion_db_url")
            assert not hasattr(coordinator.authority_source, "_completion_db_url")
            backend = coordinator.authority_source._db._backend
            assert isinstance(backend, PostgresTrainingJobAuthority)
            assert not hasattr(backend, "_completion_db_url")

            # Assert process environment contains no completion database URL
            assert "CFI_TRAINING_JOB_COMPLETION_DB_URL" not in os.environ

            # Assert sys.argv contains no completion password or role
            comp_parsed = psycopg.conninfo.conninfo_to_dict(comp_url)
            raw_password = comp_parsed.get("password")
            comp_password: str = str(raw_password) if raw_password is not None else ""
            if comp_password:
                for arg in sys.argv:
                    assert comp_password not in arg

            # Assert genuine OS kernel boundary access denial on vault file
            with pytest.raises(PermissionError):
                with open(vault_file, "r", encoding="utf-8") as f:
                    _ = f.read()
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    def test_comp_r02_coordinator_cannot_create_privileged_connection(
        self, pg_roles: dict[str, str]
    ) -> None:
        """COMP-R02: Prove coordinator principal cannot create or bypass to privileged completion connection.

        Attempts:
        - Connecting as cfi_completion_role with coordinator credentials fails.
        - SET ROLE cfi_completion_role under coordinator session fails with SQLSTATE 42501.
        - Direct INSERT on cfi_completed_jobs under coordinator session fails with SQLSTATE 42501.
        """
        coord_url = pg_roles["coordinator"]
        admin_url = pg_roles["admin"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_coord_bypass_{uid}"

        # 1. Attempt SET ROLE cfi_completion_role under coordinator session
        with psycopg.connect(coord_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc:
                    cur.execute("SET ROLE cfi_completion_role;")
                assert exc.value.sqlstate == "42501"
                conn.rollback()

        # 2. Attempt direct SQL INSERT into cfi_completed_jobs under coordinator session
        with psycopg.connect(coord_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc:
                    cur.execute(
                        """
                        INSERT INTO cfi_completed_jobs (
                            job_id, training_run_id, simulation_id, completed_at,
                            executed_rounds, executed_epochs, sample_count,
                            observed_metrics_json, artifact_sha256, architecture,
                            feature_schema_version, input_dim, feature_names_json,
                            worker_id, status, git_commit_hash, dataset_manifest_id,
                            evidence_fingerprint
                        ) VALUES (
                            %s, 'run_bypass', 'sim_bypass', NOW(), 1, 1, 100, '{}',
                            'fake_sha', 'FraudDetectionNN', 'v1.0', 10, '[]',
                            'FEDERATED_COORDINATOR', 'COMPLETED', NULL, NULL, 'fake_fp'
                        )
                        """,
                        (job_id,),
                    )
                assert exc.value.sqlstate == "42501"
                conn.rollback()

        # 3. Verify under admin authority that 0 rows were persisted
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_comp_r03_unauthorized_completion_request_rejected(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R03: Prove unauthorized completion service requests fail closed.

        Verifies:
        - Request with invalid coordinator token is rejected during IPC authentication.
        - No completed job record is written to PostgreSQL.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        vault_dir = str(tmp_path / "comp_vault_r03")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )
        try:
            uid = uuid.uuid4().hex[:8]
            job_id = f"job_unauth_{uid}"
            run_id = f"run_unauth_{uid}"
            sim_id = f"sim_unauth_{uid}"
            art_sha = hashlib.sha256(b"unauth_test").hexdigest()

            helper_setup_valid_observed_job(
                admin_url=admin_url,
                job_id=job_id,
                run_id=run_id,
                sim_id=sim_id,
                artifact_sha=art_sha,
            )

            # Connect with wrong authentication token
            client_unauth = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token="FORGED_COORDINATOR_TOKEN_INVALID",
                app_env="production",
            )

            with pytest.raises(TrainingProvenanceSecurityError):
                client_unauth.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                )

            # Verify no completed job was persisted in PostgreSQL
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 0
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    def test_comp_r04_authorized_completion_request_succeeds(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R04: Prove authorized completion request succeeds through independent boundary.

        Verifies:
        - Request with valid coordinator token succeeds.
        - Atomic validation and write under cfi_completion_role succeeds.
        - Exactly 1 authoritative row is written to PostgreSQL.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        vault_dir = str(tmp_path / "comp_vault_r04")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )
        try:
            uid = uuid.uuid4().hex[:8]
            job_id = f"job_auth_{uid}"
            run_id = f"run_auth_{uid}"
            sim_id = f"sim_auth_{uid}"
            art_sha = hashlib.sha256(b"auth_test").hexdigest()

            helper_setup_valid_observed_job(
                admin_url=admin_url,
                job_id=job_id,
                run_id=run_id,
                sim_id=sim_id,
                artifact_sha=art_sha,
            )

            client_auth = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_TOKEN,
                app_env="production",
            )

            record = client_auth.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=450,
                observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                artifact_sha256=art_sha,
            )

            assert record.job_id == job_id
            assert record.artifact_sha256 == art_sha
            assert record.status == "COMPLETED"

            # Verify persisted row in PostgreSQL
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 1
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    def test_comp_r05_concurrent_evidence_mutation_serialized_or_rejected(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R05: Prove real concurrent evidence mutation cannot invalidate finalization.

        Verifies:
        - PostgreSQL transaction-level advisory locks serialize evidence writes and completion.
        - A concurrent evidence write attempting to add an observation while completion is reconciling
          is serialized until completion commits, and then rejected because the job is finalized.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]
        worker_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_concur_write_{uid}"
        run_id = f"run_concur_write_{uid}"
        sim_id = f"sim_concur_write_{uid}"
        art_sha = hashlib.sha256(b"concur_write_test").hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
            num_rounds=1,
        )

        authority_comp = PostgresTrainingJobAuthority(db_url=comp_url)
        authority_worker = PostgresTrainingJobAuthority(db_url=worker_url)

        marker_file = str(tmp_path / f"checkpoint_marker_r05_{uid}.txt")
        obs_outcome: dict[str, bool] = {"rejected": False}
        completed_record = None
        complete_err = None

        def _concurrent_observation_writer() -> None:
            # Try to add a late observation for round 2
            late_obs = RoundObservation(
                event_id=f"obs_late_{uid}",
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                worker_id="BANK_A",
                bank_id="BANK_A",
                round_number=2,
                attempt_number=1,
                actual_sample_count=50,
                actual_executed_epochs=1,
                observed_loss=0.08,
                metrics={"loss": 0.08},
                execution_timestamp=datetime.now(timezone.utc).isoformat(),
                model_update_digest=hashlib.sha256(b"late").hexdigest(),
                execution_status="COMPLETED",
            )
            try:
                authority_worker.record_observation(late_obs)
            except TrainingProvenanceSecurityError as e:
                if "finalized completed job" in str(e):
                    obs_outcome["rejected"] = True

        def _completion_runner() -> None:
            nonlocal completed_record, complete_err
            try:
                completed_record = authority_comp.complete_job(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                    worker_id="FEDERATED_COORDINATOR",
                    pause_checkpoint_marker_file=marker_file,
                )
            except Exception as e:
                complete_err = e

        comp_thread = threading.Thread(target=_completion_runner)
        comp_thread.start()

        # 1. Wait until completion transaction is active and holding exclusive advisory lock
        for _ in range(50):
            if os.path.exists(marker_file):
                break
            time.sleep(0.1)
        assert os.path.exists(marker_file), "Completion did not reach in-transaction checkpoint!"

        # 2. Start concurrent worker observation write
        obs_thread = threading.Thread(target=_concurrent_observation_writer)
        obs_thread.start()

        # 3. Verify obs_thread is running and blocked waiting for shared advisory lock
        time.sleep(0.3)
        assert obs_thread.is_alive(), "Observation thread did not block on transaction advisory lock!"

        # 4. Release completion checkpoint to commit transaction
        release_file = marker_file + ".release"
        with open(release_file, "w", encoding="utf-8") as f:
            f.write("RELEASE")

        comp_thread.join(timeout=5)
        obs_thread.join(timeout=5)

        assert complete_err is None
        assert completed_record is not None
        assert completed_record.status == "COMPLETED"
        assert obs_outcome["rejected"] is True

        # Verify PostgreSQL state: completed record reflects exactly round 1
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT sample_count, executed_rounds FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 450
            assert row[1] == 1

            # Late observation was never committed
            cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE event_id = %s", (f"obs_late_{uid}",))
            row_obs = cur.fetchone()
            assert row_obs is not None
            assert row_obs[0] == 0

    def test_comp_r06_worker_terminated_inside_open_transaction(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R06: Prove terminating the completion worker process inside an open transaction rolls back cleanly.

        Required sequence:
        1. Begin valid finalization with in-transaction pause checkpoint.
        2. Confirm worker process has entered the transaction and written the checkpoint marker.
        3. Abruptly kill the worker process before commit.
        4. Inspect PostgreSQL state: zero completed rows persisted.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        vault_dir = str(tmp_path / "comp_vault_r06")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_crash_{uid}"
        run_id = f"run_crash_{uid}"
        sim_id = f"sim_crash_{uid}"
        art_sha = hashlib.sha256(b"crash_test").hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
        )

        marker_file = str(tmp_path / f"checkpoint_marker_{uid}.txt")

        client = IsolatedTrainingCompletionAuthority(
            service_endpoint=comp_endpoint,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )

        # 1. Start finalization with checkpoint pause in separate thread
        def _request_with_checkpoint() -> None:
            with pytest.raises(TrainingProvenanceSecurityError):
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                    pause_checkpoint_marker_file=marker_file,
                )

        t = threading.Thread(target=_request_with_checkpoint)
        t.start()

        # 2. Confirm worker reached in-transaction checkpoint
        for _ in range(50):
            if os.path.exists(marker_file):
                break
            time.sleep(0.1)
        assert os.path.exists(marker_file), "Worker did not reach in-transaction checkpoint!"

        # 3. Abruptly terminate worker process while transaction is OPEN
        comp_proc.kill()
        comp_proc.join(timeout=5)
        t.join(timeout=5)

        # 4. Inspect PostgreSQL: transaction rolled back cleanly, 0 rows in cfi_completed_jobs
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_comp_r07_post_crash_retry_preserves_correct_state(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R07: Prove post-crash retry preserves correct completion state without partial corruption.

        Verifies:
        - Restarting the service after a process crash.
        - Retrying the completion request succeeds cleanly.
        - Exactly 1 authoritative completed record is persisted in PostgreSQL.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        vault_dir = str(tmp_path / "comp_vault_r07")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)

        # Step 1: Simulate crash using COMP-R06 pattern
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_retry_{uid}"
        run_id = f"run_retry_{uid}"
        sim_id = f"sim_retry_{uid}"
        art_sha = hashlib.sha256(b"retry_test").hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
        )

        marker_file = str(tmp_path / f"checkpoint_marker_{uid}.txt")
        client = IsolatedTrainingCompletionAuthority(
            service_endpoint=comp_endpoint,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )

        def _request_crash() -> None:
            with pytest.raises(TrainingProvenanceSecurityError):
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                    pause_checkpoint_marker_file=marker_file,
                )

        t = threading.Thread(target=_request_crash)
        t.start()

        for _ in range(50):
            if os.path.exists(marker_file):
                break
            time.sleep(0.1)
        assert os.path.exists(marker_file)

        comp_proc.kill()
        comp_proc.join(timeout=5)
        t.join(timeout=5)

        # Step 2: Restart service with provisioned vault
        restarted_proc, new_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )
        try:
            restarted_client = IsolatedTrainingCompletionAuthority(
                service_endpoint=new_endpoint,
                coordinator_token=COORD_TOKEN,
                app_env="production",
            )

            # Step 3: Retry completion without pause
            record = restarted_client.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=450,
                observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                artifact_sha256=art_sha,
            )

            assert record.job_id == job_id
            assert record.status == "COMPLETED"

            # Exactly 1 row in PostgreSQL
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 1
        finally:
            restarted_proc.terminate()
            restarted_proc.join(timeout=3)

    def test_comp_r08_concurrent_independent_clients_resolve_safely(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R08: Prove concurrent independent clients resolve safely without queue sharing or mismatched responses.

        Verifies:
        - Two distinct clients each connect via separate IPC sockets.
        - Concurrently submit completion requests for the same job.
        - Neither client receives a mismatched or corrupted response.
        - PostgreSQL contains exactly 1 authoritative completed record.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        vault_dir = str(tmp_path / "comp_vault_r08")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )
        try:
            uid = uuid.uuid4().hex[:8]
            job_id = f"job_concurrent_{uid}"
            run_id = f"run_concurrent_{uid}"
            sim_id = f"sim_concurrent_{uid}"
            art_sha = hashlib.sha256(b"concurrent_test").hexdigest()

            helper_setup_valid_observed_job(
                admin_url=admin_url,
                job_id=job_id,
                run_id=run_id,
                sim_id=sim_id,
                artifact_sha=art_sha,
            )

            client_1 = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_TOKEN,
                app_env="production",
            )
            client_2 = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_TOKEN,
                app_env="production",
            )

            def _req_1():
                return client_1.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                )

            def _req_2():
                return client_2.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(_req_1)
                f2 = executor.submit(_req_2)
                r1 = f1.result(timeout=15)
                r2 = f2.result(timeout=15)

            assert r1.job_id == r2.job_id == job_id
            assert r1.artifact_sha256 == r2.artifact_sha256 == art_sha
            assert r1.status == r2.status == "COMPLETED"

            # Exactly 1 row persisted in PostgreSQL
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 1
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    def test_comp_r09_forged_completion_produces_no_trusted_receipt_or_registry_candidate(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R09: Prove uncompleted/forged jobs produce no trusted Ed25519 receipt or Registry candidate.

        Verifies:
        - Signing authority rejects issuance for uncompleted job.
        - ModelRegistry rejects admission without verified receipt (UNVERIFIED, ineligible).
        """
        issuer_url = pg_roles["issuer"]
        issuer_priv_hex = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()

        issuer_source = TrustedTrainingJobRecordSource(
            db_url=issuer_url,
            enforce_lifecycle=True,
            app_env="production",
            worker_token="tok_issuer_r09",
        )
        issuer = TrainingReceiptIssuer(
            signing_key_hex=issuer_priv_hex,
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=issuer_source,
            enforce_job_records=True,
            app_env="production",
        )
        verifier = get_default_training_verifier()
        registry = ModelRegistry(
            storage_dir=str(tmp_path / "model_reg_r09"),
            verifier=verifier,
        )

        uncompleted_job_id = f"job_uncomp_{uuid.uuid4().hex[:8]}"

        # Receipt signing fails closed
        with pytest.raises(TrainingProvenanceSecurityError, match="has no authenticated execution record"):
            issuer.issue_receipt(
                job_id=uncompleted_job_id,
                training_run_id="run_uncomp",
                simulation_id="sim_uncomp",
                model_artifact_sha256=hashlib.sha256(b"fake").hexdigest(),
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                metrics={"loss": 0.05},
            )

        # ModelRegistry marks candidate UNVERIFIED and ineligible
        version_entry = registry.save_version(
            simulation_id="sim_uncomp",
            state_dict={},
            metrics={"loss": 0.05},
            is_promoted=False,
            training_receipt=None,
        )
        assert version_entry["is_production_eligible"] is False
        assert version_entry["provenance_type"] == "UNVERIFIED"

    def test_comp_r10_genuine_three_bank_training_completes_through_independent_boundary(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """COMP-R10: Prove genuine three-bank federated training completes through independent completion authority.

        Verifies:
        - Full multi-process distributed training executes across BANK_A, BANK_B, BANK_C.
        - Coordinator holds coordinator credentials ONLY (zero completion DB credentials).
        - Completion finalized through independent service boundary over authenticated IPC.
        - Ed25519 receipt issued by isolated signing authority and admitted to ModelRegistry.
        """
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]
        issuer_url = pg_roles["issuer"]

        # 1. Privileged bootstrap provisions completion vault & spawns independent completion service
        comp_vault_dir = str(tmp_path / "comp_vault_r10")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        )
        try:
            # 2. Coordinator receives client with service_endpoint ONLY
            comp_auth = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_TOKEN,
                app_env="production",
            )
            manager = DistributedFederatedTrainingManager(
                coordinator_token=COORD_TOKEN,
                db_url=coord_url,
                app_env="production",
            )

            # 3. Provision bank partitions and spawn bank worker processes
            bank_enc_keys = {
                "BANK_A": Fernet.generate_key(),
                "BANK_B": Fernet.generate_key(),
                "BANK_C": Fernet.generate_key(),
            }
            vaults_dir = str(tmp_path / "bank_vaults")
            np.random.seed(42)
            bank_data_paths = {}
            for b_id, n_samples in [("BANK_A", 100), ("BANK_B", 150), ("BANK_C", 200)]:
                X_part = np.random.randn(n_samples, NUM_FEATURES).astype(np.float32)
                y_part = np.random.randint(0, 2, size=n_samples).astype(np.float32)
                p = setup_bank_private_storage(
                    bank_storage_dir=vaults_dir,
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
            )

            # 4. Provision signing authority
            dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
            issuer_vault_dir = str(tmp_path / "issuer_vault")
            issuer_key_file, _ = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)
            signing_auth = IsolatedReceiptSigningAuthority(
                issuer_key_path=issuer_key_file,
                coordinator_token=COORD_TOKEN,
                db_url=issuer_url,
                key_id=DEFAULT_TRAINING_KEY_ID,
                app_env="production",
            )

            uid = uuid.uuid4().hex[:8]
            job_id = f"job_e2e_{uid}"
            run_id = f"run_e2e_{uid}"
            sim_id = f"sim_e2e_{uid}"
            registry_dir = str(tmp_path / "model_reg_r10")

            X_test = np.random.randn(100, NUM_FEATURES).astype(np.float32)
            y_test = np.random.randint(0, 2, size=100).astype(np.float32)

            result = manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=(X_test, y_test),
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                worker_endpoints=endpoints,
                worker_processes=procs,
                num_rounds=2,
                expected_unique_samples=450,
                registry_storage_dir=registry_dir,
                signing_authority=signing_auth,
                completion_authority=comp_auth,
            )

            assert result["completed_record"].job_id == job_id
            assert result["completed_record"].status == "COMPLETED"
            assert result["receipt"].job_id == job_id
            assert result["saved_version"]["is_production_eligible"] is True
            assert result["saved_version"]["provenance_type"] == "TRAINING_RECORDED"
            assert result["saved_version"]["status"] == "inactive"
            assert result["saved_version"]["is_active"] is False

            signing_auth.terminate()
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)
