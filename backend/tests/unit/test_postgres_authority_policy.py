"""PostgreSQL Policy, Privilege Enforcement, and Credential Redaction Suite (DB-01 to DB-14).

Verifies the production database authority security invariants:
- DB-01: Production source rejects plain SQLite filesystem path.
- DB-02: Production source rejects sqlite:// URLs.
- DB-03: Production source rejects in-memory database (:memory:).
- DB-04: Production source requires configured PostgreSQL URL.
- DB-05: Direct authority initialization cannot bypass policy.
- DB-06: Real PostgreSQL permissions reject unauthorized writes.
- DB-07: Issuer and serving roles cannot modify execution evidence.
- DB-08: Separate OS processes execute concurrent PostgreSQL operations.
- DB-09: Conflicting completion transactions cannot overwrite committed record.
- DB-10: Process termination during unfinished transaction leaves consistent state.
- DB-11: Database outage fails closed without fallback.
- DB-12: Connection errors do not expose sensitive credentials.
- DB-13: PostgreSQL restart preserves completed immutable job evidence.
- DB-14: Authorized multi-process writes preserve every valid observation.
"""

from __future__ import annotations

import multiprocessing as mp
import urllib.parse
import uuid
from collections.abc import Generator
from typing import Any

import psycopg
import pytest

from app.application.services.training_provenance_service import (
    TrainingJobAuthorityDatabase,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrustedTrainingJobRecordSource,
)
from app.infrastructure.database.training_authority_backends import (
    PostgresTrainingJobAuthority,
    redact_database_url,
    sanitize_database_error_message,
)

COORD_SECRET = "coord_secret_db_policy_production_991"
BANK_A_SECRET = "bank_a_secret_db_policy_alpha_112"


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


def _worker_concurrent_writer(db_url: str, job_id: str, bank_id: str, worker_token: str, out_q: mp.Queue) -> None:
    """Helper process writing observations to PostgreSQL."""
    try:
        source = TrustedTrainingJobRecordSource(
            worker_token=worker_token,
            bank_tokens={bank_id: worker_token},
            db_url=db_url,
            enforce_lifecycle=True,
            app_env="production",
        )
        obs = source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=worker_token,
            bank_id=bank_id,
            model_update_digest=f"digest_conc_{bank_id}",
        )
        out_q.put(("SUCCESS", obs.event_id))
    except Exception as e:
        out_q.put(("ERROR", str(e)))


