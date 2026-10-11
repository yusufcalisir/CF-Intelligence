"""Lifecycle Enforcement Attack Suite (LC-01 to LC-07).

Verifies that production lifecycle invariants are non-bypassable:
- LC-01: Call production complete_training_job(enforce_lifecycle=False) for a nonexistent job.
- LC-02: Direct PostgreSQL authority complete_job with enforce_lifecycle=False fails without auto-creating schedule/start records.
- LC-03: Attempt completion with zero observed rounds and invented executed epochs.
- LC-04: Attempt completion with automatically created schedule/start state is rejected.
- LC-05: Attempt to issue an Ed25519 receipt from any fabricated completion record is rejected.
- LC-06: Construct an issuer with require_authenticated_job=False or enforce_job_records=False in production is rejected.
- LC-07: Use a valid coordinator credential to submit fabricated completion claims without actual observed execution is rejected.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingExecutionEvidence,
    TrainingProvenanceConfigurationError,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrustedTrainingJobRecordSource,
)
from app.infrastructure.database.training_authority_backends import (
    PostgresTrainingJobAuthority,
)

COORD_SECRET = "coord_secret_lc_attacks_production_991"
BANK_A_SECRET = "bank_a_secret_lc_attacks_alpha_112"


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


class TestLifecycleEnforcementAttacks:
    """Security verification of non-bypassable training lifecycle invariants."""

    def test_lc_01_complete_with_enforce_lifecycle_false_rejected_in_production(
        self, real_pg_instance: str
    ) -> None:
        """LC-01: Calling production complete_training_job(enforce_lifecycle=False) is strictly rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        fake_job_id = f"job_lc01_{uuid.uuid4().hex[:8]}"

        with pytest.raises(TrainingProvenanceSecurityError, match="enforce_lifecycle=False is forbidden in production"):
            source.complete_training_job(
                job_id=fake_job_id,
                executed_rounds=2,
                executed_epochs=2,
                sample_count=100,
                observed_metrics={"loss": 0.05},
                artifact_sha256="fake_sha_" + "a" * 56,
                worker_id="ATTACKER",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=False,
            )

        # Confirm no record was created
        assert source.get_completed_job_by_id(fake_job_id) is None

    def test_lc_02_direct_postgres_authority_complete_without_prior_lifecycle_fails(
        self, real_pg_instance: str
    ) -> None:
        """LC-02: Direct call to PostgreSQL authority complete_job with enforce_lifecycle=False fails without auto-creation."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        fake_job_id = f"job_lc02_{uuid.uuid4().hex[:8]}"

        with pytest.raises(TrainingProvenanceSecurityError, match="has not been scheduled|was not scheduled"):
            authority.complete_job(
                job_id=fake_job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=50,
                observed_metrics={"loss": 0.12},
                artifact_sha256="fake_sha_" + "b" * 55,
                worker_id="DIRECT_CALLER",
                enforce_lifecycle=False,
            )

        # Verify database has zero records for this job
        assert authority.get_completed_job_by_id(fake_job_id) is None

    def test_lc_03_completion_with_zero_observed_rounds_rejected(
        self, real_pg_instance: str
    ) -> None:
        """LC-03: Attempt completion with zero observed rounds and invented executed epochs is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_lc03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_lc03_{uuid.uuid4().hex[:8]}"

        # Schedule and start job, but record ZERO rounds
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_lc03",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="executed_rounds must be > 0|has no verified completed round observation|no verified execution observations recorded",
        ):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=0,
                executed_epochs=5,  # Invented
                sample_count=100,
                observed_metrics={"loss": 0.05},
                artifact_sha256="fake_sha_" + "c" * 55,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_lc_04_no_auto_created_schedule_or_start_state(
        self, real_pg_instance: str
    ) -> None:
        """LC-04: Compatibility mode cannot create synthetic schedule/started rows for unobserved jobs."""
        authority = PostgresTrainingJobAuthority(db_url=real_pg_instance)
        ghost_job_id = f"job_lc04_{uuid.uuid4().hex[:8]}"

        # Attempting completion for ghost job
        with pytest.raises(TrainingProvenanceSecurityError, match="has not been scheduled|was not scheduled"):
            authority.complete_job(
                job_id=ghost_job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.2},
                artifact_sha256="ghost_sha_" + "d" * 54,
                worker_id="FEDERATED_COORDINATOR",
                enforce_lifecycle=False,
            )

        # Inspect DB state: ensure no schedule or started rows were slipped in
        with authority._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM cfi_scheduled_jobs WHERE job_id = %s", (ghost_job_id,))
                row1 = cur.fetchone()
                assert (row1["cnt"] if isinstance(row1, dict) else row1[0]) == 0
                cur.execute("SELECT COUNT(*) AS cnt FROM cfi_started_jobs WHERE job_id = %s", (ghost_job_id,))
                row2 = cur.fetchone()
                assert (row2["cnt"] if isinstance(row2, dict) else row2[0]) == 0

    def test_lc_05_receipt_issuance_from_fabricated_completion_rejected(
        self, real_pg_instance: str
    ) -> None:
        """LC-05: Attempting to issue an Ed25519 receipt from fabricated completion claims is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        fake_job_id = f"job_lc05_{uuid.uuid4().hex[:8]}"
        priv_key = ed25519.Ed25519PrivateKey.generate()
        issuer = TrainingReceiptIssuer(
            private_key=priv_key,
            job_record_source=source,
            enforce_job_records=True,
            app_env="production",
        )

        import hashlib

        fake_model_bytes = b"fake_model_bytes_lc05"
        model_sha = hashlib.sha256(fake_model_bytes).hexdigest()

        fabricated_evidence = TrainingExecutionEvidence(
            training_run_id=f"run_lc05_{uuid.uuid4().hex[:8]}",
            job_id=fake_job_id,
            simulation_id="sim_lc05",
            completed_at="2026-10-11T12:00:00Z",
            status="COMPLETED",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=200,
            observed_metrics={"loss": 0.03},
            artifact_sha256=model_sha,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="FEDERATED_COORDINATOR",
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="has no authenticated execution record"):
            issuer.issue_receipt(evidence=fabricated_evidence, model_bytes=fake_model_bytes)

    def test_lc_06_issuer_disabling_job_records_rejected_in_production(
        self, real_pg_instance: str
    ) -> None:
        """LC-06: Constructing an issuer with require_authenticated_job=False or enforce_job_records=False in production fails."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )

        # In production, require_authenticated_job=False is forbidden
        with pytest.raises(TrainingProvenanceConfigurationError, match="require_authenticated_job=False is forbidden in production"):
            TrainingReceiptIssuer(
                job_record_source=source,
                require_authenticated_job=False,
                app_env="production",
            )

        with pytest.raises(TrainingProvenanceConfigurationError, match="require_authenticated_job=False is forbidden in production"):
            TrainingReceiptIssuer(
                job_record_source=source,
                enforce_job_records=False,
                app_env="production",
            )

    def test_lc_07_coordinator_fabricated_claims_without_actual_observations_rejected(
        self, real_pg_instance: str
    ) -> None:
        """LC-07: Valid coordinator credential attempting completion without actual worker observations is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens={"BANK_A": BANK_A_SECRET},
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_lc07_{uuid.uuid4().hex[:8]}"
        run_id = f"run_lc07_{uuid.uuid4().hex[:8]}"

        # Valid scheduling and starting
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_lc07",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Attempt completion directly without any worker observations
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="has no verified completed round observation|no verified execution observations recorded",
        ):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.15},
                artifact_sha256="fake_sha_" + "7" * 55,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

        # Confirm job is NOT in completed state
        assert source.get_completed_job_by_id(job_id) is None
