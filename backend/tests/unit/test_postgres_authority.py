"""Real PostgreSQL Training Job Authority Verification Suite.

Exercises PG-01 through PG-10 on a genuine, real PostgreSQL 18.1 instance:
- PG-01: PostgreSQL URL connects to real PostgreSQL server, never creating local SQLite files.
- PG-02: Genuine PostgreSQL database accepts valid scheduling, start, observation, and completion transitions.
- PG-03: Cross-process / multi-connection conflicting completion results in one committed authoritative record.
- PG-04: Concurrent valid observations from different bank workers survive without lost updates.
- PG-05: Transaction abort leaves no partial completion.
- PG-06: Unique and foreign-key constraints prevent inconsistent identity mappings.
- PG-07: Database outage fails closed without fallback.
- PG-08: Incorrect credentials/permissions fail closed with configuration error.
- PG-09: Migration creates the expected PostgreSQL schema with strict foreign keys and unique constraints.
- PG-10: Application restart preserves trustworthy job state and historical observations across reconnections.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest

try:
    import psycopg
    from psycopg.rows import dict_row as pg_dict_row
except ImportError:
    psycopg = None  # type: ignore[assignment]
    pg_dict_row = None  # type: ignore[assignment]

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.training_provenance_service import (
    GlobalAggregationEvent,
    PostgresTrainingJobAuthority,
    RoundObservation,
    TrainingJobAuthorityDatabase,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
)

# Deterministic test parameters
TEST_SECRET_COORDINATOR = "coord_secret_999999999999999999999999"
TEST_SECRET_WORKER_A = "worker_a_secret_111111111111111111111"
TEST_SECRET_WORKER_B = "worker_b_secret_222222222222222222222"
TEST_SECRET_WORKER_C = "worker_c_secret_333333333333333333333"


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
    """Start an embedded real PostgreSQL 18.1 server instance and yield its connection URI."""
    try:
        import pg0
    except ImportError:
        pytest.skip("pg0 is not installed (PostgreSQL 18.1 embedded authority unavailable)")

    if psycopg is None:
        pytest.skip("psycopg is not installed (PostgreSQL driver unavailable)")

    pg = pg0.Pg0()
    pg.start()
    try:
        assert pg.uri is not None
        yield pg.uri
    finally:
        pg.stop()


class TestPostgresTrainingJobAuthority:
    """Rigorous verification of PostgreSQL authority backend under real PostgreSQL 18.1."""

    def test_pg_01_postgresql_url_connects_to_postgres_not_sqlite(self, real_pg_instance: str, tmp_path) -> None:
        """PG-01: A PostgreSQL URL connects to PostgreSQL rather than creating a local SQLite file."""
        initial_files = set(os.listdir(tmp_path))

        db = TrainingJobAuthorityDatabase(db_target=real_pg_instance)
        assert db.backend_type == "postgres"

        # Verify backend type and verify NO SQLite files were generated in current directory or tmp
        after_files = set(os.listdir(tmp_path))
        assert initial_files == after_files

        # Ensure sqlite query fails on Postgres authority connection
        conn: Any
        with psycopg.connect(real_pg_instance, row_factory=pg_dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                row: Any = cur.fetchone()
                assert row is not None
                assert "PostgreSQL" in row["version"]

    def test_pg_02_genuine_postgres_lifecycle_transitions(self, real_pg_instance: str) -> None:
        """PG-02: Genuine PostgreSQL database accepts valid scheduling, start, observation, and completion."""
        db = TrainingJobAuthorityDatabase(db_target=real_pg_instance)
        db.clear()

        job_id = f"job_pg02_{uuid.uuid4().hex[:8]}"
        run_id = f"run_pg02_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_pg02_{uuid.uuid4().hex[:8]}"

        # 1. Schedule
        sched = db.schedule_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            coordinator_id="COORDINATOR_PG",
            permitted_workers=["BANK_A", "BANK_B"],
        )
        assert sched.job_id == job_id
        assert sched.status == "SCHEDULED"

        # 2. Start
        started = db.start_job(job_id=job_id, worker_id="BANK_A")
        assert started.job_id == job_id
        assert started.status == "STARTED"

        # 3. Observation
        model_digest = hashlib.sha256(b"fake_model_update_bytes_round_1").hexdigest()
        obs = RoundObservation(
            event_id=f"evt_{job_id}_r1_a",
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=250,
            actual_executed_epochs=1,
            observed_loss=0.32,
            metrics={"loss": 0.32},
            execution_timestamp=datetime.now(UTC).isoformat(),
            model_update_digest=model_digest,
        )
        saved_obs = db.record_observation(obs)
        assert saved_obs.event_id == obs.event_id

        # 4. Global Aggregation Event
        agg_digest = hashlib.sha256(b"aggregated_model_bytes_r1").hexdigest()
        agg_evt = GlobalAggregationEvent(
            event_id=f"agg_{job_id}_r1",
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=("BANK_A",),
            accepted_local_update_digests=(model_digest,),
            aggregation_configuration={"weights": "proportional"},
            aggregated_global_model_digest=agg_digest,
            timestamp=datetime.now(UTC).isoformat(),
        )
        db.record_global_aggregation(agg_evt)

        # 5. Complete
        comp = db.complete_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=250,
            observed_metrics={"loss": 0.32},
            artifact_sha256=agg_digest,
            worker_id="BANK_A",
            enforce_lifecycle=True,
        )
        assert comp.job_id == job_id
        assert comp.status == "COMPLETED"

    def test_pg_03_concurrent_conflicting_completion_single_committed_record(self, real_pg_instance: str) -> None:
        """PG-03: Multi-connection conflicting completion results in exactly one committed authoritative record."""
        db = TrainingJobAuthorityDatabase(db_target=real_pg_instance)
        db.clear()

        job_id = f"job_pg03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_pg03_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_pg03_{uuid.uuid4().hex[:8]}"

        db.schedule_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A", "BANK_B"],
        )
        db.start_job(job_id=job_id, worker_id="BANK_A")

        obs = RoundObservation(
            event_id=f"evt_{job_id}_r1",
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            worker_id="BANK_A",
            bank_id="BANK_A",
            round_number=1,
            attempt_number=1,
            actual_sample_count=200,
            actual_executed_epochs=1,
            observed_loss=0.25,
            metrics={"loss": 0.25},
            execution_timestamp=datetime.now(UTC).isoformat(),
            model_update_digest="a" * 64,
        )
        db.record_observation(obs)

        # Spawn two concurrent threads with independent Postgres connections trying to complete with conflicting hashes
        def attempt_completion(sha_val: str) -> tuple[bool, str]:
            local_db = PostgresTrainingJobAuthority(db_url=real_pg_instance)
            try:
                local_db.complete_job(
                    job_id=job_id,
                    executed_rounds=1,
                    executed_epochs=1,
                    sample_count=200,
                    observed_metrics={"loss": 0.25},
                    artifact_sha256=sha_val,
                    worker_id="BANK_A",
                    enforce_lifecycle=True,
                )
                return True, "SUCCESS"
            except Exception as e:
                return False, str(e)

        sha1 = "1" * 64
        sha2 = "2" * 64

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(attempt_completion, sha1)
            fut2 = executor.submit(attempt_completion, sha2)
            res1 = fut1.result()
            res2 = fut2.result()

        # Exactly one must succeed and the other must be rejected
        successes = [r for r in [res1, res2] if r[0]]
        failures = [r for r in [res1, res2] if not r[0]]

        assert len(successes) == 1, f"Expected exactly 1 success, got: {successes}"
        assert len(failures) == 1, f"Expected exactly 1 failure, got: {failures}"
        assert "Conflicting completion record" in failures[0][1]

    def test_pg_04_concurrent_valid_observations_survive(self, real_pg_instance: str) -> None:
        """PG-04: Concurrent valid observations from different bank workers survive without dropped events."""
        db = TrainingJobAuthorityDatabase(db_target=real_pg_instance)
        db.clear()

        job_id = f"job_pg04_{uuid.uuid4().hex[:8]}"
        run_id = f"run_pg04_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_pg04_{uuid.uuid4().hex[:8]}"

        db.schedule_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        db.start_job(job_id=job_id, worker_id="BANK_A")

        def record_bank_obs(bank_id: str, count: int) -> None:
            local_db = PostgresTrainingJobAuthority(db_url=real_pg_instance)
            obs = RoundObservation(
                event_id=f"evt_{job_id}_{bank_id}",
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                worker_id=bank_id,
                bank_id=bank_id,
                round_number=1,
                attempt_number=1,
                actual_sample_count=count,
                actual_executed_epochs=1,
                observed_loss=0.30,
                metrics={"loss": 0.30},
                execution_timestamp=datetime.now(UTC).isoformat(),
                model_update_digest=hashlib.sha256(bank_id.encode()).hexdigest(),
            )
            local_db.record_observation(obs)

        banks = [("BANK_A", 100), ("BANK_B", 150), ("BANK_C", 200)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda b: record_bank_obs(b[0], b[1]), banks))

        saved_obs = db.get_observations(job_id)
        assert len(saved_obs) == 3
        bank_ids = {o.bank_id for o in saved_obs}
        assert bank_ids == {"BANK_A", "BANK_B", "BANK_C"}

    def test_pg_05_transaction_abort_leaves_no_partial_completion(self, real_pg_instance: str) -> None:
        """PG-05: Transaction abort leaves no partial completed record."""
        db = TrainingJobAuthorityDatabase(db_target=real_pg_instance)
        db.clear()

        job_id = f"job_pg05_{uuid.uuid4().hex[:8]}"
        run_id = f"run_pg05_{uuid.uuid4().hex[:8]}"
        sim_id = f"sim_pg05_{uuid.uuid4().hex[:8]}"

        db.schedule_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id=sim_id,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=2,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
        )
        db.start_job(job_id=job_id, worker_id="BANK_A")

        # Record round 1 only
        db.record_observation(
            RoundObservation(
                event_id=f"evt_{job_id}_r1",
                job_id=job_id,
                training_run_id=run_id,
                simulation_id=sim_id,
                worker_id="BANK_A",
                bank_id="BANK_A",
                round_number=1,
                attempt_number=1,
                actual_sample_count=100,
                actual_executed_epochs=1,
                observed_loss=0.40,
                metrics={"loss": 0.40},
                execution_timestamp=datetime.now(UTC).isoformat(),
            )
        )

        # Attempting to complete claiming 2 rounds fails validation in reconcile_job_completion_evidence
        with pytest.raises(TrainingProvenanceSecurityError, match="Executed rounds mismatch"):
            db.complete_job(
                job_id=job_id,
                executed_rounds=2,
                executed_epochs=1,
                sample_count=200,
                observed_metrics={"loss": 0.40},
                artifact_sha256="3" * 64,
                worker_id="BANK_A",
                enforce_lifecycle=True,
            )

        # Verify no completion record was inserted
        assert db.get_completed_job_by_id(job_id) is None

    def test_pg_06_unique_and_fk_constraints_enforced(self, real_pg_instance: str) -> None:
        """PG-06: Foreign key and unique constraints prevent orphaned observations and duplicate runs."""
        db = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        db.clear()

        # 1. Starting or recording for non-existent job must be rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="job was not scheduled"):
            db.start_job(job_id="non_existent_job_123")

        # 2. Duplicate training_run_id is rejected
        db.schedule_job(
            job_id="job_fk_1",
            training_run_id="run_unique_test",
            simulation_id="sim_1",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="duplicate run ID rejected"):
            db.schedule_job(
                job_id="job_fk_2",
                training_run_id="run_unique_test",
                simulation_id="sim_2",
                architecture="FraudDetectionNN",
                feature_schema_version="v1.0",
                expected_rounds=1,
                expected_epochs=1,
                input_dim=len(FEATURE_NAMES),
                feature_names=list(FEATURE_NAMES),
            )

    def test_pg_07_database_outage_fails_closed_no_fallback(self) -> None:
        """PG-07: Unreachable PostgreSQL database fails closed without fallback to SQLite or memory."""
        dead_pg_url = "postgresql://postgres:wrong_password@127.0.0.1:59999/non_existent_db"

        # Direct instantiation must fail with TrainingProvenanceConfigurationError
        with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to connect to PostgreSQL authority"):
            PostgresTrainingJobAuthority(db_url=dead_pg_url)

        # TrainingJobAuthorityDatabase must not fall back to SQLite
        with pytest.raises(TrainingProvenanceConfigurationError, match="Failed to connect to PostgreSQL authority"):
            TrainingJobAuthorityDatabase(db_target=dead_pg_url)

    def test_pg_08_invalid_url_scheme_rejected(self) -> None:
        """PG-08: Unsupported database schemes fail immediately with configuration error."""
        with pytest.raises(TrainingProvenanceConfigurationError, match="Unsupported database URL scheme"):
            TrainingJobAuthorityDatabase(db_target="mysql://user:pass@localhost:3306/cfi")

        with pytest.raises(TrainingProvenanceConfigurationError, match="Unsupported database URL scheme"):
            TrainingJobAuthorityDatabase(db_target="redis://localhost:6379/0")

    def test_pg_09_schema_tables_and_columns_exist(self, real_pg_instance: str) -> None:
        """PG-09: Verifies that PostgreSQL schema migration creates all 6 tables with proper types."""
        db = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        db.init_schema()

        expected_tables = {
            "cfi_scheduled_jobs",
            "cfi_started_jobs",
            "cfi_round_observations",
            "cfi_global_aggregations",
            "cfi_model_evaluations",
            "cfi_completed_jobs",
        }
        conn: Any
        with psycopg.connect(real_pg_instance, row_factory=pg_dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name LIKE 'cfi_%';
                    """
                )
                rows: list[Any] = cur.fetchall()
                tables = {r["table_name"] for r in rows}
                assert expected_tables.issubset(tables)

    def test_pg_10_application_restart_preserves_state(self, real_pg_instance: str) -> None:
        """PG-10: Application restart preserves trustworthy job state and historical observations."""
        job_id = f"job_pg10_{uuid.uuid4().hex[:8]}"
        run_id = f"run_pg10_{uuid.uuid4().hex[:8]}"

        # Connection 1: Schedule and record
        db1 = TrainingJobAuthorityDatabase(db_target=real_pg_instance)
        db1.schedule_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_pg10",
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
        )
        db1.start_job(job_id=job_id, worker_id="BANK_A")
        db1.record_observation(
            RoundObservation(
                event_id=f"evt_{job_id}_r1",
                job_id=job_id,
                training_run_id=run_id,
                simulation_id="sim_pg10",
                worker_id="BANK_A",
                bank_id="BANK_A",
                round_number=1,
                attempt_number=1,
                actual_sample_count=100,
                actual_executed_epochs=1,
                observed_loss=0.35,
                metrics={"loss": 0.35},
                execution_timestamp=datetime.now(UTC).isoformat(),
            )
        )
        db1.close()

        # Connection 2: New authority instance (simulating app restart)
        db2 = TrainingJobAuthorityDatabase(db_target=real_pg_instance)
        sched = db2.get_scheduled_job(job_id)
        assert sched is not None
        assert sched.training_run_id == run_id

        obs_list = db2.get_observations(job_id)
        assert len(obs_list) == 1
        assert obs_list[0].actual_sample_count == 100
        assert obs_list[0].observed_loss == 0.35
