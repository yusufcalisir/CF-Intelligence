"""Mandatory Adversarial Test Suite for Authentic Aggregation and Evaluation Evidence.

Verifies the security guarantees:
- EVID-01: Direct aggregation forgery rejected at the database and service boundaries.
- EVID-02: Evaluation forgery cannot become trusted (rejection of coordinator direct write, unaggregated digests, out-of-range metrics).
- EVID-03: Artifact substitution rejected (final artifact digest bound strictly to accepted aggregations).
- EVID-04: Cross-run replay rejected (cannot reuse digests across jobs or across rounds).
- EVID-05: Missing evidence fails closed (missing aggregations or evaluations blocks completion).
- EVID-06: Downstream Ed25519 receipt and ModelRegistry reject forged evidence.
- EVID-07: Authentic evidence persists exact model digests and measured metrics through completion.
- EVID-08: Genuine three-bank, two-round federation regression succeeds across isolation boundaries.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
import psycopg
import pytest
from cryptography.fernet import Fernet

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.distributed_fl_service import (
    IsolatedReceiptSigningAuthority,
    IsolatedTrainingCompletionAuthority,
    provision_isolated_completion_authority_vault,
    provision_isolated_issuer_vault,
    setup_bank_private_storage,
    spawn_bank_workers,
    spawn_isolated_completion_authority_service,
    spawn_isolated_coordinator_process,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingProvenanceSecurityError,
    TrainingReceiptVerifier,
)
from app.infrastructure.database.training_authority_backends import (
    GlobalAggregationEvent,
    ModelEvaluationEvent,
    PostgresTrainingJobAuthority,
    provision_runtime_service_roles,
)

COORD_SECRET = "coord_secret_token_evid_suite_99"
BANK_TOKENS = {
    "BANK_A": "bank_a_token_evid_suite_01",
    "BANK_B": "bank_b_token_evid_suite_02",
    "BANK_C": "bank_c_token_evid_suite_03",
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


def helper_setup_prerequisite_job(
    admin_url: str,
    job_id: str,
    run_id: str,
    sim_id: str,
    expected_rounds: int = 2,
    permitted_workers: tuple[str, ...] = ("BANK_A", "BANK_B", "BANK_C"),
) -> None:
    """Ensure a valid scheduled and started job exists so tests evaluate evidence authorization."""
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
        expected_epochs=expected_rounds,
        input_dim=len(fnames),
        feature_names=fnames,
        permitted_workers=list(permitted_workers),
    )
    admin_auth.start_job(job_id=job_id, worker_id=permitted_workers[0])


def helper_insert_bank_observation(
    db_url: str,
    job_id: str,
    run_id: str,
    sim_id: str,
    bank_id: str,
    round_number: int,
    model_update_digest: str,
    sample_count: int = 100,
    observed_loss: float = 0.05,
) -> str:
    """Insert a genuine bank round observation directly using bank role."""
    now_str = datetime.now(UTC).isoformat()
    event_id = f"evt_{job_id}_r{round_number}_{bank_id}_{uuid.uuid4().hex[:8]}"
    payload_digest = hashlib.sha256(f"pay_{event_id}".encode()).hexdigest()
    sql = """
    INSERT INTO cfi_round_observations (
        event_id, job_id, training_run_id, simulation_id,
        worker_id, bank_id, round_number, attempt_number,
        actual_sample_count, actual_executed_epochs, observed_loss,
        metrics_json, model_update_digest, execution_timestamp,
        execution_status, payload_digest
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    event_id, job_id, run_id, sim_id,
                    bank_id, bank_id, round_number, 1,
                    sample_count, 1, observed_loss,
                    json.dumps({"loss": observed_loss}),
                    model_update_digest, now_str,
                    "COMPLETED", payload_digest,
                ),
            )
    return event_id