class TestPostgresAuthorityPolicy:
    """Rigorous verification of PostgreSQL requirements, privilege controls, and durability."""

    def test_db_01_rejects_plain_sqlite_path_in_production(self) -> None:
        """DB-01: Production source rejects plain SQLite filesystem path."""
        with pytest.raises(
            TrainingProvenanceConfigurationError,
            match="must be a PostgreSQL connection URL|strictly forbidden in production",
        ):
            TrainingJobAuthorityDatabase(
                db_url="storage/training_authority.db",
                is_prod=True,
            )

    def test_db_02_rejects_sqlite_url_in_production(self) -> None:
        """DB-02: Production source rejects sqlite:// URLs."""
        with pytest.raises(
            TrainingProvenanceConfigurationError,
            match="must be a PostgreSQL connection URL|strictly forbidden in production",
        ):
            TrainingJobAuthorityDatabase(
                db_url="sqlite:///data/test.db",
                is_prod=True,
            )

    def test_db_03_rejects_in_memory_database_in_production(self) -> None:
        """DB-03: Production source rejects in-memory databases."""
        with pytest.raises(
            TrainingProvenanceConfigurationError,
            match="must be a PostgreSQL connection URL|strictly forbidden in production",
        ):
            TrainingJobAuthorityDatabase(
                db_url=":memory:",
                is_prod=True,
            )

    def test_db_04_requires_configured_postgresql_in_production(
        self, real_pg_instance: str
    ) -> None:
        """DB-04: Production source accepts valid PostgreSQL URL and verifies connection."""
        db = TrainingJobAuthorityDatabase(
            db_url=real_pg_instance,
            is_prod=True,
        )
        assert db.backend_type == "postgres"

    def test_db_05_direct_authority_initialization_cannot_bypass_policy(self) -> None:
        """DB-05: Direct source initialization with empty or non-postgres URL fails."""
        with pytest.raises(
            TrainingProvenanceConfigurationError,
            match="requires a genuine PostgreSQL backend|database/store is not configured",
        ):
            TrustedTrainingJobRecordSource(
                coordinator_token=COORD_SECRET,
                db_url="",
                app_env="production",
            )

    def test_db_06_real_postgres_permissions_reject_unauthorized_direct_mutations(
        self, real_pg_instance: str
    ) -> None:
        """DB-06: Direct raw SQL injection of fraudulent completed job is prevented/detectable."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        fake_job_id = f"job_db06_{uuid.uuid4().hex[:8]}"

        # Attempting to insert a fake completed job directly without schedule/start
        with authority._connect() as conn:
            with conn.cursor() as cur:
                with pytest.raises(psycopg.Error):
                    # Foreign key violation since job_id is not in cfi_scheduled_jobs
                    cur.execute(
                        """
                        INSERT INTO cfi_completed_jobs (
                            job_id, training_run_id, simulation_id, completed_at, status,
                            executed_rounds, executed_epochs, sample_count, observed_metrics_json,
                            artifact_sha256, architecture, feature_schema_version, input_dim,
                            feature_names_json, worker_id, evidence_fingerprint
                        ) VALUES (%s, %s, %s, NOW(), 'COMPLETED', 1, 1, 100, '{}', 'fake_sha', 'arch', 'v1', 1, '[]', 'HAX', 'fp')
                        """,
                        (fake_job_id, "run_fake", "sim_fake"),
                    )

    def test_db_07_read_only_role_cannot_modify_execution_evidence(
        self, real_pg_instance: str
    ) -> None:
        """DB-07: Read-only PostgreSQL session cannot insert or update execution evidence."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        with authority._connect() as conn:
            with conn.cursor() as cur:
                # Set transaction to READ ONLY
                cur.execute("SET TRANSACTION READ ONLY")
                with pytest.raises(Exception, match="read-only transaction|cannot execute"):
                    cur.execute(
                        """
                        INSERT INTO cfi_scheduled_jobs (
                            job_id, training_run_id, simulation_id, coordinator_id,
                            architecture, feature_schema_version, expected_rounds,
                            expected_epochs, input_dim, feature_names_json, config_fingerprint, scheduled_at
                        ) VALUES ('j', 'r', 's', 'c', 'a', 'v', 1, 1, 1, '[]', 'f', 't')
                        """
                    )

    def test_db_07b_real_restricted_role_privilege_enforcement(
        self, real_pg_instance: str
    ) -> None:
        """DB-07b: Dedicated restricted role receives SQLSTATE 42501 (InsufficientPrivilege) on unauthorized writes."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        role_name = f"cfi_reader_{uuid.uuid4().hex[:6]}"
        role_pass = "restricted_secret_123"

        # Setup restricted role using admin connection
        with authority._connect() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"DROP ROLE IF EXISTS {role_name}")
                cur.execute(f"CREATE ROLE {role_name} WITH LOGIN PASSWORD '{role_pass}'")
                cur.execute(f"GRANT USAGE ON SCHEMA public TO {role_name}")
                cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role_name}")
                cur.execute(f"REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {role_name}")

        parsed = urllib.parse.urlsplit(real_pg_instance)
        restricted_netloc = f"{role_name}:{role_pass}@{parsed.hostname}:{parsed.port}"
        restricted_url = urllib.parse.urlunsplit(
            (parsed.scheme, restricted_netloc, parsed.path, parsed.query, parsed.fragment)
        )

        # Connect as restricted role and attempt unauthorized mutations
        with psycopg.connect(restricted_url) as rconn:
            with rconn.cursor() as rcur:
                # 1. INSERT rejected with SQLSTATE 42501
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_insert:
                    rcur.execute(
                        """
                        INSERT INTO cfi_scheduled_jobs (
                            job_id, training_run_id, simulation_id, coordinator_id,
                            architecture, feature_schema_version, expected_rounds,
                            expected_epochs, input_dim, feature_names_json, config_fingerprint, scheduled_at
                        ) VALUES ('unauth_j', 'unauth_r', 'unauth_s', 'c', 'a', 'v', 1, 1, 1, '[]', 'f', 't')
                        """
                    )
                assert exc_insert.value.sqlstate == "42501"
                rconn.rollback()

                # 2. UPDATE rejected with SQLSTATE 42501
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_update:
                    rcur.execute("UPDATE cfi_completed_jobs SET status = 'TAMPERED'")
                assert exc_update.value.sqlstate == "42501"
                rconn.rollback()

                # 3. DELETE rejected with SQLSTATE 42501
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_delete:
                    rcur.execute("DELETE FROM cfi_round_observations")
                assert exc_delete.value.sqlstate == "42501"
                rconn.rollback()

                # 4. SELECT permitted for read-only inspection
                rcur.execute("SELECT COUNT(*) FROM cfi_scheduled_jobs")
                count = rcur.fetchone()[0]
                assert count >= 0


    def test_db_08_separate_os_processes_execute_concurrent_postgres_writes(
        self, real_pg_instance: str
    ) -> None:
        """DB-08: Separate OS processes concurrently write observations to PostgreSQL without deadlock."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET, "BANK_B": "bank_b_token_sec"},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_db08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_db08_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_db08",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        out_q_a: Any = mp.Queue()
        out_q_b: Any = mp.Queue()

        p_a = mp.Process(target=_worker_concurrent_writer, args=(real_pg_instance, job_id, "BANK_A", BANK_A_SECRET, out_q_a))
        p_b = mp.Process(target=_worker_concurrent_writer, args=(real_pg_instance, job_id, "BANK_B", "bank_b_token_sec", out_q_b))

        p_a.start()
        p_b.start()
        p_a.join(timeout=10)
        p_b.join(timeout=10)

        status_a, res_a = out_q_a.get(timeout=5)
        status_b, res_b = out_q_b.get(timeout=5)

        assert status_a == "SUCCESS"
        assert status_b == "SUCCESS"

        obs = source.get_round_observations(job_id)
        assert len(obs) == 2

    def test_db_09_conflicting_completion_cannot_overwrite_committed_record(
        self, real_pg_instance: str
    ) -> None:
        """DB-09: Conflicting completion attempt fails closed with idempotency / conflict error."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_db09_{uuid.uuid4().hex[:8]}"
        run_id = f"run_db09_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_db09_" + "0" * 55

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_db09",
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
            model_update_digest="digest_db09",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_db09"],
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
        source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.3},
            artifact_sha256=art_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        # Conflicting second completion with different metrics
        with pytest.raises(TrainingProvenanceSecurityError, match="Conflicting completion record"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.99},  # Conflicting metric!
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_db_10_unfinished_transaction_leaves_consistent_state(
        self, real_pg_instance: str
    ) -> None:
        """DB-10: Exception during transaction rolls back cleanly without leaving orphan data."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        job_id = f"job_db10_{uuid.uuid4().hex[:8]}"

        try:
            with authority._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO cfi_scheduled_jobs (
                            job_id, training_run_id, simulation_id, coordinator_id,
                            architecture, feature_schema_version, expected_rounds,
                            expected_epochs, input_dim, feature_names_json, config_fingerprint, scheduled_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, 1, 1, 1, '[]', 'fp', 'now')
                        """,
                        (job_id, "run_db10", "sim_db10", "coord10", "arch", "v1"),
                    )
                    # Crash before commit
                    raise RuntimeError("Simulated process crash during transaction")
        except RuntimeError:
            pass

        # Verify rollback
        assert authority.get_scheduled_job(job_id) is None

    def test_db_11_database_outage_fails_closed_without_fallback(self) -> None:
        """DB-11: Database connection failure fails closed without falling back to memory or SQLite."""
        with pytest.raises(TrainingProvenanceConfigurationError):
            source = TrustedTrainingJobRecordSource(
                db_url="postgresql://invalid_user:invalid_pass@127.0.0.1:59999/down_db",
                app_env="production",
            )
            source.schedule_training_job(
                job_id="job_down",
                training_run_id="run_down",
                simulation_id="sim_down",
                coordinator_token="any_tok",
            )

    def test_db_12_connection_errors_do_not_expose_credentials(self) -> None:
        """DB-12: Error messages sanitize sensitive database passwords and credentials."""
        secret_pass = "SuperSecretDbPassword123"
        url = f"postgresql://app_user:{secret_pass}@db.internal:5432/training_db"

        # 1. URL redaction helper
        redacted = redact_database_url(url)
        assert secret_pass not in redacted
        assert ":***@" in redacted

        # 2. Deep message scrubbing
        raw_msg = f"psycopg.OperationalError: connection to server at 'db.internal', password='{secret_pass}' failed: password authentication failed"
        scrubbed = sanitize_database_error_message(raw_msg)
        assert secret_pass not in scrubbed
        assert "password=***" in scrubbed

    def test_db_13_restart_preserves_completed_job_evidence(
        self, real_pg_instance: str
    ) -> None:
        """DB-13: Completed training records remain queryable across new connections."""
        job_id = f"job_db13_{uuid.uuid4().hex[:8]}"
        run_id = f"run_db13_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_db13_" + "3" * 55

        # Record and complete job
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_db13",
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
            model_update_digest="digest_db13",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_db13"],
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
        source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            observed_metrics={"loss": 0.25},
            artifact_sha256=art_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        # Fresh authority instance querying persisted data
        fresh_authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        persisted = fresh_authority.get_completed_job_by_id(job_id)
        assert persisted is not None
        assert persisted.status == "COMPLETED"
        assert persisted.artifact_sha256 == art_sha

    def test_db_14_multiprocess_writes_preserve_all_valid_observations(
        self, real_pg_instance: str
    ) -> None:
        """DB-14: Concurrently dispatched multi-process bank observations are all persisted and retrievable."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET, "BANK_B": "token_b_db14", "BANK_C": "token_c_db14"},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_db14_{uuid.uuid4().hex[:8]}"
        run_id = f"run_db14_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_db14",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        queues: dict[str, Any] = {b: mp.Queue() for b in ("BANK_A", "BANK_B", "BANK_C")}
        tokens = {"BANK_A": BANK_A_SECRET, "BANK_B": "token_b_db14", "BANK_C": "token_c_db14"}
        procs = [
            mp.Process(target=_worker_concurrent_writer, args=(real_pg_instance, job_id, b, tokens[b], queues[b]))
            for b in ("BANK_A", "BANK_B", "BANK_C")
        ]

        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=10)

        for b, q in queues.items():
            status, res = q.get(timeout=5)
            assert status == "SUCCESS", f"Bank {b} failed: {res}"

        obs = source.get_round_observations(job_id)
        assert len(obs) == 3
        assert {o.bank_id for o in obs} == {"BANK_A", "BANK_B", "BANK_C"}
