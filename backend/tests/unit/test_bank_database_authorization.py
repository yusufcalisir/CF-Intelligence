"""Mandatory Executable Security Test Suite for Cross-Bank PostgreSQL Worker Authorization.

Verifies the elimination of cross-bank worker identity bypass:
- BANKDB-01: Bank A writes a valid Bank A observation successfully.
- BANKDB-02: Bank A attempts a valid Bank B observation; PostgreSQL rejects it for the intended authorization reason.
- BANKDB-03: Bank B attempts Bank C impersonation; rejected.
- BANKDB-04: Generic cfi_worker_role attempts to insert observations for each bank; no unrestricted cross-bank write is permitted.
- BANKDB-05: Missing bank-specific database credentials cannot silently fall back to the generic role in production.
- BANKDB-06: Caller-supplied bank_id and worker_id cannot override authenticated identity.
- BANKDB-07: Bank A cannot update, delete, or improperly read Bank B's protected observation evidence.
- BANKDB-08: Verify actual PostgreSQL current_user, effective grants, role membership, and RLS enforcement, including attempted bypasses.
- BANKDB-09: Invalid/rejected observations leave no authoritative evidence and cannot contribute to a trusted completion.
- BANKDB-10: Genuine three-bank, two-round federated training succeeds using only the corrected bank-specific runtime permissions, including completion and receipt verification.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
import psycopg
import psycopg.conninfo
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
from app.application.services.model_service import NUM_FEATURES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingProvenanceSecurityError,
    TrustedTrainingJobRecordSource,
)
from app.infrastructure.database.training_authority_backends import (
    PostgresTrainingJobAuthority,
    provision_runtime_service_roles,
)

COORD_SECRET = "coord_secret_token_bankdb_suite_99"
BANK_TOKENS = {
    "BANK_A": "bank_a_token_bankdb_suite_01",
    "BANK_B": "bank_b_token_bankdb_suite_02",
    "BANK_C": "bank_c_token_bankdb_suite_03",
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
    permitted_workers: tuple[str, ...] = ("BANK_A", "BANK_B", "BANK_C"),
) -> None:
    """Ensure a valid scheduled and started job exists so tests evaluate authorization, not missing prerequisites."""
    fnames = list(FEATURE_NAMES)
    admin_auth = PostgresTrainingJobAuthority(db_url=admin_url)
    admin_auth.schedule_job(
        job_id=job_id,
        training_run_id=run_id,
        simulation_id=sim_id,
        coordinator_id="cfi_coord_role",
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        expected_rounds=2,
        expected_epochs=2,
        input_dim=len(fnames),
        feature_names=fnames,
        permitted_workers=list(permitted_workers),
    )
    admin_auth.start_job(job_id=job_id, worker_id=permitted_workers[0])


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
    """Construct a syntactically valid observation tuple matching cfi_round_observations schema."""
    now_str = datetime.now(UTC).isoformat()
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


class TestBankDatabaseAuthorization:
    """Verification matrix eliminating cross-bank PostgreSQL worker identity bypass."""

    def test_bankdb_01_bank_a_writes_valid_bank_a_observation(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-01: Bank A writes a valid Bank A observation successfully."""
        admin_url = pg_roles["admin"]
        bank_a_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bdb01_{uid}"
        run_id = f"run_bdb01_{uid}"
        sim_id = f"sim_bdb01_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id)

        obs_params = helper_build_observation_tuple(
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            bank_id="BANK_A",
            worker_id="BANK_A",
        )

        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(INSERT_OBSERVATION_SQL, obs_params)

        # Verify persisted authoritative state in PostgreSQL as admin
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT bank_id, worker_id, actual_sample_count FROM cfi_round_observations WHERE job_id = %s",
                (job_id,),
            )
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "BANK_A"
            assert rows[0][1] == "BANK_A"

    def test_bankdb_02_bank_a_attempts_valid_bank_b_observation_rejected(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-02: Bank A attempts a valid Bank B observation; PostgreSQL rejects it for RLS policy violation."""
        admin_url = pg_roles["admin"]
        bank_a_url = pg_roles["worker_bank_a"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bdb02_{uid}"
        run_id = f"run_bdb02_{uid}"
        sim_id = f"sim_bdb02_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id)

        # Syntactically valid observation data, but claiming bank_id='BANK_B' and worker_id='BANK_B'
        forged_obs = helper_build_observation_tuple(
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            bank_id="BANK_B",
            worker_id="BANK_B",
        )

        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(INSERT_OBSERVATION_SQL, forged_obs)
                conn.rollback()

        # Check precise rejection reason: SQLSTATE 42501 (insufficient privilege / RLS violation)
        assert exc_info.value.sqlstate == "42501"
        assert "violates row-level security policy" in str(exc_info.value).lower()

        # Verify zero rows persisted in PostgreSQL
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_bankdb_03_bank_b_attempts_bank_c_impersonation_rejected(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-03: Bank B attempts Bank C impersonation; PostgreSQL rejects it for RLS policy violation."""
        admin_url = pg_roles["admin"]
        bank_b_url = pg_roles["worker_bank_b"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bdb03_{uid}"
        run_id = f"run_bdb03_{uid}"
        sim_id = f"sim_bdb03_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id)

        forged_c_obs = helper_build_observation_tuple(
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            bank_id="BANK_C",
            worker_id="BANK_C",
        )

        with psycopg.connect(bank_b_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(INSERT_OBSERVATION_SQL, forged_c_obs)
                conn.rollback()

        assert exc_info.value.sqlstate == "42501"
        assert "violates row-level security policy" in str(exc_info.value).lower()

        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_bankdb_04_generic_worker_cannot_insert_observations(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-04: Generic cfi_worker_role attempts to insert observations for each bank; no cross-bank write permitted."""
        admin_url = pg_roles["admin"]
        generic_worker_url = pg_roles["worker"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bdb04_{uid}"
        run_id = f"run_bdb04_{uid}"
        sim_id = f"sim_bdb04_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id)

        with psycopg.connect(generic_worker_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                for b_target in ("BANK_A", "BANK_B", "BANK_C"):
                    obs = helper_build_observation_tuple(
                        job_id=job_id,
                        run_id=run_id,
                        sim_id=sim_id,
                        bank_id=b_target,
                        worker_id=b_target,
                    )
                    with pytest.raises(psycopg.Error) as exc_info:
                        cur.execute(INSERT_OBSERVATION_SQL, obs)
                    conn.rollback()
                    # Must fail closed with SQLSTATE 42501
                    assert exc_info.value.sqlstate == "42501"

        # Verify zero rows written by generic worker role
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_round_observations WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 0

    def test_bankdb_05_missing_bank_credentials_cannot_fallback_to_generic(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-05: Missing bank-specific database credentials cannot silently fall back to the generic role in production."""
        generic_worker_url = pg_roles["worker"]

        # Case A: bank_db_urls is None in production
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="requires dedicated authenticated bank-specific database credentials.*Fallback to generic database role is strictly forbidden",
        ):
            spawn_bank_workers(
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                bank_tokens=BANK_TOKENS,
                db_url=generic_worker_url,
                bank_db_urls=None,
                app_env="production",
            )

        # Case B: bank_db_urls is missing BANK_B in production
        incomplete_urls = {
            "BANK_A": pg_roles["worker_bank_a"],
            "BANK_C": pg_roles["worker_bank_c"],
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="requires dedicated authenticated bank-specific database credentials",
        ):
            spawn_bank_workers(
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                bank_tokens=BANK_TOKENS,
                db_url=generic_worker_url,
                bank_db_urls=incomplete_urls,
                app_env="production",
            )

        # Case C: bank_db_urls explicitly passes generic cfi_worker_role URL in production
        forged_generic_dict = {
            "BANK_A": generic_worker_url,
            "BANK_B": pg_roles["worker_bank_b"],
            "BANK_C": pg_roles["worker_bank_c"],
        }
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="cannot execute under shared generic worker role 'cfi_worker_role'",
        ):
            spawn_bank_workers(
                participating_banks=["BANK_A", "BANK_B", "BANK_C"],
                bank_tokens=BANK_TOKENS,
                db_url=generic_worker_url,
                bank_db_urls=forged_generic_dict,
                app_env="production",
            )

    def test_bankdb_06_caller_supplied_ids_cannot_override_identity(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-06: Caller-supplied bank_id and worker_id cannot override authenticated identity."""
        admin_url = pg_roles["admin"]
        bank_a_url = pg_roles["worker_bank_a"]
        bank_b_url = pg_roles["worker_bank_b"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bdb06_{uid}"
        run_id = f"run_bdb06_{uid}"
        sim_id = f"sim_bdb06_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id)

        # Attempt A: Connected as Bank A, setting bank_id='BANK_B', worker_id='BANK_A'
        obs_a1 = helper_build_observation_tuple(
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            bank_id="BANK_B",
            worker_id="BANK_A",
        )
        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(INSERT_OBSERVATION_SQL, obs_a1)
                conn.rollback()
                assert exc_info.value.sqlstate == "42501"

        # Attempt B: Connected as Bank A, setting bank_id='BANK_A', worker_id='BANK_B' (worker impersonation)
        obs_a2 = helper_build_observation_tuple(
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            bank_id="BANK_A",
            worker_id="BANK_B",
        )
        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(INSERT_OBSERVATION_SQL, obs_a2)
                conn.rollback()
                assert exc_info.value.sqlstate == "42501"

        # Attempt C: Connected as Bank B, setting bank_id='BANK_B', worker_id='BANK_C'
        obs_b1 = helper_build_observation_tuple(
            job_id=job_id,
            run_id=run_id,
            sim_id=sim_id,
            bank_id="BANK_B",
            worker_id="BANK_C",
        )
        with psycopg.connect(bank_b_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_info:
                    cur.execute(INSERT_OBSERVATION_SQL, obs_b1)
                conn.rollback()
                assert exc_info.value.sqlstate == "42501"

        # Application-layer verification: TrustedTrainingJobRecordSource rejects bank_id spoofing
        record_source_a = TrustedTrainingJobRecordSource(
            worker_token=BANK_TOKENS["BANK_A"],
            bank_tokens={"BANK_A": BANK_TOKENS["BANK_A"]},
            db_url=bank_a_url,
            enforce_lifecycle=True,
            app_env="production",
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            record_source_a.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=100,
                observed_loss=0.05,
                worker_token=BANK_TOKENS["BANK_A"],
                bank_id="BANK_B",
            )

    def test_bankdb_07_bank_a_cannot_mutate_or_improperly_read_bank_b_evidence(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-07: Bank A cannot update, delete, or improperly read Bank B's protected observation evidence."""
        admin_url = pg_roles["admin"]
        bank_a_url = pg_roles["worker_bank_a"]
        bank_b_url = pg_roles["worker_bank_b"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bdb07_{uid}"
        run_id = f"run_bdb07_{uid}"
        sim_id = f"sim_bdb07_{uid}"

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id)

        # 1. Bank A writes valid Bank A observation
        obs_a = helper_build_observation_tuple(job_id, run_id, sim_id, "BANK_A", "BANK_A")
        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(INSERT_OBSERVATION_SQL, obs_a)

        # 2. Bank B writes valid Bank B observation
        obs_b = helper_build_observation_tuple(job_id, run_id, sim_id, "BANK_B", "BANK_B")
        with psycopg.connect(bank_b_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(INSERT_OBSERVATION_SQL, obs_b)

        # 3. Bank A attempts unauthorized actions on Bank B's rows:
        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                # 3a. Read attempt on Bank B rows: RLS USING clause filters it out (0 rows returned)
                cur.execute("SELECT * FROM cfi_round_observations WHERE bank_id = 'BANK_B'")
                b_rows = cur.fetchall()
                assert len(b_rows) == 0, f"Bank A leaked Bank B rows: {b_rows}"

                # 3b. Read all rows: Only Bank A's rows must be visible
                cur.execute("SELECT bank_id FROM cfi_round_observations WHERE job_id = %s", (job_id,))
                all_visible = cur.fetchall()
                assert len(all_visible) == 1
                assert all_visible[0][0] == "BANK_A"

                # 3c. Update attempt on Bank B row: Must be denied with SQLSTATE 42501
                with pytest.raises(psycopg.Error) as exc_up:
                    cur.execute(
                        "UPDATE cfi_round_observations SET actual_sample_count = 99999 WHERE bank_id = 'BANK_B'"
                    )
                conn.rollback()
                assert exc_up.value.sqlstate == "42501"

                # 3d. Delete attempt on Bank B row: Must be denied with SQLSTATE 42501
                with pytest.raises(psycopg.Error) as exc_del:
                    cur.execute("DELETE FROM cfi_round_observations WHERE bank_id = 'BANK_B'")
                conn.rollback()
                assert exc_del.value.sqlstate == "42501"

        # Verify Bank B's row remains intact and unmutated in PostgreSQL as admin
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT actual_sample_count FROM cfi_round_observations WHERE job_id = %s AND bank_id = 'BANK_B'",
                (job_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 100

    def test_bankdb_08_effective_grants_current_user_and_rls_inspection(
        self, pg_roles: dict[str, str]
    ) -> None:
        """BANKDB-08: Verify actual PostgreSQL current_user, effective grants, role membership, and RLS enforcement."""
        admin_url = pg_roles["admin"]

        # Verify each role's current_user matches the authenticated connection
        for role_key, expected_user in (
            ("worker_bank_a", "cfi_bank_a_role"),
            ("worker_bank_b", "cfi_bank_b_role"),
            ("worker_bank_c", "cfi_bank_c_role"),
            ("worker", "cfi_worker_role"),
            ("coordinator", "cfi_coord_role"),
            ("completion", "cfi_completion_role"),
        ):
            with psycopg.connect(pg_roles[role_key]) as conn, conn.cursor() as cur:
                cur.execute("SELECT current_user, session_user")
                c_user, s_user = cur.fetchone()  # type: ignore[misc]
                assert c_user == expected_user
                assert s_user == expected_user

        # Verify table privileges and RLS metadata using administrative connection
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            # Bank A role: can insert into round observations, cannot update or delete
            cur.execute("SELECT has_table_privilege('cfi_bank_a_role', 'cfi_round_observations', 'INSERT')")
            assert cur.fetchone()[0] is True  # type: ignore[index]
            cur.execute("SELECT has_table_privilege('cfi_bank_a_role', 'cfi_round_observations', 'UPDATE')")
            assert cur.fetchone()[0] is False  # type: ignore[index]
            cur.execute("SELECT has_table_privilege('cfi_bank_a_role', 'cfi_round_observations', 'DELETE')")
            assert cur.fetchone()[0] is False  # type: ignore[index]

            # Generic worker role: CANNOT insert into round observations
            cur.execute("SELECT has_table_privilege('cfi_worker_role', 'cfi_round_observations', 'INSERT')")
            assert cur.fetchone()[0] is False  # type: ignore[index]

            # Verify RLS is enabled AND forced on cfi_round_observations
            cur.execute(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = 'cfi_round_observations'
                """
            )
            rls_enabled, rls_forced = cur.fetchone()  # type: ignore[misc]
            assert rls_enabled is True
            assert rls_forced is True

        # Verify role-switching bypass attempts fail with SQLSTATE 42501
        with psycopg.connect(pg_roles["worker_bank_a"]) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error) as exc_switch1:
                    cur.execute("SET ROLE cfi_bank_b_role")
                conn.rollback()
                assert exc_switch1.value.sqlstate == "42501"

                with pytest.raises(psycopg.Error) as exc_switch2:
                    cur.execute("SET ROLE postgres")
                conn.rollback()
                assert exc_switch2.value.sqlstate == "42501"

    def test_bankdb_09_rejected_observations_prevent_trusted_completion(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """BANKDB-09: Invalid/rejected observations leave no authoritative evidence and cannot contribute to trusted completion."""
        admin_url = pg_roles["admin"]
        bank_a_url = pg_roles["worker_bank_a"]
        comp_url = pg_roles["completion"]
        issuer_url = pg_roles["issuer"]

        uid = uuid.uuid4().hex[:8]
        job_id = f"job_bdb09_{uid}"
        run_id = f"run_bdb09_{uid}"
        sim_id = f"sim_bdb09_{uid}"
        art_sha = hashlib.sha256(b"artifact_bdb09").hexdigest()

        helper_setup_prerequisite_job(admin_url, job_id, run_id, sim_id)

        # 1. Bank A records legitimate observation for round 1
        obs_a = helper_build_observation_tuple(job_id, run_id, sim_id, "BANK_A", "BANK_A", round_number=1)
        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(INSERT_OBSERVATION_SQL, obs_a)

        # 2. Bank A attempts to forge Bank B's observation for round 1 -> rejected
        obs_forged_b = helper_build_observation_tuple(job_id, run_id, sim_id, "BANK_B", "BANK_B", round_number=1)
        with psycopg.connect(bank_a_url) as conn:
            conn.autocommit = False
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error):
                    cur.execute(INSERT_OBSERVATION_SQL, obs_forged_b)
                conn.rollback()

        # 3. Start completion service and attempt finalization
        comp_vault_dir = str(tmp_path / "comp_vault_bdb09")
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
            # Must fail closed: missing Bank B and Bank C observations
            with pytest.raises(TrainingProvenanceSecurityError, match="Executed rounds mismatch|no verified execution observations"):
                client.finalize_training_completion(
                    job_id=job_id,
                    executed_rounds=2,
                    executed_epochs=2,
                    sample_count=450,
                    observed_metrics={"loss": 0.05},
                    artifact_sha256=art_sha,
                    coordinator_token=COORD_SECRET,
                )
        finally:
            comp_proc.terminate()
            comp_proc.join(timeout=3)

        # 4. Verify cfi_completed_jobs has ZERO records
        with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM cfi_completed_jobs WHERE job_id = %s", (job_id,))
            assert cur.fetchone()[0] == 0  # type: ignore[index]

        # 5. Verify issuer refuses to sign receipt
        dev_seed = hashlib.sha256(b"seed_bdb09").digest().hex()
        issuer_vault_dir = str(tmp_path / "issuer_vault_bdb09")
        issuer_key_file, _ = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)
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
                    artifact_bytes=b"dummy_model_bytes",
                    caller_token=COORD_SECRET,
                )
        finally:
            signing_auth.terminate()

    def test_bankdb_10_genuine_three_bank_two_round_federation_succeeds(
        self, pg_roles: dict[str, str], tmp_path
    ) -> None:
        """BANKDB-10: Genuine three-bank, two-round federated training succeeds using only corrected bank-specific permissions."""
        admin_url = pg_roles["admin"]
        comp_url = pg_roles["completion"]
        coord_url = pg_roles["coordinator"]
        issuer_url = pg_roles["issuer"]

        # Setup completion authority
        comp_vault_dir = str(tmp_path / "comp_vault_bdb10")
        comp_vault_file = provision_isolated_completion_authority_vault(comp_vault_dir, comp_url)
        comp_proc, comp_endpoint = spawn_isolated_completion_authority_service(
            vault_path=comp_vault_file,
            coordinator_token=COORD_SECRET,
            app_env="production",
        )

        # Setup bank datasets and storage vaults
        vault_root = str(tmp_path / "bank_vaults_bdb10")
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

        # Spawn workers under dedicated bank database URLs
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

        # Setup receipt signing authority
        dev_seed = hashlib.sha256(b"cfi_dev_ed25519_training_issuer_seed_v1").digest().hex()
        issuer_vault_dir = str(tmp_path / "issuer_vault_bdb10")
        issuer_key_file, _ = provision_isolated_issuer_vault(issuer_vault_dir, signing_key_hex=dev_seed)
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
            job_id = f"job_bdb10_{uid}"
            run_id = f"run_bdb10_{uid}"
            sim_id = f"sim_bdb10_{uid}"
            registry_dir = str(tmp_path / "model_reg_bdb10")

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
            assert result["receipt"].job_id == job_id
            assert result["saved_version"]["is_production_eligible"] is True
            assert result["saved_version"]["provenance_type"] == "TRAINING_RECORDED"
            assert result["saved_version"]["status"] == "inactive"
            assert result["saved_version"]["is_active"] is False

            # Verify in PostgreSQL: exactly 6 observations (3 banks * 2 rounds)
            with psycopg.connect(admin_url) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT bank_id, round_number FROM cfi_round_observations WHERE job_id = %s ORDER BY round_number, bank_id",
                    (job_id,),
                )
                obs_rows = cur.fetchall()
                assert len(obs_rows) == 6
                expected_pairs = [
                    ("BANK_A", 1), ("BANK_B", 1), ("BANK_C", 1),
                    ("BANK_A", 2), ("BANK_B", 2), ("BANK_C", 2),
                ]
                assert [(r[0], r[1]) for r in obs_rows] == expected_pairs
        finally:
            coord_client.terminate()
            signing_auth.terminate()
            comp_proc.terminate()
            comp_proc.join(timeout=3)