INSERT_AGGREGATION_SQL = """
INSERT INTO cfi_global_aggregations (
    event_id, job_id, round_number, aggregation_algorithm,
    accepted_participants_json, accepted_digests_json,
    aggregation_config_json, aggregated_model_digest,
    timestamp, aggregation_status, payload_digest
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

INSERT_EVALUATION_SQL = """
INSERT INTO cfi_model_evaluations (
    event_id, job_id, evaluation_run_id, metric_name,
    metric_value, evaluated_artifact_digest, evaluator_identity,
    evaluation_timestamp, dataset_manifest_id, payload_digest
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class TestAuthenticEvidenceEnforcement:
    """Adversarial test suite proving coordinator cannot fabricate evidence."""

    def test_evid_01_direct_aggregation_forgery_rejected_at_db_boundary(
        self, pg_roles: dict[str, str]
    ) -> None:
        """EVID-01: Direct aggregation forgery using real coordinator role is rejected at DB boundary."""
        admin_url = pg_roles["admin"]
        coord_url = pg_roles["coordinator"]
        bank_a_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_evid01_{uid}"
        run_id = f"run_evid01_{uid}"
        sim_id = f"sim_evid01_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id, expected_rounds=2)

        # Genuine observation from Bank A only
        digest_a = hashlib.sha256(b"authentic_update_bank_a").hexdigest()
        helper_insert_bank_observation(bank_a_url, job_id, run_id, sim_id, "BANK_A", 1, digest_a)

        fake_digest_b = hashlib.sha256(b"fabricated_update_bank_b").hexdigest()
        fake_agg_digest = hashlib.sha256(b"fabricated_global_model").hexdigest()

        # Case 1: Direct SQL insert under cfi_coord_role claiming unobserved Bank B update
        with psycopg.connect(coord_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(
                        INSERT_AGGREGATION_SQL,
                        (
                            f"agg_{job_id}_1", job_id, 1, "fed_avg_weighted",
                            json.dumps(["BANK_A", "BANK_B"]),
                            json.dumps([digest_a, fake_digest_b]),
                            "{}", fake_agg_digest,
                            datetime.now(UTC).isoformat(), "COMPLETED",
                            hashlib.sha256(b"pay_fake").hexdigest(),
                        ),
                    )
                # Verify exact PostgreSQL error code 42501 (insufficient_privilege)
                assert exc_info.value.sqlstate == "42501"
                assert "Direct aggregation forgery" in str(exc_info.value)
                conn.rollback()

        # Case 2: Attempt via Python PostgresTrainingJobAuthority under cfi_coord_role
        coord_auth = PostgresTrainingJobAuthority(db_url=coord_url)
        event_forged = GlobalAggregationEvent(
            event_id=f"agg_{job_id}_fake2",
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A", "BANK_B"),
            accepted_local_update_digests=(digest_a, fake_digest_b),
            aggregation_configuration={},
            aggregated_global_model_digest=fake_agg_digest,
            timestamp=datetime.now(UTC).isoformat(),
        )
        with pytest.raises((TrainingProvenanceSecurityError, psycopg.Error)):
            coord_auth.record_global_aggregation(event_forged)

        # Case 3: Verify zero aggregation rows persisted
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_global_aggregations WHERE job_id = %s", (job_id,))
            assert cur.fetchone()[0] == 0

    def test_evid_02_evaluation_forgery_cannot_become_trusted(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """EVID-02: Evaluation forgery cannot become trusted across role and trigger boundaries."""
        admin_url = pg_roles["admin"]
        coord_url = pg_roles["coordinator"]
        comp_url = pg_roles["completion"]
        eval_url = pg_roles["evaluator"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_evid02_{uid}"
        run_id = f"run_evid02_{uid}"
        sim_id = f"sim_evid02_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id, expected_rounds=1)

        unagg_digest = hashlib.sha256(b"unaggregated_model_bytes").hexdigest()

        # Part A: Coordinator role has NO direct INSERT privileges on cfi_model_evaluations
        with psycopg.connect(coord_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(
                        INSERT_EVALUATION_SQL,
                        (
                            f"eval_{job_id}_1", job_id, f"run_eval_{job_id}",
                            "auc_roc", 0.9999, unagg_digest, "cfi_coord_role",
                            datetime.now(UTC).isoformat(), None,
                            hashlib.sha256(b"pay_fake_eval").hexdigest(),
                        ),
                    )
                # SQLSTATE 42501: permission denied for table cfi_model_evaluations
                assert exc_info.value.sqlstate == "42501"
                conn.rollback()

        # Part B: Evaluator role attempts to record evaluation for an unaggregated digest
        with psycopg.connect(eval_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(
                        INSERT_EVALUATION_SQL,
                        (
                            f"eval_{job_id}_2", job_id, f"run_eval_{job_id}",
                            "auc_roc", 0.88, unagg_digest, "cfi_evaluator_role",
                            datetime.now(UTC).isoformat(), None,
                            hashlib.sha256(b"pay_fake_eval2").hexdigest(),
                        ),
                    )
                assert exc_info.value.sqlstate == "42501"
                assert "Evaluation forgery" in str(exc_info.value)
                conn.rollback()

        # Part C: Attempt out-of-range metrics (e.g. auc_roc > 1.0 or loss < 0)
        with psycopg.connect(eval_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(
                        INSERT_EVALUATION_SQL,
                        (
                            f"eval_{job_id}_3", job_id, f"run_eval_{job_id}",
                            "auc_roc", 1.99, unagg_digest, "cfi_evaluator_role",
                            datetime.now(UTC).isoformat(), None,
                            hashlib.sha256(b"pay_fake_eval3").hexdigest(),
                        ),
                    )
                # SQLSTATE 42501 or 23514 check_violation
                assert exc_info.value.sqlstate in ("42501", "23514")
                conn.rollback()

        # Part D: Isolated completion authority rejects completion with unpersisted/fake metrics
        comp_vault_dir = str(tmp_path / "comp_vault_evid02")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            with pytest.raises(TrainingProvenanceSecurityError):
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=100,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.9999},
                    artifact_sha256=unagg_digest,
                    coordinator_token=COORD_SECRET,
                )
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    def test_evid_03_artifact_substitution_rejected(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """EVID-03: Attempt to bind genuine observed evidence to substituted model bytes is rejected."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]
        eval_url = pg_roles["evaluator"]
        coord_url = pg_roles["coordinator"]
        bank_a_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_evid03_{uid}"
        run_id = f"run_evid03_{uid}"
        sim_id = f"sim_evid03_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id, expected_rounds=1, permitted_workers=("BANK_A",))

        # Genuine observation
        update_digest = hashlib.sha256(b"authentic_update_r1").hexdigest()
        helper_insert_bank_observation(bank_a_url, job_id, run_id, sim_id, "BANK_A", 1, update_digest, sample_count=100)

        # Genuine global aggregation
        authentic_model_digest = hashlib.sha256(b"authentic_model_bytes_r1").hexdigest()
        coord_auth = PostgresTrainingJobAuthority(db_url=coord_url)
        coord_auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"agg_{job_id}_1",
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=("BANK_A",),
                accepted_local_update_digests=(update_digest,),
                aggregation_configuration={},
                aggregated_global_model_digest=authentic_model_digest,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

        # Genuine evaluation for authentic_model_digest
        eval_auth = PostgresTrainingJobAuthority(db_url=eval_url)
        eval_auth.record_model_evaluation(
            ModelEvaluationEvent(
                event_id=f"eval_{job_id}_auc",
                job_id=job_id,
                evaluation_run_id=f"run_{job_id}",
                metric_name="auc_roc",
                metric_value=0.88,
                evaluated_artifact_digest=authentic_model_digest,
                evaluator_identity="cfi_evaluator_role",
                evaluation_timestamp=datetime.now(UTC).isoformat(),
            )
        )

        # Attacker attempts completion substituting a different model artifact digest
        substituted_model_digest = hashlib.sha256(b"backdoored_substituted_model_bytes").hexdigest()

        comp_vault_dir = str(tmp_path / "comp_vault_evid03")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            with pytest.raises(TrainingProvenanceSecurityError, match="Artifact SHA-256 does not match final accepted aggregation digest"):
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=100,
                    observed_metrics={"loss": 0.05, "auc_roc": 0.88},
                    artifact_sha256=substituted_model_digest,  # substituted!
                    coordinator_token=COORD_SECRET,
                )
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

        # Verify no completion row exists
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            assert cur.fetchone()[0] == 0

    def test_evid_04_cross_run_replay_rejected(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """EVID-04: Cross-run and cross-round replay of aggregation/evaluation evidence is rejected."""
        admin_url = pg_roles["admin"]
        coord_url = pg_roles["coordinator"]
        bank_a_url = pg_roles["worker_bank_a"]

        # Run 1
        uid1 = uuid.uuid4().hex[:8]
        job1 = f"job_evid04_1_{uid1}"
        run1 = f"run_evid04_1_{uid1}"
        sim1 = f"sim_evid04_1_{uid1}"
        helper_setup_prerequisite_job(admin_url, job1, run1, sim1, expected_rounds=1, permitted_workers=("BANK_A",))

        digest_job1 = hashlib.sha256(b"job1_update_digest").hexdigest()
        helper_insert_bank_observation(bank_a_url, job1, run1, sim1, "BANK_A", 1, digest_job1)

        # Run 2
        uid2 = uuid.uuid4().hex[:8]
        job2 = f"job_evid04_2_{uid2}"
        run2 = f"run_evid04_2_{uid2}"
        sim2 = f"sim_evid04_2_{uid2}"
        helper_setup_prerequisite_job(admin_url, job2, run2, sim2, expected_rounds=1, permitted_workers=("BANK_A",))

        # Part A: Coordinator attempts to record aggregation in Job 2 using digest from Job 1
        model_digest2 = hashlib.sha256(b"job2_model").hexdigest()
        coord_auth = PostgresTrainingJobAuthority(db_url=coord_url)

        with pytest.raises((TrainingProvenanceSecurityError, psycopg.Error)):
            coord_auth.record_global_aggregation(
                GlobalAggregationEvent(
                    event_id=f"agg_{job2}_1",
                    job_id=job2,
                    round_number=1,
                    aggregation_algorithm="fed_avg_weighted",
                    accepted_participant_ids=("BANK_A",),
                    accepted_local_update_digests=(digest_job1,),  # replayed from Job 1!
                    aggregation_configuration={},
                    aggregated_global_model_digest=model_digest2,
                    timestamp=datetime.now(UTC).isoformat(),
                )
            )

        # Part B: In-job cross-round replay (attempting to reuse round 1 digest in round 2)
        uid3 = uuid.uuid4().hex[:8]
        job3 = f"job_evid04_3_{uid3}"
        run3 = f"run_evid04_3_{uid3}"
        sim3 = f"sim_evid04_3_{uid3}"
        helper_setup_prerequisite_job(admin_url, job3, run3, sim3, expected_rounds=2, permitted_workers=("BANK_A",))

        dig_r1 = hashlib.sha256(b"r1_update").hexdigest()
        helper_insert_bank_observation(bank_a_url, job3, run3, sim3, "BANK_A", 1, dig_r1)
        # Bank observation for round 2 replaying dig_r1
        helper_insert_bank_observation(bank_a_url, job3, run3, sim3, "BANK_A", 2, dig_r1)

        m_r1 = hashlib.sha256(b"m1").hexdigest()
        coord_auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"agg_{job3}_1", job_id=job3, round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=("BANK_A",),
                accepted_local_update_digests=(dig_r1,),
                aggregation_configuration={},
                aggregated_global_model_digest=m_r1,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )
        m_r2 = hashlib.sha256(b"m2").hexdigest()
        # Trigger rejects replaying digest across rounds
        with pytest.raises((TrainingProvenanceSecurityError, psycopg.Error)):
            coord_auth.record_global_aggregation(
                GlobalAggregationEvent(
                    event_id=f"agg_{job3}_2", job_id=job3, round_number=2,
                    aggregation_algorithm="fed_avg_weighted",
                    accepted_participant_ids=("BANK_A",),
                    accepted_local_update_digests=(dig_r1,),  # replayed in round 2!
                    aggregation_configuration={},
                    aggregated_global_model_digest=m_r2,
                    timestamp=datetime.now(UTC).isoformat(),
                )
            )

    def test_evid_05_missing_evidence_fails_closed(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """EVID-05: Missing aggregation or evaluation evidence fails completion closed."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]
        bank_a_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_evid05_{uid}"
        run_id = f"run_evid05_{uid}"
        sim_id = f"sim_evid05_{uid}"
        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id, expected_rounds=2, permitted_workers=("BANK_A",))

        # Insert observations for rounds 1 and 2
        d1 = hashlib.sha256(b"d1_evid05").hexdigest()
        d2 = hashlib.sha256(b"d2_evid05").hexdigest()
        helper_insert_bank_observation(bank_a_url, job_id, run_id, sim_id, "BANK_A", 1, d1)
        helper_insert_bank_observation(bank_a_url, job_id, run_id, sim_id, "BANK_A", 2, d2)

        # Aggregate only round 1; missing round 2 aggregation!
        m1 = hashlib.sha256(b"m1_evid05").hexdigest()
        coord_auth = PostgresTrainingJobAuthority(db_url=coord_url)
        coord_auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"agg_{job_id}_1", job_id=job_id, round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=("BANK_A",),
                accepted_local_update_digests=(d1,),
                aggregation_configuration={},
                aggregated_global_model_digest=m1,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

        comp_vault_dir = str(tmp_path / "comp_vault_evid05")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            # Fails closed because round 2 aggregation is missing
            with pytest.raises(TrainingProvenanceSecurityError, match="Global aggregation round continuity mismatch|missing global aggregation records"):
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=2,
                    executed_epochs=2,
                    sample_count=100,
                    observed_metrics={"loss": 0.05},
                    artifact_sha256=m1,
                    coordinator_token=COORD_SECRET,
                )
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

    def test_evid_06_downstream_receipt_and_registry_rejected_for_forged_evidence(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """EVID-06: Forged evidence cannot produce trusted receipts or verified ModelRegistry candidates."""
        admin_url = pg_roles["admin"]
        issuer_url = pg_roles["issuer"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_evid06_{uid}"
        run_id = f"run_evid06_{uid}"
        sim_id = f"sim_evid06_{uid}"
        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id, expected_rounds=1)

        # 1. Attempt receipt issuance on uncompleted / forged job
        dev_seed = hashlib.sha256(b"seed_evid06").digest().hex()
        issuer_vault_dir = str(tmp_path / "issuer_vault_evid06")
        issuer_key_file, pub_hex = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)

        signing_auth = IsolatedReceiptSigningAuthority(
            issuer_key_path=issuer_key_file,
            coordinator_token=COORD_SECRET,
            db_url=issuer_url,
            key_id=DEFAULT_TRAINING_KEY_ID,
            app_env="production",
        )
        try:
            with pytest.raises(TrainingProvenanceSecurityError, match="has no trusted COMPLETED record"):
                signing_auth.sign_completed_job_receipt(
                    job_id=job_id,
                    artifact_bytes=b"forged_model_bytes",
                    caller_token=COORD_SECRET,
                )
        finally:
            signing_auth.terminate()

        # 2. Attempt receipt verification on forged receipt
        verifier = TrainingReceiptVerifier(
            trusted_keys={DEFAULT_TRAINING_KEY_ID: pub_hex},
        )
        forged_receipt: dict[str, Any] = {
            "receipt_id": "rcpt_fake",
            "job_id": job_id,
            "training_run_id": run_id,
            "simulation_id": sim_id,
            "completed_at": datetime.now(UTC).isoformat(),
            "artifact_sha256": hashlib.sha256(b"forged_model_bytes").hexdigest(),
            "signer_key_id": DEFAULT_TRAINING_KEY_ID,
            "signature": "bad_hex_signature" * 4,
            "status": "COMPLETED",
            "executed_rounds": 1,
            "sample_count": 100,
            "observed_metrics": {"auc_roc": 0.99},
        }
        is_valid, reason = verifier.verify_receipt(forged_receipt)
        assert is_valid is False

        # 3. Model registry refuses to grant production eligibility or trusted provenance
        reg_dir = str(tmp_path / "reg_evid06")
        registry = ModelRegistry(storage_dir=reg_dir, verifier=verifier)
        saved = registry.save_version(
            simulation_id=sim_id,
            state_dict={},
            metrics={"auc_roc": 0.99},
            training_receipt=forged_receipt,
            provenance_type="TRAINING_RECORDED",
        )
        assert saved["provenance_type"] == "UNVERIFIED"
        assert saved["is_production_eligible"] is False
        assert "INVALID_RECEIPT" in saved["attestation_status"]

    def test_evid_07_authentic_evidence_persists_and_verifies(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """EVID-07: Authorized aggregation and real evaluation persist exact digests and metrics through completion."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]
        eval_url = pg_roles["evaluator"]
        coord_url = pg_roles["coordinator"]
        bank_a_url = pg_roles["worker_bank_a"]
        bank_b_url = pg_roles["worker_bank_b"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_evid07_{uid}"
        run_id = f"run_evid07_{uid}"
        sim_id = f"sim_evid07_{uid}"
        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id, expected_rounds=1, permitted_workers=("BANK_A", "BANK_B"))

        # 1. Bank observations
        dig_a = hashlib.sha256(b"update_a_evid07").hexdigest()
        dig_b = hashlib.sha256(b"update_b_evid07").hexdigest()
        helper_insert_bank_observation(bank_a_url, job_id, run_id, sim_id, "BANK_A", 1, dig_a, sample_count=100, observed_loss=0.04)
        helper_insert_bank_observation(bank_b_url, job_id, run_id, sim_id, "BANK_B", 1, dig_b, sample_count=200, observed_loss=0.06)

        # Weighted loss = (0.04*100 + 0.06*200) / 300 = (4 + 12) / 300 = 16 / 300 = 0.05333...
        expected_weighted_loss = (0.04 * 100 + 0.06 * 200) / 300

        # 2. Authentic global aggregation
        final_model_digest = hashlib.sha256(b"final_model_bytes_evid07").hexdigest()
        coord_auth = PostgresTrainingJobAuthority(db_url=coord_url)
        coord_auth.record_global_aggregation(
            GlobalAggregationEvent(
                event_id=f"agg_{job_id}_1", job_id=job_id, round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=("BANK_A", "BANK_B"),
                accepted_local_update_digests=(dig_a, dig_b),
                aggregation_configuration={},
                aggregated_global_model_digest=final_model_digest,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

        # 3. Authentic evaluation under cfi_evaluator_role
        eval_auth = PostgresTrainingJobAuthority(db_url=eval_url)
        eval_auth.record_model_evaluation(
            ModelEvaluationEvent(
                event_id=f"eval_{job_id}_auc", job_id=job_id,
                evaluation_run_id=f"run_eval_{job_id}",
                metric_name="auc_roc", metric_value=0.875,
                evaluated_artifact_digest=final_model_digest,
                evaluator_identity="cfi_evaluator_role",
                evaluation_timestamp=datetime.now(UTC).isoformat(),
            )
        )
        eval_auth.record_model_evaluation(
            ModelEvaluationEvent(
                event_id=f"eval_{job_id}_loss", job_id=job_id,
                evaluation_run_id=f"run_eval_{job_id}",
                metric_name="val_loss", metric_value=0.053,
                evaluated_artifact_digest=final_model_digest,
                evaluator_identity="cfi_evaluator_role",
                evaluation_timestamp=datetime.now(UTC).isoformat(),
            )
        )

        # 4. Atomic completion under cfi_completion_role
        comp_vault_dir = str(tmp_path / "comp_vault_evid07")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )
        try:
            client = IsolatedTrainingCompletionAuthority(
                service_endpoint=comp_endpoint,
                coordinator_token=COORD_SECRET,
                app_env="production",
            )
            completed_record = client.finalize_training_completion(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=300,
                observed_metrics={
                    "loss": expected_weighted_loss,
                    "auc_roc": 0.875,
                    "val_loss": 0.053,
                },
                artifact_sha256=final_model_digest,
                coordinator_token=COORD_SECRET,
            )
            assert completed_record.job_id == job_id
            assert completed_record.status == "COMPLETED"
            assert completed_record.artifact_sha256 == final_model_digest
            assert completed_record.observed_metrics["auc_roc"] == 0.875
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

        # 5. Persisted record verification in database
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT artifact_sha256, observed_metrics_json FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == final_model_digest
            metrics = json.loads(row[1])
            assert metrics["auc_roc"] == 0.875

    def test_evid_08_genuine_federation_regression(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """EVID-08: Genuine three-bank, two-round PyTorch federated training through established isolation boundaries."""
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]
        issuer_url = pg_roles["issuer"]

        # 1. Setup completion authority
        comp_vault_dir = str(tmp_path / "comp_vault_evid08")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )

        # 2. Setup bank datasets and storage vaults
        vault_root = str(tmp_path / "bank_vaults_evid08")
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

        # 3. Spawn workers under dedicated bank database URLs
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

        # 4. Setup receipt signing authority
        dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
        issuer_vault_dir = str(tmp_path / "issuer_vault_evid08")
        issuer_key_file, pub_hex = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)
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
            job_id = f"job_evid08_{uid}"
            run_id = f"run_evid08_{uid}"
            sim_id = f"sim_evid08_{uid}"
            registry_dir = str(tmp_path / "model_reg_evid08")

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

            # 5. Assert successful completion, receipt issuance, and registry candidate creation
            assert result["completed_record"].job_id == job_id
            assert result["completed_record"].status == "COMPLETED"
            assert result["completed_record"].executed_rounds == 2
            assert result["receipt"].job_id == job_id
            assert result["saved_version"]["is_production_eligible"] is True

            # 6. Verify Ed25519 signature independently
            verifier = TrainingReceiptVerifier(
                trusted_keys={DEFAULT_TRAINING_KEY_ID: pub_hex},
            )
            is_valid, reason = verifier.verify_receipt(result["receipt"].to_dict())
            assert is_valid is True

            # 7. Verify candidate in registry is inactive candidate (not automatically production active)
            registry = ModelRegistry(storage_dir=registry_dir)
            active_prod = registry.get_active_version(sim_id)
            assert active_prod is None  # Candidate is registered inactive pending governance promotion
        finally:
            coord_client.terminate()
            comp_proc.terminate()
            comp_proc.join(timeout=3)
            signing_auth.terminate()
            for p in procs.values():
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=3)
