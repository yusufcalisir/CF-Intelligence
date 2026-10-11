"""Test Bank Workload Identity and Anti-Impersonation Guarantees (ID-01 through ID-08).

Verifies Module 01-A.1 Requirement P2 against genuine PostgreSQL 18.1 in production mode:
- ID-01: Bank A authenticates as Bank A.
- ID-02: Bank A credential cannot authenticate as Bank B.
- ID-03: Worker credential cannot schedule jobs.
- ID-04: Coordinator credential cannot submit bank training observations.
- ID-05: Bank A cannot record an event for a Bank B-only assignment.
- ID-06: Unrecognized bank identity fails closed.
- ID-07: Caller-provided bank_id cannot override authenticated identity.
- ID-08: Replay of another bank's event fails.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.training_provenance_service import (
    TrustedTrainingJobRecordSource,
)
from app.infrastructure.database.training_authority_backends import (
    TrainingProvenanceSecurityError,
)

# Test-specific strong isolated credentials
COORD_SECRET = "coord_identity_secret_7719_production_grade"
BANK_A_SECRET = "bank_a_identity_secret_9941_alpha"
BANK_B_SECRET = "bank_b_identity_secret_8832_beta"
BANK_C_SECRET = "bank_c_identity_secret_6621_gamma"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
    """Start an embedded real PostgreSQL 18.1 server instance and yield its connection URI."""
    from pg0 import Pg0

    pg = Pg0()
    pg.start()
    try:
        assert pg.uri is not None
        yield pg.uri
    finally:
        pg.stop()


@pytest.fixture
def identity_source(real_pg_instance: str) -> TrustedTrainingJobRecordSource:
    """Create a record source configured with strict per-bank workload credentials backed by PostgreSQL."""
    source = TrustedTrainingJobRecordSource(
        coordinator_token=COORD_SECRET,
        bank_tokens=BANK_TOKENS,
        db_url=real_pg_instance,
        enforce_lifecycle=True,
        app_env="production",
    )
    source.clear()
    return source


class TestBankWorkloadIdentity:
    """Adversarial verification of workload identities and per-bank authorization boundaries."""

    def test_id_01_bank_a_authenticates_as_bank_a(self, identity_source: TrustedTrainingJobRecordSource) -> None:
        """ID-01: Bank A credential authenticates as Bank A without ambiguity."""
        auth_id = identity_source.authenticate_bank_worker(
            provided_token=BANK_A_SECRET,
            claimed_bank_id="BANK_A",
        )
        assert auth_id == "BANK_A"

        # Even if caller omits claimed_bank_id, credential derives authoritative identity
        derived_id = identity_source.authenticate_bank_worker(
            provided_token=BANK_A_SECRET,
            claimed_bank_id=None,
        )
        assert derived_id == "BANK_A"

    def test_id_02_bank_a_credential_cannot_authenticate_as_bank_b(
        self, identity_source: TrustedTrainingJobRecordSource
    ) -> None:
        """ID-02: Bank A credential cannot claim Bank B identity."""
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            identity_source.authenticate_bank_worker(
                provided_token=BANK_A_SECRET,
                claimed_bank_id="BANK_B",
            )

    def test_id_03_worker_cannot_schedule_jobs(
        self, identity_source: TrustedTrainingJobRecordSource
    ) -> None:
        """ID-03: Bank worker credentials cannot schedule jobs (coordinator privilege required)."""
        job_id = f"job_id03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_id03_{uuid.uuid4().hex[:8]}"

        for bank_name, bank_token in BANK_TOKENS.items():
            with pytest.raises(TrainingProvenanceSecurityError, match="Bank worker credential cannot authorize coordinator actions"):
                identity_source.schedule_training_job(
                    job_id=job_id,
                    training_run_id=run_id,
                    simulation_id="sim_01",
                    coordinator_token=bank_token,
                    input_dim=len(FEATURE_NAMES),
                    feature_names=list(FEATURE_NAMES),
                )

    def test_id_04_coordinator_cannot_submit_bank_observations(
        self, identity_source: TrustedTrainingJobRecordSource
    ) -> None:
        """ID-04: Coordinator credential cannot submit bank-local training observations."""
        job_id = f"job_id04_{uuid.uuid4().hex[:8]}"
        run_id = f"run_id04_{uuid.uuid4().hex[:8]}"

        identity_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A", "BANK_B", "BANK_C"),
        )
        identity_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker execution events"):
            identity_source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=100,
                metrics={"loss": 0.45},
                worker_token=COORD_SECRET,
                bank_id="BANK_A",
            )

    def test_id_05_bank_a_cannot_record_event_for_bank_b_only_job(
        self, identity_source: TrustedTrainingJobRecordSource
    ) -> None:
        """ID-05: Bank A cannot record observations for a job assigned exclusively to Bank B."""
        job_id = f"job_id05_{uuid.uuid4().hex[:8]}"
        run_id = f"run_id05_{uuid.uuid4().hex[:8]}"

        # Scheduled with permitted_workers only containing BANK_B
        identity_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            permitted_workers=("BANK_B",),
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
        )

        # Bank A trying to start the job must fail
        with pytest.raises(TrainingProvenanceSecurityError, match="not authorized to start job"):
            identity_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Start by Bank B
        identity_source.start_training_job(job_id=job_id, worker_token=BANK_B_SECRET)

        # Bank A trying to submit an observation must fail
        with pytest.raises(TrainingProvenanceSecurityError, match="not authorized to record observations for job"):
            identity_source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=100,
                metrics={"loss": 0.35},
                worker_token=BANK_A_SECRET,
                bank_id="BANK_A",
            )

    def test_id_06_unrecognized_bank_identity_fails_closed(
        self, identity_source: TrustedTrainingJobRecordSource
    ) -> None:
        """ID-06: Unknown or arbitrary worker tokens fail closed without authentication."""
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized bank worker"):
            identity_source.authenticate_bank_worker(
                provided_token="unrecognized_foreign_token_12345",
                claimed_bank_id="BANK_A",
            )

        with pytest.raises(TrainingProvenanceSecurityError, match="missing training worker authorization token"):
            identity_source.authenticate_bank_worker(
                provided_token="",
                claimed_bank_id="BANK_A",
            )

        with pytest.raises(TrainingProvenanceSecurityError, match="missing training worker authorization token"):
            identity_source.authenticate_bank_worker(
                provided_token=None,
                claimed_bank_id="BANK_A",
            )

    def test_id_07_caller_provided_bank_id_cannot_override_authenticated_identity(
        self, identity_source: TrustedTrainingJobRecordSource
    ) -> None:
        """ID-07: Passing bank_id='BANK_C' with Bank A's token cannot override identity to Bank C."""
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            identity_source.authenticate_bank_worker(
                provided_token=BANK_A_SECRET,
                claimed_bank_id="BANK_C",
            )

        job_id = f"job_id07_{uuid.uuid4().hex[:8]}"
        run_id = f"run_id07_{uuid.uuid4().hex[:8]}"

        identity_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            permitted_workers=("BANK_A", "BANK_B", "BANK_C"),
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
        )
        identity_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # In record_round_observation, passing Bank A's token with bank_id='BANK_C' is rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Identity spoofing rejected"):
            identity_source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=150,
                metrics={"loss": 0.38},
                worker_token=BANK_A_SECRET,
                bank_id="BANK_C",
            )

    def test_id_08_replay_of_another_banks_event_fails(
        self, identity_source: TrustedTrainingJobRecordSource
    ) -> None:
        """ID-08: Replaying an existing event ID with altered bank or content is rejected."""
        job_id = f"job_id08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_id08_{uuid.uuid4().hex[:8]}"

        identity_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            permitted_workers=("BANK_A", "BANK_B", "BANK_C"),
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
        )
        identity_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        fixed_event_id = f"evt_shared_{uuid.uuid4().hex[:8]}"

        # Bank A legitimately records observation with fixed_event_id
        obs_a = identity_source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            metrics={"loss": 0.42},
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            event_id=fixed_event_id,
        )
        assert obs_a.event_id == fixed_event_id

        # Bank B attempts to reuse the exact same event_id for its own submission
        with pytest.raises(TrainingProvenanceSecurityError, match="Duplicate event ID .* rejected"):
            identity_source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=120,
                metrics={"loss": 0.39},
                worker_token=BANK_B_SECRET,
                bank_id="BANK_B",
                event_id=fixed_event_id,
            )
