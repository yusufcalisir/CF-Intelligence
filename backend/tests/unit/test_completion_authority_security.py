"""Executable Security Test Suite for Independent, Atomic Training Completion Authority (COMP-01 through COMP-10).

Verifies the following security invariants against a real PostgreSQL 18.1 instance:
- COMP-01: Coordinator credential isolation (coordinator never holds completion credentials, URLs, or authority).
- COMP-02: Direct SQL completion forgery (cfi_coord_role denied direct INSERT on cfi_completed_jobs with SQLSTATE 42501).
- COMP-03: Privileged connection bypass (cannot SET ROLE or invoke privileged completion under coordinator security context).
- COMP-04: Forged finalization request (independent authority rejects finalization without required observations/aggregations).
- COMP-05: Genuine finalization (valid observed training lifecycle finalizes through independent authority without coordinator credentials).
- COMP-06: Atomic validation/finalization (mismatched/tampered evidence rolls back completely; zero partial state).
- COMP-07: Concurrent equivalent completion (serialized via PostgreSQL advisory xact lock; exactly 1 authoritative completed row).
- COMP-08: Conflicting completion (incompatible completion rejected after finalization; persisted evidence unchanged).
- COMP-09: Process/database failure (failure at execution checkpoint rolls back cleanly; terminated worker fails closed).
- COMP-10: Downstream provenance (uncompleted/forged job cannot be signed or admitted; genuine job signs and admits as inactive candidate).
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any
import uuid

import psycopg
import psycopg.conninfo
import pytest

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    DistributedFederatedTrainingManager,
    IsolatedTrainingCompletionAuthority,
    _generate_ed25519_keypair_hex,
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
        agg_digest = artifact_sha if r == num_rounds else hashlib.sha256(f"global_{job_id}_r{r}".encode()).hexdigest()
        admin_auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"agg_{job_id}_r{r}",
                job_id=job_id,
                round_number=r,
                aggregation_algorithm="FedAvg",
                accepted_participant_ids=tuple(participating_banks),
                accepted_local_update_digests=tuple(digests),
                aggregation_configuration={"algorithm": "FedAvg"},
                aggregated_global_model_digest=agg_digest,
                timestamp=now_str,
                aggregation_status="ACCEPTED",
            )
        )

    # 5. Record model evaluation
    admin_auth.record_model_evaluation(
        ModelEvaluationEvent(
            event_id=f"eval_{job_id}_auc",
            job_id=job_id,
            evaluation_run_id=f"eval_run_{job_id}",
            metric_name="auc_roc",
            metric_value=auc_roc,
            evaluated_artifact_digest=artifact_sha,
            evaluator_identity="EVALUATOR",
            evaluation_timestamp=now_str,
            evaluation_dataset_manifest=f"manifest_{job_id}",
        )
    )

    return {
        "job_id": job_id,
        "run_id": run_id,
        "sim_id": sim_id,
        "num_rounds": num_rounds,
        "total_samples": total_samples,
        "observed_loss": observed_loss,
        "auc_roc": auc_roc,
        "artifact_sha": artifact_sha,
        "fnames": fnames,
    }


class TestCompletionAuthoritySecurity:
    """Mandatory executable security test suite COMP-01 through COMP-10."""

    def test_comp_01_coordinator_credential_isolation(self, pg_roles: dict[str, str]) -> None:
        """COMP-01: Prove coordinator has zero access to completion-authority credentials.

        Verifies:
        - Passing completion_db_url to coordinator raises TrainingProvenanceSecurityError.
        - Coordinator holds only coordinator role URL (cfi_coord_role).
        - Coordinator internal authority source points to cfi_coord_role.
        - Process environment has no CFI_TRAINING_JOB_COMPLETION_DB_URL.
        - Neither coordinator attributes nor service objects hold completion credentials.
        """
        coord_url = pg_roles["coordinator"]
        comp_url = pg_roles["completion"]

        # 1. Constructor rejection when caller attempts to supply completion credentials
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator process cannot receive or hold completion-authority credentials"):
            DistributedFederatedTrainingManager(
                coordinator_token=COORD_TOKEN,
                db_url=coord_url,
                app_env="production",
                completion_db_url=comp_url,
            )

        # 2. Coordinator instantiated legitimately with its own role only
        coordinator = DistributedFederatedTrainingManager(
            coordinator_token=COORD_TOKEN,
            db_url=coord_url,
            app_env="production",
        )

        assert coordinator.db_url == coord_url
        assert "cfi_coord_role" in coordinator.db_url
        assert "cfi_completion_role" not in coordinator.db_url

        # 3. Underlying TrustedTrainingJobRecordSource and authority backend
        authority_source = coordinator.authority_source
        backend = authority_source._db._backend
        assert isinstance(backend, PostgresTrainingJobAuthority)
        assert backend._db_url == coord_url
        assert not hasattr(backend, "_completion_db_url")

        # 4. Process environment check
        assert "CFI_TRAINING_JOB_COMPLETION_DB_URL" not in os.environ

        # 5. Deep scan of coordinator instance dict: verify no completion URL or password leaked
        comp_parsed = psycopg.conninfo.conninfo_to_dict(comp_url)
        raw_password = comp_parsed.get("password")
        comp_password: str = str(raw_password) if raw_password is not None else ""

        def _scan_for_leak(obj: Any, depth: int = 0) -> None:
            if depth > 4 or obj is None:
                return
            if isinstance(obj, str):
                if comp_password and comp_password in obj:
                    raise AssertionError("Completion password leaked in coordinator object graph!")
                if "cfi_completion_role" in obj:
                    raise AssertionError("Completion role name found in coordinator object graph!")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    assert k != "completion_db_url"
                    _scan_for_leak(v, depth + 1)
            elif hasattr(obj, "__dict__"):
                for k, v in vars(obj).items():
                    assert k != "completion_db_url"
                    _scan_for_leak(v, depth + 1)

        _scan_for_leak(coordinator)

    def test_comp_02_direct_sql_completion_forgery(self, pg_roles: dict[str, str]) -> None:
        """COMP-02: Prove coordinator PostgreSQL role cannot directly forge completed-job records.

        Under cfi_coord_role:
        - Schedule a legitimate job.
        - Do not perform genuine training.
        - Attempt direct SQL INSERT into cfi_completed_jobs with syntactically valid data.
        - Verify authorization rejection with SQLSTATE 42501 (InsufficientPrivilege).
        - Verify 0 rows exist in cfi_completed_jobs.
        """
        coord_url = pg_roles["coordinator"]
        admin_url = pg_roles["admin"]
        uid = uuid.uuid4().hex[:8]
        job_id = f"job_forge_{uid}"
        run_id = f"run_forge_{uid}"
        sim_id = f"sim_forge_{uid}"
        art_sha = hashlib.sha256(b"forged_model").hexdigest()

        with psycopg.connect(coord_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                # 1. Schedule legitimate job under cfi_coord_role (authorized)
                cur.execute(
                    """
                    INSERT INTO cfi_scheduled_jobs (
                        job_id, training_run_id, simulation_id, coordinator_id,
                        architecture, feature_schema_version, expected_rounds,
                        expected_epochs, input_dim, feature_names_json, permitted_workers_json,
                        config_fingerprint, scheduled_at, status
                    ) VALUES (%s, %s, %s, 'cfi_coord_role', 'FraudDetectionNN', 'v1.0', 1, 1, 10, '[]', '[]', 'fp', NOW(), 'SCHEDULED')
                    """,
                    (job_id, run_id, sim_id),
                )
                conn.commit()

                # 2. Attempt direct SQL INSERT into cfi_completed_jobs under cfi_coord_role
                fnames_json = json.dumps(list(FEATURE_NAMES))
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc:
                    cur.execute(
                        """
                        INSERT INTO cfi_completed_jobs (
                            job_id, training_run_id, simulation_id, completed_at, status,
                            executed_rounds, executed_epochs, sample_count, observed_metrics_json,
                            artifact_sha256, architecture, feature_schema_version, input_dim,
                            feature_names_json, worker_id, evidence_fingerprint
                        ) VALUES (%s, %s, %s, NOW(), 'COMPLETED', 1, 1, 100, '{"loss": 0.05}',
                                  %s, 'FraudDetectionNN', 'v1.0', 10, %s, 'FEDERATED_COORDINATOR', 'fake_fp')
                        """,
                        (job_id, run_id, sim_id, art_sha, fnames_json),
                    )
                assert exc.value.sqlstate == "42501"
                conn.rollback()

        # 3. Verify under admin authority that NO completed record was persisted
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_comp_03_privileged_connection_bypass(self, pg_roles: dict[str, str]) -> None:
        """COMP-03: Prove coordinator cannot obtain or invoke privileged completion connection.

        Attempts:
        - SET ROLE cfi_completion_role under coordinator session fails with SQLSTATE 42501.
        - Invoking complete_job via coordinator PostgresTrainingJobAuthority fails with SQLSTATE 42501.
        """
        coord_url = pg_roles["coordinator"]
        admin_url = pg_roles["admin"]
        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bypass_{uid}"
        run_id = f"run_bypass_{uid}"
        sim_id = f"sim_bypass_{uid}"
        art_sha = hashlib.sha256(b"bypass_model").hexdigest()

        # 1. Attempt SET ROLE cfi_completion_role under cfi_coord_role
        with psycopg.connect(coord_url) as conn:
            with conn.cursor() as cur:
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc:
                    cur.execute("SET ROLE cfi_completion_role;")
                assert exc.value.sqlstate == "42501"
                conn.rollback()

        # 2. Setup job in DB and try to call complete_job using coordinator connection
        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
        )

        coord_auth = PostgresTrainingJobAuthority(db_url=coord_url)
        with pytest.raises(Exception) as exc_info:
            coord_auth.complete_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=450,
                observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                artifact_sha256=art_sha,
                enforce_lifecycle=True,
            )
        # Must fail due to PostgreSQL permission rejection (SQLSTATE 42501)
        assert "42501" in str(exc_info.value) or "permission denied" in str(exc_info.value).lower()

    def test_comp_04_forged_finalization_request(self, pg_roles: dict[str, str]) -> None:
        """COMP-04: Prove independent completion authority rejects forged finalization requests.

        Coordinator requests finalization for a scheduled job with NO observations, aggregations, or evaluations.
        Independent completion authority independently reconciles PostgreSQL evidence and rejects it.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]
        admin_auth = PostgresTrainingJobAuthority(db_url=admin_url)

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_forged_req_{uid}"
        run_id = f"run_forged_req_{uid}"
        sim_id = f"sim_forged_req_{uid}"
        art_sha = hashlib.sha256(b"forged_req").hexdigest()

        # Schedule and start job ONLY (no observations or aggregations)
        admin_auth.schedule_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            coordinator_id="cfi_coord_role",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=10,
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        admin_auth.start_job(job_id=job_id, worker_id="BANK_A")

        # Request finalization from isolated completion authority
        with IsolatedTrainingCompletionAuthority(
            completion_db_url=comp_url,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        ) as comp_auth:
            with pytest.raises(TrainingProvenanceSecurityError, match="no verified execution observations recorded"):
                comp_auth.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_TOKEN,
                )

        # Verify no row was inserted
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_comp_05_genuine_finalization(self, pg_roles: dict[str, str]) -> None:
        """COMP-05: Execute valid observed training lifecycle and demonstrate successful completion.

        Verifies:
        - Independent completion authority reconciles real PostgreSQL events.
        - Atomically writes completed record to cfi_completed_jobs under cfi_completion_role.
        - Coordinator never possessed completion credentials.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_genuine_{uid}"
        run_id = f"run_genuine_{uid}"
        sim_id = f"sim_genuine_{uid}"
        art_sha = hashlib.sha256(f"genuine_model_{uid}".encode()).hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
        )

        with IsolatedTrainingCompletionAuthority(
            completion_db_url=comp_url,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        ) as comp_auth:
            record = comp_auth.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=450,
                observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                artifact_sha256=art_sha,
                coordinator_token=COORD_TOKEN,
            )

            assert record.job_id == job_id
            assert record.status == "COMPLETED"
            assert record.artifact_sha256 == art_sha
            assert record.sample_count == 450
            assert record.executed_rounds == 1
            assert record.executed_epochs == 1

        # Verify PostgreSQL durable state
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == job_id
            assert row[8] == art_sha

    def test_comp_06_atomic_validation_finalization(self, pg_roles: dict[str, str]) -> None:
        """COMP-06: Attempt to tamper with evidence during completion.

        Verify the transaction either produces one fully consistent completed record
        or rolls back without leaving partial or corrupted records.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_atomic_{uid}"
        run_id = f"run_atomic_{uid}"
        sim_id = f"sim_atomic_{uid}"
        art_sha = hashlib.sha256(f"atomic_model_{uid}".encode()).hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
        )

        with IsolatedTrainingCompletionAuthority(
            completion_db_url=comp_url,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        ) as comp_auth:
            # Adversarial claim: tampered sample count (claimed 9999 vs observed 450)
            with pytest.raises(TrainingProvenanceSecurityError, match="Sample count mismatch"):
                comp_auth.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=9999,  # Inconsistent
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_TOKEN,
                )

        # Verify transaction was completely aborted; zero completed rows exist
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_comp_07_concurrent_equivalent_completion(self, pg_roles: dict[str, str]) -> None:
        """COMP-07: Submit concurrent equivalent completion requests from independent callers.

        Verifies:
        - Handled safely via PostgreSQL advisory transaction locking.
        - Documented idempotency returns identical completed record.
        - Exactly one authoritative completed row is persisted in cfi_completed_jobs.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_concurrent_{uid}"
        run_id = f"run_concurrent_{uid}"
        sim_id = f"sim_concurrent_{uid}"
        art_sha = hashlib.sha256(f"concurrent_model_{uid}".encode()).hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
        )

        with IsolatedTrainingCompletionAuthority(
            completion_db_url=comp_url,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        ) as comp_auth:
            def _request_completion() -> Any:
                return comp_auth.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_TOKEN,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(_request_completion)
                f2 = executor.submit(_request_completion)
                r1 = f1.result(timeout=15)
                r2 = f2.result(timeout=15)

            assert r1.job_id == r2.job_id == job_id
            assert r1.artifact_sha256 == r2.artifact_sha256 == art_sha

        # Exactly 1 row in PostgreSQL
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1

    def test_comp_08_conflicting_completion(self, pg_roles: dict[str, str]) -> None:
        """COMP-08: Attempt incompatible completion after a valid finalization.

        Verifies:
        - Rejection of conflicting completion request.
        - Persisted evidence in cfi_completed_jobs remains unchanged.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_conflict_{uid}"
        run_id = f"run_conflict_{uid}"
        sim_id = f"sim_conflict_{uid}"
        art_sha_1 = hashlib.sha256(f"initial_model_{uid}".encode()).hexdigest()
        art_sha_2 = hashlib.sha256(f"conflicting_model_{uid}".encode()).hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha_1,
        )

        with IsolatedTrainingCompletionAuthority(
            completion_db_url=comp_url,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        ) as comp_auth:
            # 1. First valid finalization
            r1 = comp_auth.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=450,
                observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                artifact_sha256=art_sha_1,
                coordinator_token=COORD_TOKEN,
            )
            assert r1.artifact_sha256 == art_sha_1

            # 2. Conflicting completion request with different artifact hash
            with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting completion record"):
                comp_auth.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha_2,
                    coordinator_token=COORD_TOKEN,
                )

        # 3. Persisted row remains unchanged with original artifact sha
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT artifact_sha256 FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            persisted_sha = row[0]
            assert persisted_sha == art_sha_1

    def test_comp_09_process_database_failure(self, pg_roles: dict[str, str]) -> None:
        """COMP-09: Terminate or interrupt finalization boundary at confirmed checkpoint.

        Verifies:
        - Checkpoint failure before commit rolls back cleanly; no partial completion trusted.
        - Process crash fails closed with TrainingProvenanceSecurityError.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_fail_{uid}"
        run_id = f"run_fail_{uid}"
        sim_id = f"sim_fail_{uid}"
        art_sha = hashlib.sha256(f"fail_model_{uid}".encode()).hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            artifact_sha=art_sha,
        )

        with IsolatedTrainingCompletionAuthority(
            completion_db_url=comp_url,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        ) as comp_auth:
            # 1. Simulate failure before commit at confirmed execution checkpoint
            with pytest.raises(TrainingProvenanceSecurityError, match="Simulated execution checkpoint failure before commit"):
                comp_auth.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_TOKEN,
                    simulate_failure_before_commit=True,
                )

            # Verify no row was inserted into PostgreSQL
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()
                assert row is not None
                assert row[0] == 0

            # 2. Terminate completion worker process and verify fail-closed behavior
            comp_auth.terminate()
            with pytest.raises(TrainingProvenanceSecurityError, match="worker process terminated"):
                comp_auth.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=450,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_TOKEN,
                )

    def test_comp_10_downstream_provenance(self, pg_roles: dict[str, str], tmp_path) -> None:
        """COMP-10: Downstream provenance verification for rejected and legitimate completions.

        Verifies:
        - Rejected/forged completion cannot receive an Ed25519 receipt or ModelRegistry admission.
        - Genuine completed job retains working receipt issuance, public verification, and Registry admission.
        """
        comp_url = pg_roles["completion"]
        admin_url = pg_roles["admin"]
        issuer_url = pg_roles["issuer"]

        # Setup issuer key corresponding to DEFAULT_TRAINING_KEY_ID in trust store
        issuer_priv_hex = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()

        # Job Record Source under read-only cfi_issuer_role
        issuer_source = TrustedTrainingJobRecordSource(
            db_url=issuer_url,
            enforce_lifecycle=True,
            app_env="production",
            worker_token="tok_issuer",
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
            storage_dir=str(tmp_path / "model_reg"),
            verifier=verifier,
        )

        # -------------------------------------------------------------
        # Part A: Attempt receipt and admission for uncompleted/forged job
        # -------------------------------------------------------------
        uncompleted_job_id = f"job_uncomp_{uuid.uuid4().hex[:8]}"
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

        # ModelRegistry without valid receipt marks candidate UNVERIFIED and ineligible
        unverified_version = registry.save_version(
            simulation_id="sim_uncomp",
            state_dict={},
            metrics={"loss": 0.05},
            is_promoted=False,
            training_receipt=None,
        )
        assert unverified_version["is_production_eligible"] is False
        assert unverified_version["provenance_type"] == "UNVERIFIED"

        # -------------------------------------------------------------
        # Part B: Legitimate completion workflow
        # -------------------------------------------------------------
        uid = uuid.uuid4().hex[:8]
        legit_job_id = f"job_legit_{uid}"
        legit_run_id = f"run_legit_{uid}"
        legit_sim_id = f"sim_legit_{uid}"
        model_bytes = f"legit_model_{uid}".encode()
        legit_art_sha = hashlib.sha256(model_bytes).hexdigest()

        helper_setup_valid_observed_job(
            admin_url=admin_url,
            job_id=legit_job_id,
            run_id=legit_run_id,
            sim_id=legit_sim_id,
            artifact_sha=legit_art_sha,
        )

        with IsolatedTrainingCompletionAuthority(
            completion_db_url=comp_url,
            coordinator_token=COORD_TOKEN,
            app_env="production",
        ) as comp_auth:
            comp_auth.finalize_training_completion(
                job_id=legit_job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=450,
                observed_metrics={"loss": 0.05, "auc_roc": 0.94},
                artifact_sha256=legit_art_sha,
                coordinator_token=COORD_TOKEN,
            )

        # Issue receipt reading from PostgreSQL under cfi_issuer_role
        legit_receipt = issuer.issue_receipt(
            job_id=legit_job_id,
            training_run_id=legit_run_id,
            simulation_id=legit_sim_id,
            model_artifact_sha256=legit_art_sha,
            architecture="FraudDetectionNN",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=450,
            metrics={"loss": 0.05, "auc_roc": 0.94},
            producer_id="FEDERATED_COORDINATOR",
        )
        assert legit_receipt.job_id == legit_job_id
        assert legit_receipt.model_artifact_sha256 == legit_art_sha

        # Verify receipt with public-key-only verifier
        is_valid, _ = verifier.verify_receipt(
            receipt=legit_receipt,
            expected_simulation_id=legit_sim_id,
            expected_architecture="FraudDetectionNN",
            expected_feature_schema_version="v1.0",
            expected_input_dim=NUM_FEATURES,
            expected_feature_names=list(FEATURE_NAMES),
        )
        assert is_valid is True

        # ModelRegistry admits verified inactive candidate
        admitted_version = registry.save_version(
            simulation_id=legit_sim_id,
            state_dict={},
            artifact_bytes=model_bytes,
            metrics={"loss": 0.05, "auc_roc": 0.94},
            is_promoted=False,
            training_receipt=legit_receipt,
        )
        assert admitted_version["is_production_eligible"] is True
        assert admitted_version["provenance_type"] == "TRAINING_RECORDED"
        assert admitted_version["status"] == "inactive"
        assert admitted_version["is_active"] is False

        # Verify active champion remains unaltered
        active_champ = registry.get_active_champion_identity(legit_sim_id)
        assert active_champ is None
