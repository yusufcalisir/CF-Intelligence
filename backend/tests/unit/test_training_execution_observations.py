"""Adversarial Verification Suite: Training Execution Observations & Lifecycle Reconciliation.

Covers adversarial requirements ADV-01 through ADV-07:
- ADV-01: Schedule and start without observations; attempt completion.
- ADV-02: Record one completed round; claim ten.
- ADV-03: Record 100 samples; claim 100,000.
- ADV-04: Record one loss; claim another.
- ADV-05: Modify scheduled feature order at completion.
- ADV-06: Worker tries to schedule an unauthorized job.
- ADV-07: Coordinator tries to fabricate worker execution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.training_provenance_service import (
    TrainingProvenanceSecurityError,
    TrustedTrainingJobRecordSource,
    reset_default_training_job_record_source,
)


class TestTrainingExecutionObservationsAdversarial:
    """Adversarial test suite enforcing observation-derived completion and strict workload role boundaries."""

    def setup_method(self) -> None:
        reset_default_training_job_record_source()

    def teardown_method(self) -> None:
        reset_default_training_job_record_source()

    def test_adv_01_schedule_and_start_without_observations_completion_rejected(self, tmp_path: Path) -> None:
        """ADV-01: Schedule and start without observations; attempt completion. Must fail closed."""
        db_path = str(tmp_path / "adv_01.db")
        source = TrustedTrainingJobRecordSource(
            store_path=db_path,
            configured_token="worker_secret_12345678901234567890",
            coordinator_token="coordinator_secret_12345678901234567890",
            enforce_lifecycle=True,
        )

        job_id = "job_adv_01"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_01",
            simulation_id="sim_adv_01",
            expected_rounds=5,
            coordinator_token="coordinator_secret_12345678901234567890",
        )
        source.start_training_job(
            job_id=job_id,
            worker_token="worker_secret_12345678901234567890",
        )

        # Attacker attempts completion without any recorded observations
        with pytest.raises(TrainingProvenanceSecurityError, match="no verified execution observations recorded"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=5,
                sample_count=1000,
                observed_metrics={"loss": 0.25},
                artifact_sha256="a" * 64,
                worker_token="worker_secret_12345678901234567890",
                enforce_lifecycle=True,
            )

    def test_adv_02_record_one_round_claim_ten_rejected(self, tmp_path: Path) -> None:
        """ADV-02: Record one completed round; claim ten. Must fail closed."""
        db_path = str(tmp_path / "adv_02.db")
        source = TrustedTrainingJobRecordSource(
            store_path=db_path,
            configured_token="worker_secret_12345678901234567890",
            coordinator_token="coordinator_secret_12345678901234567890",
            enforce_lifecycle=True,
        )

        job_id = "job_adv_02"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_02",
            simulation_id="sim_adv_02",
            expected_rounds=1,
            coordinator_token="coordinator_secret_12345678901234567890",
        )
        source.start_training_job(
            job_id=job_id,
            worker_token="worker_secret_12345678901234567890",
        )

        # Record exactly 1 valid round observation
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=500,
            observed_loss=0.30,
            worker_token="worker_secret_12345678901234567890",
        )

        # Attacker attempts to complete claiming 10 rounds
        with pytest.raises(TrainingProvenanceSecurityError, match="Executed rounds mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=10,
                sample_count=500,
                observed_metrics={"loss": 0.30},
                artifact_sha256="b" * 64,
                worker_token="worker_secret_12345678901234567890",
                enforce_lifecycle=True,
            )

    def test_adv_03_record_100_samples_claim_100k_rejected(self, tmp_path: Path) -> None:
        """ADV-03: Record 100 samples; claim 100,000. Must fail closed."""
        db_path = str(tmp_path / "adv_03.db")
        source = TrustedTrainingJobRecordSource(
            store_path=db_path,
            configured_token="worker_secret_12345678901234567890",
            coordinator_token="coordinator_secret_12345678901234567890",
            enforce_lifecycle=True,
        )

        job_id = "job_adv_03"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_03",
            simulation_id="sim_adv_03",
            expected_rounds=1,
            coordinator_token="coordinator_secret_12345678901234567890",
        )
        source.start_training_job(
            job_id=job_id,
            worker_token="worker_secret_12345678901234567890",
        )

        # Record 100 observed samples
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            observed_loss=0.45,
            worker_token="worker_secret_12345678901234567890",
        )

        # Attacker claims 100,000 samples
        with pytest.raises(TrainingProvenanceSecurityError, match="Sample count mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                sample_count=100000,
                observed_metrics={"loss": 0.45},
                artifact_sha256="c" * 64,
                worker_token="worker_secret_12345678901234567890",
                enforce_lifecycle=True,
            )

    def test_adv_04_record_one_loss_claim_another_rejected(self, tmp_path: Path) -> None:
        """ADV-04: Record one loss; claim another. Must fail closed."""
        db_path = str(tmp_path / "adv_04.db")
        source = TrustedTrainingJobRecordSource(
            store_path=db_path,
            configured_token="worker_secret_12345678901234567890",
            coordinator_token="coordinator_secret_12345678901234567890",
            enforce_lifecycle=True,
        )

        job_id = "job_adv_04"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_04",
            simulation_id="sim_adv_04",
            expected_rounds=1,
            coordinator_token="coordinator_secret_12345678901234567890",
        )
        source.start_training_job(
            job_id=job_id,
            worker_token="worker_secret_12345678901234567890",
        )

        # Record loss of 0.45
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            bank_id="BANK_A",
            sample_count=500,
            observed_loss=0.45,
            model_update_digest="d" * 64,
            worker_token="worker_secret_12345678901234567890",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["d" * 64],
            aggregated_global_model_digest="d" * 64,
            coordinator_token="coordinator_secret_12345678901234567890",
        )

        # Attacker claims fabricated loss of 0.05
        with pytest.raises(TrainingProvenanceSecurityError, match="Observed loss mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                sample_count=500,
                observed_metrics={"loss": 0.05},
                artifact_sha256="d" * 64,
                worker_token="worker_secret_12345678901234567890",
                enforce_lifecycle=True,
            )

    def test_adv_05_modify_scheduled_feature_order_at_completion_rejected(self, tmp_path: Path) -> None:
        """ADV-05: Modify scheduled feature order at completion. Must fail closed."""
        db_path = str(tmp_path / "adv_05.db")
        source = TrustedTrainingJobRecordSource(
            store_path=db_path,
            configured_token="worker_secret_12345678901234567890",
            coordinator_token="coordinator_secret_12345678901234567890",
            enforce_lifecycle=True,
        )

        job_id = "job_adv_05"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_05",
            simulation_id="sim_adv_05",
            expected_rounds=1,
            feature_names=list(FEATURE_NAMES),
            coordinator_token="coordinator_secret_12345678901234567890",
        )
        source.start_training_job(
            job_id=job_id,
            worker_token="worker_secret_12345678901234567890",
        )
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=500,
            observed_loss=0.35,
            worker_token="worker_secret_12345678901234567890",
        )

        # Permute feature names
        reversed_features = list(reversed(FEATURE_NAMES))
        with pytest.raises(TrainingProvenanceSecurityError, match="Feature names mismatch"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                sample_count=500,
                observed_metrics={"loss": 0.35},
                artifact_sha256="e" * 64,
                feature_names=reversed_features,
                worker_token="worker_secret_12345678901234567890",
                enforce_lifecycle=True,
            )

    def test_adv_06_worker_tries_to_schedule_unauthorized_job_rejected(self, tmp_path: Path) -> None:
        """ADV-06: Worker attempts to schedule an unauthorized job. Must fail closed."""
        db_path = str(tmp_path / "adv_06.db")
        source = TrustedTrainingJobRecordSource(
            store_path=db_path,
            configured_token="worker_secret_12345678901234567890",
            coordinator_token="coordinator_secret_12345678901234567890",
        )

        # Worker presents worker credential to schedule_training_job
        with pytest.raises(TrainingProvenanceSecurityError, match="Worker credential cannot authorize job scheduling"):
            source.schedule_training_job(
                job_id="job_adv_06_unauth",
                training_run_id="run_adv_06",
                simulation_id="sim_adv_06",
                coordinator_token="worker_secret_12345678901234567890",
            )

    def test_adv_07_coordinator_tries_to_fabricate_worker_execution_rejected(self, tmp_path: Path) -> None:
        """ADV-07: Coordinator attempts to fabricate worker execution events. Must fail closed."""
        db_path = str(tmp_path / "adv_07.db")
        source = TrustedTrainingJobRecordSource(
            store_path=db_path,
            configured_token="worker_secret_12345678901234567890",
            coordinator_token="coordinator_secret_12345678901234567890",
        )

        job_id = "job_adv_07"
        source.schedule_training_job(
            job_id=job_id,
            training_run_id="run_adv_07",
            simulation_id="sim_adv_07",
            coordinator_token="coordinator_secret_12345678901234567890",
        )

        # 1. Coordinator attempts to start job as a worker
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker"):
            source.start_training_job(
                job_id=job_id,
                worker_token="coordinator_secret_12345678901234567890",
            )

        # Start job with valid worker token
        source.start_training_job(
            job_id=job_id,
            worker_token="worker_secret_12345678901234567890",
        )

        # 2. Coordinator attempts to submit round observation
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker"):
            source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=100,
                observed_loss=0.5,
                worker_token="coordinator_secret_12345678901234567890",
            )

        # 3. Coordinator attempts to complete job
        with pytest.raises(TrainingProvenanceSecurityError, match="Coordinator credential cannot authorize worker"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                sample_count=100,
                observed_metrics={"loss": 0.5},
                artifact_sha256="f" * 64,
                worker_token="coordinator_secret_12345678901234567890",
            )
