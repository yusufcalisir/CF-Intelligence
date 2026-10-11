"""Canonical Execution Accounting Test Suite (AC-01 to AC-10).

Verifies strict derivation of execution accounting quantities:
- AC-01: Zero executed epochs despite nonzero observed epochs -> rejected.
- AC-02: Zero observed epochs but a positive completion claim -> rejected.
- AC-03: Claim total bank epochs when canonical requires sequential global epochs -> rejected.
- AC-04: Claim last-round epochs when canonical requires full-run epochs -> rejected.
- AC-05: Claim planned epochs rather than observed epochs -> rejected.
- AC-06: Substitute sample exposures for canonical sample count -> rejected.
- AC-07: Change dataset sample counts across rounds -> rejected.
- AC-08: Retry a bank contribution without double-counting samples -> handled accurately.
- AC-09: Include a failed bank contribution in sample or epoch accounting -> rejected.
- AC-10: Verify correct canonical counts for two real global rounds with three banks -> verified.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest

from app.application.services.training_provenance_service import (
    TrainingProvenanceSecurityError,
    TrustedTrainingJobRecordSource,
)

COORD_SECRET = "coord_secret_ac_accounting_production_991"
BANK_A_SECRET = "bank_a_secret_ac_accounting_alpha_112"
BANK_B_SECRET = "bank_b_secret_ac_accounting_beta_223"
BANK_C_SECRET = "bank_c_secret_ac_accounting_gamma_334"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


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


class TestExecutionAccountingCanonical:
    """Rigorous verification of canonical execution accounting contracts."""

    def _setup_job_with_observations(
        self,
        source: TrustedTrainingJobRecordSource,
        job_id: str,
        run_id: str,
        rounds: int = 1,
        epochs_per_worker: int = 1,
        samples_per_worker: int = 100,
        include_eval: bool = True,
        artifact_digest: str = "art_sha_" + "0" * 56,
    ) -> None:
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ac",
            coordinator_token=COORD_SECRET,
            expected_rounds=rounds,
            expected_epochs=rounds * epochs_per_worker,
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        for r in range(1, rounds + 1):
            digests = []
            for b_id, s_token in (("BANK_A", BANK_A_SECRET), ("BANK_B", BANK_B_SECRET), ("BANK_C", BANK_C_SECRET)):
                u_digest = f"upd_{b_id}_r{r}"
                digests.append(u_digest)
                source.record_round_observation(
                    job_id=job_id,
                    round_number=r,
                    sample_count=samples_per_worker,
                    actual_executed_epochs=epochs_per_worker,
                    observed_loss=0.25,
                    worker_token=s_token,
                    bank_id=b_id,
                    model_update_digest=u_digest,
                )
            r_digest = f"agg_r{r}_" + "1" * 57 if r < rounds else artifact_digest
            source.record_global_aggregation(
                job_id=job_id,
                round_number=r,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A", "BANK_B", "BANK_C"],
                accepted_local_update_digests=digests,
                aggregated_global_model_digest=r_digest,
                coordinator_token=COORD_SECRET,
            )

        if include_eval:
            source.record_evaluation_observation(
                job_id=job_id,
                metric_name="loss",
                metric_value=0.25,
                evaluated_artifact_digest=artifact_digest,
                coordinator_token=COORD_SECRET,
            )

    def test_ac_01_zero_executed_epochs_with_nonzero_observed_epochs_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AC-01: Zero executed epochs despite nonzero observed epochs is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac01_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac01_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac01_" + "a" * 55

        self._setup_job_with_observations(source, job_id, run_id, rounds=1, epochs_per_worker=1, artifact_digest=art_sha)

        with pytest.raises(TrainingProvenanceSecurityError, match="Executed epochs mismatch|executed_epochs must be > 0"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=0,  # Zero claim
                sample_count=300,
                observed_metrics={"loss": 0.25},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ac_02_positive_epochs_with_zero_observed_epochs_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AC-02: Zero observed epochs but a positive completion claim is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac02_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac02_{uuid.uuid4().hex[:8]}"

        # Schedule and start job but record no observations
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ac02",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="has no verified completed round observation|no verified execution observations recorded",
        ):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=2,
                sample_count=100,
                observed_metrics={"loss": 0.1},
                artifact_sha256="sha_ac02_" + "b" * 55,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ac_03_claim_total_worker_epochs_when_canonical_requires_sequential_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AC-03: Claiming sum of all workers' local epochs (3) instead of global sequential epochs (1) is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac03_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac03_" + "c" * 55

        # 1 round with 3 workers, each doing 1 epoch -> Canonical executed_epochs = 1 (not 3)
        self._setup_job_with_observations(source, job_id, run_id, rounds=1, epochs_per_worker=1, artifact_digest=art_sha)

        with pytest.raises(TrainingProvenanceSecurityError, match="Executed epochs mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=3,  # Total worker epochs (rejected)
                sample_count=300,
                observed_metrics={"loss": 0.25},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ac_04_claim_last_round_epochs_when_canonical_requires_full_run_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AC-04: Claiming only last-round epochs (1) when 2 rounds were executed (canonical = 2) is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac04_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac04_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac04_" + "d" * 55

        # 2 rounds, 1 epoch per round -> Canonical executed_epochs = 2
        self._setup_job_with_observations(source, job_id, run_id, rounds=2, epochs_per_worker=1, artifact_digest=art_sha)

        with pytest.raises(TrainingProvenanceSecurityError, match="Executed epochs mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=2,
                executed_epochs=1,  # Claiming only round 2's epochs (rejected)
                sample_count=300,
                observed_metrics={"loss": 0.25},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ac_05_claim_planned_epochs_rather_than_observed_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AC-05: Claiming planned epochs (10) when only 1 round of 1 epoch was observed is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac05_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac05_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac05_" + "e" * 55

        self._setup_job_with_observations(source, job_id, run_id, rounds=1, epochs_per_worker=1, artifact_digest=art_sha)

        with pytest.raises(TrainingProvenanceSecurityError, match="Executed epochs mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=10,  # Planned/scheduled, not observed
                sample_count=300,
                observed_metrics={"loss": 0.25},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ac_06_substitute_sample_exposures_for_canonical_sample_count_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AC-06: Substituting sample exposures (600 = 300 * 2 rounds) for canonical sample count (300) is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac06_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac06_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac06_" + "f" * 55

        # 2 rounds, 100 samples per worker (300 total unique bank samples)
        self._setup_job_with_observations(source, job_id, run_id, rounds=2, epochs_per_worker=1, samples_per_worker=100, artifact_digest=art_sha)

        with pytest.raises(TrainingProvenanceSecurityError, match="Repeated sample exposures cannot substitute for unique dataset samples"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=2,
                executed_epochs=2,
                sample_count=600,  # 300 * 2 = 600 exposures (rejected)
                observed_metrics={"loss": 0.25},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ac_07_changing_dataset_sample_counts_across_rounds_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AC-07: Bank changing partition sample size across rounds without manifest update is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac07_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac07_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac07_" + "7" * 55

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ac07",
            coordinator_token=COORD_SECRET,
            expected_rounds=2,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Round 1: Bank A reports 100 samples
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="upd_a_r1",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["upd_a_r1"],
            aggregated_global_model_digest="agg_r1_digest_" + "1" * 50,
            coordinator_token=COORD_SECRET,
        )

        # Round 2: Bank A unexpectedly changes sample count to 250!
        source.record_round_observation(
            job_id=job_id,
            round_number=2,
            sample_count=250,
            actual_executed_epochs=1,
            observed_loss=0.2,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="upd_a_r2",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=2,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["upd_a_r2"],
            aggregated_global_model_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.2,
            evaluated_artifact_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Inconsistent sample count observed for bank 'BANK_A'|Sample count mismatch across rounds",
        ):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=2,
                executed_epochs=2,
                sample_count=250,
                observed_metrics={"loss": 0.2},
                artifact_sha256=art_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ac_08_retry_bank_contribution_without_double_counting(
        self, real_pg_instance: str
    ) -> None:
        """AC-08: Retrying a bank contribution replaces the attempt and does not double-count samples."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac08_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac08_" + "8" * 55

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ac08",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Attempt 1: Failed
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            attempt_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.9,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="upd_a_failed",
            execution_status="FAILED",
        )
        # Attempt 2: Successful retry
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            attempt_number=2,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="upd_a_retry",
            execution_status="COMPLETED",
        )

        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["upd_a_retry"],
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

        # Completion should accurately count 100 samples (not 200)
        rec = source.complete_training_job(
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
        assert rec.sample_count == 100

    def test_ac_09_failed_bank_contribution_excluded_from_accounting(
        self, real_pg_instance: str
    ) -> None:
        """AC-09: Including a failed bank contribution in epoch or sample accounting is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac09_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac09_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ac09",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Bank A succeeds, Bank B fails
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="upd_a",
            execution_status="COMPLETED",
        )
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.9,
            worker_token=BANK_B_SECRET,
            bank_id="BANK_B",
            model_update_digest="upd_b_fail",
            execution_status="FAILED",
        )

        # Quorum requirement for 2 workers fails
        with pytest.raises(TrainingProvenanceSecurityError, match="has no verified completed round observation|Quorum failure"):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A", "BANK_B"],
                accepted_local_update_digests=["upd_a", "upd_b_fail"],
                aggregated_global_model_digest="agg_sha_" + "9" * 56,
                coordinator_token=COORD_SECRET,
            )

    def test_ac_10_two_round_three_bank_canonical_accounting_verified(
        self, real_pg_instance: str
    ) -> None:
        """AC-10: Exact verification of canonical counts for 2 real global rounds across 3 banks."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ac10_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ac10_{uuid.uuid4().hex[:8]}"
        art_sha = "sha_ac10_final_" + "a" * 49

        # Bank A: 120 samples, Bank B: 150 samples, Bank C: 180 samples -> Unique = 450
        # 2 rounds, 1 epoch per round -> Sequential global epochs = 2
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ac10",
            coordinator_token=COORD_SECRET,
            expected_rounds=2,
            expected_epochs=2,
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        bank_samples = {"BANK_A": 120, "BANK_B": 150, "BANK_C": 180}
        for r in (1, 2):
            digests = []
            for b_id, s_token in (("BANK_A", BANK_A_SECRET), ("BANK_B", BANK_B_SECRET), ("BANK_C", BANK_C_SECRET)):
                d = f"digest_{b_id}_round{r}"
                digests.append(d)
                source.record_round_observation(
                    job_id=job_id,
                    round_number=r,
                    sample_count=bank_samples[b_id],
                    actual_executed_epochs=1,
                    observed_loss=0.3 / r,
                    worker_token=s_token,
                    bank_id=b_id,
                    model_update_digest=d,
                )
            r_digest = "agg_r1_sha_" + "1" * 53 if r == 1 else art_sha
            source.record_global_aggregation(
                job_id=job_id,
                round_number=r,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A", "BANK_B", "BANK_C"],
                accepted_local_update_digests=digests,
                aggregated_global_model_digest=r_digest,
                coordinator_token=COORD_SECRET,
            )

        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.15,
            evaluated_artifact_digest=art_sha,
            coordinator_token=COORD_SECRET,
        )

        completed = source.complete_training_job(
            job_id=job_id,
            executed_rounds=2,
            executed_epochs=2,
            sample_count=450,
            observed_metrics={"loss": 0.15},
            artifact_sha256=art_sha,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        assert completed.executed_rounds == 2
        assert completed.executed_epochs == 2
        assert completed.sample_count == 450
        assert completed.status == "COMPLETED"
        assert completed.artifact_sha256 == art_sha
