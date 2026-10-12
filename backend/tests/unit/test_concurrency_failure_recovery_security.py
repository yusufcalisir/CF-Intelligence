"""Workstream 6: Concurrency, Failure Injection & Recovery Security Verification Suite.

Exhaustively verifies CON-01 through CON-15 against real PostgreSQL 18.1 and multi-process boundaries:
- CON-01: Concurrent equivalent valid completions produce exactly one authoritative record with safe idempotency.
- CON-02: Conflicting completions cannot overwrite the first valid result.
- CON-03: Valid and invalid competing completions cannot result in an invalid trusted record.
- CON-04: Concurrent observation writes and finalization preserve a consistent execution snapshot.
- CON-05: Duplicate observations cannot inflate training accounting.
- CON-06: Completed execution records remain immutable against unauthorized UPDATE or DELETE.
- CON-07: Termination during an actual completion transaction cannot leave partially committed trusted evidence.
- CON-08: Restart and retry after ambiguous completion preserve idempotency and record integrity.
- CON-09: Coordinator termination during aggregation cannot publish an invalid completed model or receipt.
- CON-10: Bank worker failure cannot be silently counted as successful execution.
- CON-11: Receipt issuer unavailability cannot trigger an untrusted local-signing fallback.
- CON-12: PostgreSQL unavailability cannot produce trusted completion through an in-memory substitute.
- CON-13: Evaluation failure cannot produce authoritative successful evaluation evidence.
- CON-14: Final artifact substitution must be rejected during receipt or Registry verification.
- CON-15: Conflicting concurrent evidence mutations cannot bypass reconciliation or atomic finalization.
- POS-01: Genuine three-bank, two-round federated training completes through the independent boundary.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import os
import threading
import time
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import numpy as np
import psycopg
import pytest
from cryptography.fernet import Fernet

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedReceiptSigningAuthority,
    IsolatedTrainingCompletionAuthority,
    _serialize_model_state_dict,
    provision_isolated_completion_authority_vault,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
    spawn_isolated_completion_authority_service,
    spawn_isolated_coordinator_process,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    CompletedJobExecutionRecord,
    GlobalAggregationEvent,
    ModelEvaluationEvent,
    PostgresTrainingJobAuthority,
    RoundObservation,
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrustedTrainingJobRecordSource,
    get_default_training_verifier,
)
from app.config import get_settings
from app.infrastructure.database.training_authority_backends import (
    provision_runtime_service_roles,
)

COORD_SECRET = "coord_secret_concur_suite_token_9911"
BANK_TOKENS = {
    "BANK_A": "bank_a_token_concur_suite_01",
    "BANK_B": "bank_b_token_concur_suite_02",
    "BANK_C": "bank_c_token_concur_suite_03",
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


@pytest.fixture(scope="module")
def pg_roles(real_pg_instance: str) -> dict[str, str]:
    """Provision isolated PostgreSQL service roles for coordinator, completion, workers, issuer, and serving."""
    return provision_runtime_service_roles(real_pg_instance, app_env="production")


def helper_setup_observed_job(
    admin_url: str,
    job_id: str,
    run_id: str,
    sim_id: str,
    artifact_sha: str,
    sample_count: int = 300,
    num_rounds: int = 1,
    participating_banks: tuple[str, ...] = ("BANK_A", "BANK_B", "BANK_C"),
) -> None:
    """Set up an authoritative job with valid scheduled, started, observation, aggregation, and evaluation evidence."""
    auth = PostgresTrainingJobAuthority(db_url=admin_url)
    auth.schedule_job(
        job_id=job_id,
        training_run_id=run_id,
        simulation_id=sim_id,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        expected_rounds=num_rounds,
        expected_epochs=num_rounds,
        input_dim=len(FEATURE_NAMES),
        feature_names=list(FEATURE_NAMES),
        permitted_workers=participating_banks,
    )
    auth.start_job(job_id=job_id, worker_id=participating_banks[0])

    samples_per_bank = sample_count // len(participating_banks)
    now_str = datetime.now(UTC).isoformat()

    for r in range(1, num_rounds + 1):
        accepted_digests = []
        for b_id in participating_banks:
            up_digest = hashlib.sha256(f"update_{job_id}_{r}_{b_id}".encode()).hexdigest()
            accepted_digests.append(up_digest)
            obs = RoundObservation(
                event_id=f"evt_{job_id}_{r}_{b_id}",
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                worker_id=b_id,
                bank_id=b_id,
                round_number=r,
                attempt_number=1,
                actual_sample_count=samples_per_bank,
                actual_executed_epochs=1,
                observed_loss=0.25,
                metrics={"loss": 0.25},
                execution_timestamp=now_str,
                model_update_digest=up_digest,
                execution_status="COMPLETED",
            )
            auth.record_observation(obs)

        agg = GlobalAggregationEvent(
            event_id=f"agg_{job_id}_{r}",
            job_id=job_id,
            round_number=r,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=participating_banks,
            accepted_local_update_digests=tuple(accepted_digests),
            aggregation_configuration={"weights": "proportional"},
            aggregated_global_model_digest=artifact_sha,
            timestamp=now_str,
            aggregation_status="COMPLETED",
        )
        auth.record_global_aggregation(agg)

    auth.record_model_evaluation(
        ModelEvaluationEvent(
            event_id=f"eval_auc_{job_id}",
            job_id=job_id,
            evaluation_run_id=f"eval_run_{job_id}",
            metric_name="auc_roc",
            metric_value=0.92,
            evaluated_artifact_digest=artifact_sha,
            evaluator_identity="ISOLATED_EVALUATOR",
            evaluation_timestamp=now_str,
        )
    )
    auth.record_model_evaluation(
        ModelEvaluationEvent(
            event_id=f"eval_loss_{job_id}",
            job_id=job_id,
            evaluation_run_id=f"eval_run_{job_id}",
            metric_name="val_loss",
            metric_value=0.25,
            evaluated_artifact_digest=artifact_sha,
            evaluator_identity="ISOLATED_EVALUATOR",
            evaluation_timestamp=now_str,
        )
    )


class TestConcurrencyFailureRecoverySecurity:
    """Rigorous verification of Concurrency, Failure Injection, and Recovery Invariants."""

    # -------------------------------------------------------------------------
    # CON-01: Concurrent Equivalent Valid Completions (Safe Idempotency)
    # -------------------------------------------------------------------------
    def test_con_01_concurrent_equivalent_valid_completions_produce_one_record_idempotent(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-01: Concurrent equivalent valid completions produce exactly one authoritative record with safe idempotency."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con01_{uid}"
        run_id = f"run_con01_{uid}"
        sim_id = f"sim_con01_{uid}"
        art_sha = hashlib.sha256(b"con01_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        vault_dir = str(tmp_path / "comp_vault_con01")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client1 = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            client2 = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )

            def _complete(c: IsolatedTrainingCompletionAuthority) -> CompletedJobExecutionRecord:
                return c.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=300,
                    observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_SECRET,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(_complete, client1)
                f2 = executor.submit(_complete, client2)
                r1 = f1.result(timeout=15)
                r2 = f2.result(timeout=15)

            assert r1.job_id == r2.job_id == job_id
            assert r1.artifact_sha256 == r2.artifact_sha256 == art_sha
            assert r1.status == r2.status == "COMPLETED"

            # Exactly 1 completed row persisted in PostgreSQL
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None and row[0] == 1
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    # -------------------------------------------------------------------------
    # CON-02: Conflicting Completions Cannot Overwrite First Valid Result
    # -------------------------------------------------------------------------
    def test_con_02_conflicting_completions_cannot_overwrite_first_valid_result(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-02: Conflicting completions cannot overwrite the first valid result."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con02_{uid}"
        run_id = f"run_con02_{uid}"
        sim_id = f"sim_con02_{uid}"
        art_sha = hashlib.sha256(b"con02_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        vault_dir = str(tmp_path / "comp_vault_con02")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )

            # First valid completion succeeds
            first_rec = client.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=300,
                observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                artifact_sha256=art_sha,
                coordinator_token=COORD_SECRET,
            )
            assert first_rec.status == "COMPLETED"

            # Conflicting completion attempt (conflicting sample count or metrics)
            with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=500,  # Conflicting evidence
                    observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_SECRET,
                )
            assert "Conflicting completion record" in str(exc_info.value) or "cannot overwrite" in str(exc_info.value)

            # PostgreSQL state preserved exactly as original
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT sample_count FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 300
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    # -------------------------------------------------------------------------
    # CON-03: Valid and Invalid Competing Completions (No Invalid Trusted Record)
    # -------------------------------------------------------------------------
    def test_con_03_competing_valid_and_invalid_completions_no_invalid_trusted_record(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-03: Valid and invalid competing completions cannot result in an invalid trusted record."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con03_{uid}"
        run_id = f"run_con03_{uid}"
        sim_id = f"sim_con03_{uid}"
        art_sha = hashlib.sha256(b"con03_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        vault_dir = str(tmp_path / "comp_vault_con03")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            valid_client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            invalid_client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )

            def _valid():
                return valid_client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=300,
                    observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_SECRET,
                )

            def _invalid():
                # Invalid: unobserved artifact digest
                return invalid_client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=300,
                    observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                    artifact_sha256="0" * 64,
                    coordinator_token=COORD_SECRET,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f_valid = executor.submit(_valid)
                f_invalid = executor.submit(_invalid)

                valid_res = f_valid.result(timeout=15)
                with pytest.raises(TrainingProvenanceSecurityError):
                    f_invalid.result(timeout=15)

            assert valid_res.artifact_sha256 == art_sha

            # Single valid row in database, never an invalid record
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT artifact_sha256 FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == art_sha
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    # -------------------------------------------------------------------------
    # CON-04: Concurrent Observation Writes & Finalization (Consistent Snapshot)
    # -------------------------------------------------------------------------
    def test_con_04_concurrent_observation_writes_and_finalization_consistent_snapshot(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-04: Concurrent observation writes and finalization preserve a consistent execution snapshot."""
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]
        worker_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con04_{uid}"
        run_id = f"run_con04_{uid}"
        sim_id = f"sim_con04_{uid}"
        art_sha = hashlib.sha256(b"con04_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        authority_comp = PostgresTrainingJobAuthority(db_url=comp_url)
        authority_worker = PostgresTrainingJobAuthority(db_url=worker_url)

        marker_file = str(tmp_path / f"checkpoint_con04_{uid}.txt")
        obs_rejected: list[bool] = []

        def _late_worker():
            late_obs = RoundObservation(
                event_id=f"obs_late_con04_{uid}",
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
                execution_timestamp=datetime.now(UTC).isoformat(),
                model_update_digest=hashlib.sha256(b"late").hexdigest(),
                execution_status="COMPLETED",
            )
            try:
                authority_worker.record_observation(late_obs)
            except TrainingProvenanceSecurityError as e:
                if "finalized completed job" in str(e):
                    obs_rejected.append(True)

        def _completion():
            return authority_comp.complete_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=300,
                observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                pause_checkpoint_marker_file=marker_file,
            )

        t_comp = threading.Thread(target=_completion)
        t_comp.start()

        # Wait until completion holds advisory lock
        for _ in range(50):
            if os.path.exists(marker_file):
                break
            time.sleep(0.1)
        assert os.path.exists(marker_file)

        t_obs = threading.Thread(target=_late_worker)
        t_obs.start()

        time.sleep(0.3)
        assert t_obs.is_alive(), "Observation thread did not block on transaction advisory lock"

        # Release completion checkpoint
        with open(marker_file + ".release", "w", encoding="utf-8") as f:
            f.write("RELEASE")

        t_comp.join(timeout=5)
        t_obs.join(timeout=5)

        assert True in obs_rejected

        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT sample_count, executed_rounds FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 300
            assert row[1] == 1

            cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE event_id = %s", (f"obs_late_con04_{uid}",))
            late_row = cur.fetchone()
            assert late_row is not None and late_row[0] == 0

    # -------------------------------------------------------------------------
    # CON-05: Duplicate Observations Cannot Inflate Accounting
    # -------------------------------------------------------------------------
    def test_con_05_duplicate_observations_cannot_inflate_training_accounting(
        self, pg_roles: dict[str, str]
    ) -> None:
        """CON-05: Duplicate observations cannot inflate training accounting."""
        admin_url = pg_roles["admin"]
        worker_a_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con05_{uid}"
        run_id = f"run_con05_{uid}"
        sim_id = f"sim_con05_{uid}"

        admin_auth = PostgresTrainingJobAuthority(db_url=admin_url)
        admin_auth.schedule_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A"],
        )
        admin_auth.start_job(job_id=job_id, worker_id="BANK_A")

        worker_auth = PostgresTrainingJobAuthority(db_url=worker_a_url)
        obs = RoundObservation(
            event_id=f"evt_con05_{uid}",
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.30,
            metrics={"loss": 0.30},
            execution_timestamp=datetime.now(UTC).isoformat(),
            model_update_digest=hashlib.sha256(b"up_con05").hexdigest(),
            execution_status="COMPLETED",
        )

        # 1. First record succeeds
        res1 = worker_auth.record_observation(obs)
        assert res1.event_id == obs.event_id

        # 2. Duplicate record with identical payload returns idempotently without inserting rows
        res2 = worker_auth.record_observation(obs)
        assert res2.event_id == obs.event_id

        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE job_id = %s", (job_id,))
            obs_row = cur.fetchone()
            assert obs_row is not None and obs_row[0] == 1

        # 3. Duplicate record with conflicting payload is rejected
        conflicting_obs = RoundObservation(
            event_id=f"evt_con05_diff_{uid}",
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=200,  # Conflicting count
            actual_executed_epochs=1,
            observed_loss=0.30,
            metrics={"loss": 0.30},
            execution_timestamp=datetime.now(UTC).isoformat(),
            model_update_digest=hashlib.sha256(b"up_con05").hexdigest(),
            execution_status="COMPLETED",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Duplicate observation"):
            worker_auth.record_observation(conflicting_obs)

    # -------------------------------------------------------------------------
    # CON-06: Completed Execution Records Immutable (UPDATE / DELETE Denied)
    # -------------------------------------------------------------------------
    def test_con_06_completed_records_immutable_against_unauthorized_update_or_delete(
        self, pg_roles: dict[str, str]
    ) -> None:
        """CON-06: Completed execution records remain immutable against unauthorized UPDATE or DELETE."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]
        worker_url = pg_roles["worker_bank_a"]
        issuer_url = pg_roles["issuer"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con06_{uid}"
        run_id = f"run_con06_{uid}"
        sim_id = f"sim_con06_{uid}"
        art_sha = hashlib.sha256(b"con06_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        # Complete job as completion authority
        comp_auth = PostgresTrainingJobAuthority(db_url=comp_url)
        comp_auth.complete_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=300,
            observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
            artifact_sha256=art_sha,
            worker_id="FEDERATED_COORDINATOR",
        )

        for role_name, test_url in [
            ("completion", comp_url),
            ("coordinator", coord_url),
            ("worker", worker_url),
            ("issuer", issuer_url),
        ]:
            with psycopg.connect(test_url) as conn, conn.cursor() as cur:
                # Attempt UPDATE -> Must fail with SQLSTATE 42501 (insufficient_privilege)
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(
                        "UPDATE cfi_completed_jobs SET sample_count = 999 WHERE job_id = %s",
                        (job_id,),
                    )
                assert exc_info.value.sqlstate == "42501", f"Role '{role_name}' could UPDATE cfi_completed_jobs"
                conn.rollback()

                # Attempt DELETE -> Must fail with SQLSTATE 42501
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(
                        "DELETE FROM cfi_completed_jobs WHERE job_id = %s",
                        (job_id,),
                    )
                assert exc_info.value.sqlstate == "42501", f"Role '{role_name}' could DELETE cfi_completed_jobs"
                conn.rollback()

    # -------------------------------------------------------------------------
    # CON-07: Process Termination During Completion Transaction (Clean Rollback)
    # -------------------------------------------------------------------------
    def test_con_07_termination_during_completion_transaction_leaves_no_partial_evidence(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-07: Termination during an actual completion transaction cannot leave partially committed trusted evidence."""
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        vault_dir = str(tmp_path / "comp_vault_con07")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_SECRET,
            app_env="testing",
        )

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con07_{uid}"
        run_id = f"run_con07_{uid}"
        sim_id = f"sim_con07_{uid}"
        art_sha = hashlib.sha256(b"con07_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        marker_file = str(tmp_path / f"checkpoint_con07_{uid}.txt")
        client = IsolatedTrainingCompletionAuthority(
            service_endpoint=comp_endpoint,
            coordinator_token=COORD_SECRET,
            app_env="testing",
        )

        def _request_crash():
            with pytest.raises(TrainingProvenanceSecurityError):
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=300,
                    observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                    artifact_sha256=art_sha,
                    pause_checkpoint_marker_file=marker_file,
                )

        t = threading.Thread(target=_request_crash)
        t.start()

        # Wait until transaction is open at checkpoint
        for _ in range(50):
            if os.path.exists(marker_file):
                break
            time.sleep(0.1)
        assert os.path.exists(marker_file)

        # Abruptly kill the worker process while transaction is OPEN
        comp_proc.kill()
        comp_proc.join(timeout=5)
        t.join(timeout=5)

        # Inspect PostgreSQL: 0 rows committed
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            zero_row = cur.fetchone()
            assert zero_row is not None and zero_row[0] == 0

    # -------------------------------------------------------------------------
    # CON-08: Restart and Retry After Ambiguous Completion (Idempotency)
    # -------------------------------------------------------------------------
    def test_con_08_restart_and_retry_after_ambiguous_completion_preserves_idempotency(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-08: Restart and retry after ambiguous completion preserve idempotency and record integrity."""
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        vault_dir = str(tmp_path / "comp_vault_con08")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con08_{uid}"
        run_id = f"run_con08_{uid}"
        sim_id = f"sim_con08_{uid}"
        art_sha = hashlib.sha256(b"con08_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        # 1. Spawn completion authority service in testing mode (allows checkpoint marker file)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_SECRET,
            app_env="testing",
        )
        try:
            client1 = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="testing",
            )

            marker_file = str(tmp_path / f"post_commit_marker_con08_{uid}.txt")
            client_error: list[Exception] = []

            def _call_service():
                try:
                    client1.finalize_training_completion(
                        job_id=job_id,
                        executed_rounds=1,
                        executed_epochs=1,
                        sample_count=300,
                        observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                        artifact_sha256=art_sha,
                        coordinator_token=COORD_SECRET,
                        pause_after_commit_marker_file=marker_file,
                    )
                except Exception as exc:
                    client_error.append(exc)

            t = threading.Thread(target=_call_service)
            t.start()

            # Wait until PostgreSQL transaction has committed and service pauses at the post-commit checkpoint
            for _ in range(60):
                if os.path.exists(marker_file):
                    break
                time.sleep(0.1)
            assert os.path.exists(marker_file), "Service did not reach post-commit checkpoint"

            # 2. Verify the actual commit checkpoint in PostgreSQL while service is paused before returning ACK
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT completed_at, artifact_sha256, sample_count FROM cfi_completed_jobs WHERE job_id = %s",
                    (job_id,),
                )
                commit_row = cur.fetchone()
                assert commit_row is not None, "PostgreSQL row was not committed before acknowledgment loss"
                first_completed_at, first_sha, first_samples = commit_row
                assert first_sha == art_sha
                assert first_samples == 300

            # 3. Interrupt communication / terminate the service abruptly after commit, before client receives ACK
            comp_proc.kill()
            comp_proc.join(timeout=5)
            t.join(timeout=5)

            # Client must have experienced communication failure
            assert len(client_error) == 1, "Client did not observe network/service interruption"
            assert isinstance(client_error[0], TrainingProvenanceSecurityError)

        finally:
            if comp_proc.is_alive():
                comp_proc.terminate()
                comp_proc.join(timeout=3)

        # 4. Restart the service under production configuration
        restarted_proc, new_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client2 = IsolatedTrainingCompletionAuthority(
                service_endpoint=new_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )

            # 5. Retry completion request through the normal production interface
            retry_rec = client2.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=300,
                observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                artifact_sha256=art_sha,
                coordinator_token=COORD_SECRET,
            )

            # 6. Prove exactly one unchanged authoritative record exists
            assert retry_rec.job_id == job_id
            assert retry_rec.artifact_sha256 == art_sha
            assert retry_rec.completed_at == first_completed_at

            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                one_row = cur.fetchone()
                assert one_row is not None and one_row[0] == 1
        finally:
            restarted_proc.terminate()
            restarted_proc.join(timeout=3)

    # -------------------------------------------------------------------------
    # CON-09: Coordinator Termination During Aggregation
    # -------------------------------------------------------------------------
    def test_con_09_coordinator_termination_during_aggregation_leaves_no_trusted_record_or_receipt(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-09: Coordinator termination during aggregation cannot publish an invalid completed model or receipt."""
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]
        admin_url = pg_roles["admin"]
        issuer_url = pg_roles["issuer"]

        comp_vault_dir = str(tmp_path / "comp_vault_con09")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="testing")
        ident = coord_client.get_process_identity()
        if os.name == "nt":
            assert ident.details.get("is_restricted") is True, "Coordinator must run under genuine Windows restricted token"
        procs = {}
        try:
            bank_enc_keys = {
                "BANK_A": Fernet.generate_key(),
                "BANK_B": Fernet.generate_key(),
                "BANK_C": Fernet.generate_key(),
            }
            vaults_dir = str(tmp_path / "bank_vaults_con09")
            np.random.seed(99)
            bank_data_paths = {}
            for b_id, n_samples in [("BANK_A", 100), ("BANK_B", 100), ("BANK_C", 100)]:
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

            dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_con09").digest().hex()
            issuer_vault_dir = str(tmp_path / "issuer_vault_con09")
            issuer_key_file, _ = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)
            signing_auth = IsolatedReceiptSigningAuthority(
                issuer_key_path=issuer_key_file,
                coordinator_token=COORD_SECRET,
                db_url=issuer_url,
                key_id=DEFAULT_TRAINING_KEY_ID,
                app_env="production",
            )

            uid = uuid.uuid4().hex[:8]
            job_id = f"job_con09_{uid}"
            run_id = f"run_con09_{uid}"
            sim_id = f"sim_con09_{uid}"
            registry_dir = str(tmp_path / "model_reg_con09")
            agg_checkpoint_file = str(tmp_path / f"agg_checkpoint_con09_{uid}.txt")

            X_test = np.random.randn(50, NUM_FEATURES).astype(np.float32)
            y_test = np.random.randint(0, 2, size=50).astype(np.float32)

            worker_pids: dict[str, int] = {b: (p.pid or 0) for b, p in procs.items()}

            fed_errors: list[Exception] = []

            def _run_fed():
                try:
                    coord_client.execute_federation(
                        job_id=job_id,
                        training_run_id=run_id,
                        simulation_id=sim_id,
                        holdout_val_data=(X_test, y_test),
                        participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                        worker_endpoints=endpoints,
                        num_rounds=2,
                        expected_unique_samples=300,
                        registry_storage_dir=registry_dir,
                        coordinator_token=COORD_SECRET,
                        db_url=coord_url,
                        comp_service_endpoint=comp_endpoint,
                        signing_authority=signing_auth,
                        worker_pids=worker_pids,
                        pause_aggregation_checkpoint_file=agg_checkpoint_file,
                    )
                except Exception as exc:
                    fed_errors.append(exc)

            t_fed = threading.Thread(target=_run_fed)
            t_fed.start()

            # Wait until coordinator executes FedAvg aggregation in memory and reaches the exact aggregation checkpoint
            # before the global aggregation event is committed to PostgreSQL
            reached_checkpoint = False
            checkpoint_payload = ""
            for _ in range(120):
                if os.path.exists(agg_checkpoint_file):
                    try:
                        with open(agg_checkpoint_file, encoding="utf-8") as f:
                            checkpoint_payload = f.read().strip()
                        if checkpoint_payload.startswith("AGGREGATION_EXECUTED_ROUND_1:"):
                            reached_checkpoint = True
                            break
                    except Exception:
                        pass
                time.sleep(0.1)

            assert reached_checkpoint, (
                f"Coordinator did not reach deterministic aggregation checkpoint (content: {checkpoint_payload})"
            )
            computed_agg_digest = checkpoint_payload.split(":", 1)[1]
            assert len(computed_agg_digest) == 64, f"Invalid model digest in checkpoint: {computed_agg_digest}"

            # Terminate the coordinator process abruptly during aggregation execution
            if os.name == "nt" and coord_proc is not None:
                import win32process
                with contextlib.suppress(Exception):
                    win32process.TerminateProcess(coord_proc, 9)
            coord_client.terminate()
            t_fed.join(timeout=5)

            # Inspect authoritative PostgreSQL records: 0 completed jobs, 0 global aggregations, 1 started job, 3 round observations
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                zero_comp = cur.fetchone()
                assert zero_comp is not None and zero_comp[0] == 0

                cur.execute("SELECT COUNT(*) FROM cfi_global_aggregations WHERE job_id = %s", (job_id,))
                zero_agg = cur.fetchone()
                assert zero_agg is not None and zero_agg[0] == 0

                cur.execute("SELECT COUNT(*) FROM cfi_started_jobs WHERE job_id = %s", (job_id,))
                started_row = cur.fetchone()
                assert started_row is not None and started_row[0] == 1

                cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE job_id = %s", (job_id,))
                obs_row = cur.fetchone()
                assert obs_row is not None and obs_row[0] == 3

            # Prove no trusted receipt can be issued
            issuer_source = TrustedTrainingJobRecordSource(
                db_url=issuer_url,
                enforce_lifecycle=True,
                coordinator_token=COORD_SECRET,
                bank_tokens=BANK_TOKENS,
                app_env="production",
            )
            issuer = TrainingReceiptIssuer(
                signing_key_hex=dev_seed,
                key_id=DEFAULT_TRAINING_KEY_ID,
                job_record_source=issuer_source,
                enforce_job_records=True,
                app_env="production",
            )
            model_payload = b"incomplete_aggregated_model_bytes_con09"
            evidence = TrainingExecutionEvidence(
                training_run_id=run_id,
                job_id=job_id,
                simulation_id=sim_id,
                completed_at=datetime.now(UTC).isoformat(),
                status="COMPLETED",
                executed_rounds=2,
                executed_epochs=2,
                sample_count=300,
                observed_metrics={"loss": 0.25},
                artifact_sha256=hashlib.sha256(model_payload).hexdigest(),
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                feature_names=list(FEATURE_NAMES),
                producer_id="FEDERATED_COORDINATOR",
            )
            with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
                issuer.issue_receipt(evidence=evidence, model_bytes=model_payload)
            assert "Cannot issue receipt" in str(exc_info.value) or "not found" in str(exc_info.value).lower()

            # Prove no verified Registry candidate can result
            verifier = get_default_training_verifier()
            registry = ModelRegistry(
                storage_dir=registry_dir,
                verifier=verifier,
            )
            model_svc = ModelService(get_settings())
            unverified_model = model_svc.create_model(input_dim=NUM_FEATURES)
            version_entry = registry.save_version(
                simulation_id=sim_id,
                state_dict=unverified_model.state_dict(),
                metrics={"loss": 0.25},
                is_promoted=False,
                training_receipt=None,
            )
            assert version_entry["is_production_eligible"] is False
            assert version_entry["provenance_type"] == "UNVERIFIED"

            # Prove ISO-07: Production coordinator strictly rejects test aggregation checkpoint hooks
            prod_proc, prod_client = spawn_isolated_coordinator_process(app_env="production")
            try:
                with pytest.raises(TrainingProvenanceSecurityError, match="ISO-07"):
                    prod_client.execute_federation(
                        job_id=f"job_iso07_{uid}",
                        training_run_id=f"run_iso07_{uid}",
                        simulation_id=f"sim_iso07_{uid}",
                        holdout_val_data=(X_test, y_test),
                        participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                        worker_endpoints=endpoints,
                        num_rounds=1,
                        expected_unique_samples=300,
                        registry_storage_dir=registry_dir,
                        coordinator_token=COORD_SECRET,
                        db_url=coord_url,
                        comp_service_endpoint=comp_endpoint,
                        signing_authority=signing_auth,
                        pause_aggregation_checkpoint_file=agg_checkpoint_file,
                    )
            finally:
                prod_client.terminate()

        finally:
            for p in procs.values():
                with contextlib.suppress(Exception):
                    p.terminate()
                    p.join(timeout=2)
            coord_client.terminate()
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    # -------------------------------------------------------------------------
    # CON-10: Bank Worker Failure Cannot Be Silently Counted
    # -------------------------------------------------------------------------
    def test_con_10_bank_worker_failure_cannot_be_silently_counted_as_successful(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-10: Bank worker failure cannot be silently counted as successful execution."""
        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con10_{uid}"
        run_id = f"run_con10_{uid}"
        sim_id = f"sim_con10_{uid}"

        np.random.seed(110)
        bank_partitions = {
            "BANK_A": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
            "BANK_B": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
            "BANK_C": (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32)),
        }
        holdout_val = (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32))

        bank_db_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }
        procs, endpoints = spawn_bank_workers(
            participating_banks=["BANK_A", "BANK_B", "BANK_C"],
            bank_tokens=BANK_TOKENS,
            db_url=pg_roles["worker"],
            bank_db_urls=bank_db_urls,
            bank_partitions=bank_partitions,
            fail_bank_b_observation=True,
        )

        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["coordinator"],
            app_env="testing",
        )

        with pytest.raises(TrainingProvenanceSecurityError) as exc_info:
            manager.execute_distributed_federation(
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                holdout_val_data=holdout_val,
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                worker_endpoints=endpoints,
                worker_processes=procs,
                num_rounds=1,
                fail_bank_b_observation=True,
                registry_storage_dir=str(tmp_path / "registry"),
            )
        assert (
            "Sample count mismatch" in str(exc_info.value)
            or "Required participant" in str(exc_info.value)
            or "observation" in str(exc_info.value).lower()
        )

    # -------------------------------------------------------------------------
    # CON-11: Receipt Issuer Unavailability (No Local Signing Fallback)
    # -------------------------------------------------------------------------
    def test_con_11_receipt_issuer_unavailability_cannot_trigger_untrusted_local_signing_fallback(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-11: Receipt issuer unavailability cannot trigger an untrusted local-signing fallback."""
        manager = DistributedFederatedTrainingManager(
            coordinator_token=COORD_SECRET,
            db_url=pg_roles["coordinator"],
            app_env="testing",
        )
        manager.app_env = "production"

        # When signing_authority is None in production, coordinator cannot self-sign receipts
        np.random.seed(111)
        holdout_val = (np.random.randn(50, NUM_FEATURES).astype(np.float32), np.random.randint(0, 2, 50).astype(np.float32))

        # Attempting federation without isolated signing authority in production must fail closed
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="requires an isolated receipt signing authority|restricted operating system security token",
        ):
            manager.execute_distributed_federation(
                job_id="job_con11",
                training_run_id="run_con11",
                simulation_id="sim_con11",
                holdout_val_data=holdout_val,
                participating_banks=["BANK_A"],
                worker_endpoints={},
                num_rounds=1,
                signing_authority=None,
            )

        # Isolated signing authority fails closed when authority database/service is unavailable
        with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to connect to PostgreSQL authority"):
            IsolatedReceiptSigningAuthority(
                signing_key_hex=hashlib.sha256(b"con11_seed").digest().hex(),
                db_url="postgresql://invalid:invalid@127.0.0.1:59999/cfi_dev",
                app_env="testing",
            )

    # -------------------------------------------------------------------------
    # CON-12: PostgreSQL Unavailability (Fails Closed Without In-Memory)
    # -------------------------------------------------------------------------
    def test_con_12_postgresql_unavailability_fails_closed_no_in_memory_substitute(self) -> None:
        """CON-12: PostgreSQL unavailability cannot produce trusted completion through an in-memory substitute."""
        unreachable_url = "postgresql://postgres:postgres@127.0.0.1:54399/nonexistent"
        with pytest.raises(Exception) as exc_info:
            auth = TrustedTrainingJobRecordSource(
                coordinator_token=COORD_SECRET,
                db_url=unreachable_url,
                app_env="production",
            )
            auth.schedule_training_job(
                job_id="job_con12",
                training_run_id="run_con12",
                simulation_id="sim_con12",
                coordinator_token=COORD_SECRET,
            )
        assert "connection" in str(exc_info.value).lower() or "database" in str(exc_info.value).lower()

    # -------------------------------------------------------------------------
    # CON-13: Evaluation Failure Cannot Produce Authoritative Evidence
    # -------------------------------------------------------------------------
    def test_con_13_evaluation_failure_cannot_produce_authoritative_evaluation_evidence(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-13: Evaluation failure cannot produce authoritative successful evaluation evidence."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con13_{uid}"
        run_id = f"run_con13_{uid}"
        sim_id = f"sim_con13_{uid}"
        art_sha = hashlib.sha256(b"con13_art").hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300)

        vault_dir = str(tmp_path / "comp_vault_con13")
        vault_file = provision_isolated_completion_authority_vault(vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )

            # Corrupt artifact bytes (not a valid PyTorch model state dict)
            corrupt_bytes = b"not_a_valid_pytorch_model_bytes"
            np.random.seed(113)
            X_val = np.random.randn(50, NUM_FEATURES).astype(np.float32)
            y_val = np.random.randint(0, 2, size=50).astype(np.float32)

            with pytest.raises(TrainingProvenanceSecurityError, match="Evaluation forgery|execution failed"):
                client.evaluate_and_record_artifact(
                    job_id=job_id,
                    artifact_bytes=corrupt_bytes,
                    holdout_features=X_val,
                    holdout_labels=y_val,
                    coordinator_token=COORD_SECRET,
                )
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    # -------------------------------------------------------------------------
    # CON-14: Final Artifact Substitution Rejected During Receipt and Registry
    # -------------------------------------------------------------------------
    def test_con_14_final_artifact_substitution_rejected_during_receipt_and_registry(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-14: Final artifact substitution must be rejected during receipt or Registry verification."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]
        issuer_url = pg_roles["issuer"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con14_{uid}"
        run_id = f"run_con14_{uid}"
        sim_id = f"sim_con14_{uid}"

        model_svc = ModelService(get_settings())
        real_model = model_svc.create_model(input_dim=NUM_FEATURES)
        real_bytes = _serialize_model_state_dict(real_model)
        real_sha = hashlib.sha256(real_bytes).hexdigest()

        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, real_sha, sample_count=300)

        # Complete job with real artifact SHA
        comp_auth = PostgresTrainingJobAuthority(db_url=comp_url)
        completed_rec = comp_auth.complete_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=300,
            observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
            artifact_sha256=real_sha,
            worker_id="FEDERATED_COORDINATOR",
        )

        # Substituted model bytes
        fake_model = model_svc.create_model(input_dim=NUM_FEATURES)
        fake_bytes = _serialize_model_state_dict(fake_model)
        assert fake_bytes != real_bytes

        issuer_source = TrustedTrainingJobRecordSource(
            db_url=issuer_url,
            enforce_lifecycle=True,
            coordinator_token="coord_tok_con14",
            bank_tokens={"BANK_A": "bank_a_tok_con14"},
            app_env="testing",
        )
        issuer = TrainingReceiptIssuer(
            signing_key_hex=hashlib.sha256(b"dev_seed_con14").digest().hex(),
            key_id=DEFAULT_TRAINING_KEY_ID,
            job_record_source=issuer_source,
            enforce_job_records=True,
            app_env="testing",
        )

        evidence = TrainingExecutionEvidence(
            training_run_id=run_id,
            job_id=job_id,
            simulation_id=sim_id,
            completed_at=completed_rec.completed_at,
            status="COMPLETED",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=300,
            observed_metrics=dict(completed_rec.observed_metrics),
            artifact_sha256=real_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )

        # Issuer rejects issuance when model bytes mismatch evidence SHA-256
        with pytest.raises(ValueError, match="does not match evidence artifact_sha256"):
            issuer.issue_receipt(evidence=evidence, model_bytes=fake_bytes)

        # ModelRegistry candidate admission with substituted artifact bytes is UNVERIFIED & ineligible
        verifier = get_default_training_verifier()
        registry = ModelRegistry(
            storage_dir=str(tmp_path / "model_reg_con14"),
            verifier=verifier,
        )
        version_entry = registry.save_version(
            simulation_id=sim_id,
            state_dict=fake_model.state_dict(),
            metrics={"loss": 0.25},
            is_promoted=False,
            training_receipt=None,
        )
        assert version_entry["is_production_eligible"] is False
        assert version_entry["provenance_type"] == "UNVERIFIED"

    # -------------------------------------------------------------------------
    # CON-15: Conflicting Concurrent Evidence Mutations Cannot Bypass Reconciliation
    # -------------------------------------------------------------------------
    def test_con_15_conflicting_concurrent_evidence_mutations_cannot_bypass_reconciliation(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """CON-15: Conflicting concurrent evidence mutations cannot bypass reconciliation or atomic finalization."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]
        worker_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_con15_{uid}"
        run_id = f"run_con15_{uid}"
        sim_id = f"sim_con15_{uid}"
        art_sha = hashlib.sha256(b"con15_art").hexdigest()

        # Setup an observed job with round 1 observations and aggregation completed
        helper_setup_observed_job(admin_url, job_id, run_id, sim_id, art_sha, sample_count=300, num_rounds=1)

        # 1. Establish counterfactual: Prove that in the absence of finalization, an otherwise valid
        # round 2 observation or aggregation succeeds without error
        twin_job_id = f"job_twin_con15_{uid}"
        auth_admin = PostgresTrainingJobAuthority(db_url=admin_url)
        auth_admin.schedule_job(
            job_id=twin_job_id,
            training_run_id=f"run_twin_{uid}",
            simulation_id=f"sim_twin_{uid}",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=2,
            expected_epochs=2,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        auth_admin.start_job(job_id=twin_job_id, worker_id="BANK_A")

        worker_auth_twin = PostgresTrainingJobAuthority(db_url=worker_url)
        twin_obs = RoundObservation(
            event_id=f"evt_twin_{uid}",
            job_id=twin_job_id,
            training_run_id=f"run_twin_{uid}",
            simulation_id=f"sim_twin_{uid}",
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=2,
            attempt_number=1,
            actual_sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.18,
            metrics={"loss": 0.18},
            execution_timestamp=datetime.now(UTC).isoformat(),
            model_update_digest=hashlib.sha256(b"competing_twin_up").hexdigest(),
            execution_status="COMPLETED",
        )
        recorded_twin = worker_auth_twin.record_observation(twin_obs)
        assert recorded_twin.event_id == twin_obs.event_id, "Counterfactual failed: valid mutation was rejected"

        # 2. Establish a real race using independent PostgreSQL connections on the target job
        authority_comp = PostgresTrainingJobAuthority(db_url=comp_url)
        authority_worker = PostgresTrainingJobAuthority(db_url=worker_url)

        marker_file = str(tmp_path / f"checkpoint_con15_{uid}.txt")

        # Competing mutation A: Valid round 2 observation from BANK_A
        competing_obs = RoundObservation(
            event_id=f"evt_competing_con15_{uid}",
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=2,
            attempt_number=1,
            actual_sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.18,
            metrics={"loss": 0.18},
            execution_timestamp=datetime.now(UTC).isoformat(),
            model_update_digest=hashlib.sha256(b"competing_update_15").hexdigest(),
            execution_status="COMPLETED",
        )

        obs_rejected: list[bool] = []
        obs_error: list[Exception] = []

        def _competing_worker():
            try:
                authority_worker.record_observation(competing_obs)
            except TrainingProvenanceSecurityError as e:
                obs_error.append(e)
                if "finalized completed job" in str(e):
                    obs_rejected.append(True)

        # Thread 1: Finalization holding exclusive transaction advisory lock pre-commit
        def _finalization():
            return authority_comp.complete_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=300,
                observed_metrics={"loss": 0.25, "auc_roc": 0.92, "val_loss": 0.25},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                pause_checkpoint_marker_file=marker_file,
            )

        t_comp = threading.Thread(target=_finalization)
        t_comp.start()

        # Wait until finalization holds exclusive advisory lock at the checkpoint
        for _ in range(60):
            if os.path.exists(marker_file):
                break
            time.sleep(0.1)
        assert os.path.exists(marker_file), "Finalization did not reach in-transaction checkpoint"

        # Thread 2: Competing evidence mutation launched while finalization holds exclusive advisory lock
        t_worker = threading.Thread(target=_competing_worker)
        t_worker.start()

        time.sleep(0.3)
        assert t_worker.is_alive(), "Worker mutation did not block on transaction advisory lock"

        # 3. Verify intended transaction/advisory-lock ordering directly via PostgreSQL pg_locks
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT mode, granted FROM pg_locks
                WHERE locktype = 'advisory' AND objid = hashtext(%s)
                ORDER BY granted DESC
                """,
                (job_id,),
            )
            locks = cur.fetchall()
            assert len(locks) >= 2, f"Expected active and waiting advisory locks, found: {locks}"
            granted_modes = [lock_entry[0] for lock_entry in locks if lock_entry[1] is True]
            waiting_modes = [lock_entry[0] for lock_entry in locks if lock_entry[1] is False]
            assert "ExclusiveLock" in granted_modes, f"Finalization exclusive lock not held: {granted_modes}"
            assert "ShareLock" in waiting_modes, f"Worker shared lock not waiting: {waiting_modes}"

        # 4. Release finalization checkpoint so transaction commits
        with open(marker_file + ".release", "w", encoding="utf-8") as f:
            f.write("RELEASE")

        t_comp.join(timeout=5)
        t_worker.join(timeout=5)

        # 5. Worker mutation unblocked and was rejected upon seeing finalized job
        assert len(obs_rejected) == 1, f"Competing mutation was not rejected: errors={obs_error}"

        # 6. Verify persistent database state: no inconsistent trusted completion
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            # Completed job record remains exactly the snapshot finalized by completion authority
            cur.execute(
                "SELECT sample_count, artifact_sha256, executed_rounds FROM cfi_completed_jobs WHERE job_id = %s",
                (job_id,),
            )
            comp_row = cur.fetchone()
            assert comp_row is not None
            assert comp_row[0] == 300
            assert comp_row[1] == art_sha
            assert comp_row[2] == 1

            # Competing round 2 observation was never committed
            cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE event_id = %s", (competing_obs.event_id,))
            zero_competing = cur.fetchone()
            assert zero_competing is not None and zero_competing[0] == 0

    # -------------------------------------------------------------------------
    # POS-01: Mandatory Positive Regression (Genuine 3-Bank 2-Round Training)
    # -------------------------------------------------------------------------
    def test_pos_01_genuine_three_bank_two_round_training_succeeds(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """POS-01: Verify genuine three-bank, two-round PyTorch federated training completes through the independent boundary."""
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]
        issuer_url = pg_roles["issuer"]

        comp_vault_dir = str(tmp_path / "comp_vault_pos01")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        coord_proc, coord_client = spawn_isolated_coordinator_process(app_env="production")
        try:
            bank_enc_keys = {
                "BANK_A": Fernet.generate_key(),
                "BANK_B": Fernet.generate_key(),
                "BANK_C": Fernet.generate_key(),
            }
            vaults_dir = str(tmp_path / "bank_vaults_pos01")
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

            dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
            issuer_vault_dir = str(tmp_path / "issuer_vault_pos01")
            issuer_key_file, _ = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)
            signing_auth = IsolatedReceiptSigningAuthority(
                issuer_key_path=issuer_key_file,
                coordinator_token=COORD_SECRET,
                db_url=issuer_url,
                key_id=DEFAULT_TRAINING_KEY_ID,
                app_env="production",
            )

            uid = uuid.uuid4().hex[:8]
            job_id = f"job_pos01_{uid}"
            run_id = f"run_pos01_{uid}"
            sim_id = f"sim_pos01_{uid}"
            registry_dir = str(tmp_path / "model_reg_pos01")

            X_test = np.random.randn(100, NUM_FEATURES).astype(np.float32)
            y_test = np.random.randint(0, 2, size=100).astype(np.float32)

            worker_pids: dict[str, int] = {b: (p.pid or 0) for b, p in procs.items()}
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
            assert result["receipt"].job_id == job_id
            assert result["saved_version"]["is_production_eligible"] is True
            assert result["saved_version"]["provenance_type"] == "TRAINING_RECORDED"
            assert result["saved_version"]["status"] == "inactive"
            assert result["saved_version"]["is_active"] is False

            signing_auth.terminate()
        finally:
            with contextlib.suppress(Exception):
                coord_client.terminate()
            comp_proc.terminate()
            comp_proc.join(timeout=3)
