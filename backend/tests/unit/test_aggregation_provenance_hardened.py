"""Aggregation Provenance and Update Lineage Suite (AG-01 to AG-12).

Verifies authoritative aggregation evidence and tamper-proof model lineage:
- AG-01: Missing observation digest is rejected.
- AG-02: Empty digest substituted for actual digest is rejected.
- AG-03: Observed digest differs from received update bytes is rejected.
- AG-04: Coordinator lowers quorum below scheduled contract is rejected.
- AG-05: One bank omitted despite required-participant contract is rejected.
- AG-06: Duplicate bank contribution is rejected.
- AG-07: Replay of earlier round update is rejected.
- AG-08: Reference to update from another job is rejected.
- AG-09: Substitution of unrelated final global model bytes is rejected.
- AG-10: Attempt completion without aggregation evidence is rejected.
- AG-11: Claim that evaluation applies to different model bytes is rejected.
- AG-12: Valid two-round federation independently verifies update-to-artifact identity chain.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Generator

import pytest

from app.application.services.training_provenance_service import (
    TrainingProvenanceSecurityError,
    TrustedTrainingJobRecordSource,
)

COORD_SECRET = "coord_secret_ag_provenance_production_991"
BANK_A_SECRET = "bank_a_secret_ag_provenance_alpha_112"
BANK_B_SECRET = "bank_b_secret_ag_provenance_beta_223"
BANK_C_SECRET = "bank_c_secret_ag_provenance_gamma_334"

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


class TestAggregationProvenanceHardened:
    """Security verification of aggregation provenance and cryptographic model lineage."""

    def test_ag_01_missing_observation_digest_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-01: Recording round observation with missing or None model update digest is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag01_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag01_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag01",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="model_update_digest is mandatory|model_update_digest cannot be empty|cannot be empty",
        ):
            source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=100,
                worker_token=BANK_A_SECRET,
                bank_id="BANK_A",
                model_update_digest="",  # Empty
            )

    def test_ag_02_empty_digest_substituted_in_aggregation_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-02: Supplying empty digest in accepted_local_update_digests is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag02_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag02_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag02",
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
            model_update_digest="real_update_digest_a",
        )

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Accepted local update digest for participant 'BANK_A' is missing or empty|cannot be empty",
        ):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=[""],  # Empty substitution
                aggregated_global_model_digest="02" * 32,
                coordinator_token=COORD_SECRET,
            )

    def test_ag_03_observed_digest_mismatch_with_received_bytes_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-03: Supplying digest that does not match the bank's observed digest is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag03_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag03_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag03",
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
            model_update_digest="authentic_sha_from_bank_a",
        )

        # Coordinator attempts to aggregate a tampered digest
        with pytest.raises(TrainingProvenanceSecurityError, match="does not match observed digest"):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=["tampered_sha_from_attacker"],
                aggregated_global_model_digest="agg_digest_03",
                coordinator_token=COORD_SECRET,
            )

    def test_ag_04_coordinator_cannot_lower_quorum_below_scheduled_contract(
        self, real_pg_instance: str
    ) -> None:
        """AG-04: Coordinator attempting to reduce quorum below immutable scheduled contract is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag04_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag04_{uuid.uuid4().hex[:8]}"

        # Scheduled contract: 3 permitted workers (BANK_A, BANK_B, BANK_C), quorum = 3
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag04",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Only Bank A observes
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_a_04",
        )

        # Coordinator attempts to lower quorum by aggregating with only Bank A
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Quorum failure for job .* requires at least 3 participants|Quorum failure",
        ):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=["digest_a_04"],
                aggregated_global_model_digest="04" * 32,
                coordinator_token=COORD_SECRET,
            )

    def test_ag_05_omitting_required_bank_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-05: Omitting a required participant bank from aggregation is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag05_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag05_{uuid.uuid4().hex[:8]}"

        # Scheduled with 3 required banks
        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag05",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Bank A and B observe
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_a_05",
        )
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_B_SECRET,
            bank_id="BANK_B",
            model_update_digest="digest_b_05",
        )

        # Aggregation attempts to omit Bank C
        with pytest.raises(TrainingProvenanceSecurityError, match="Quorum failure|missing required participants"):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A", "BANK_B"],
                accepted_local_update_digests=["digest_a_05", "digest_b_05"],
                aggregated_global_model_digest="agg_05_omitted",
                coordinator_token=COORD_SECRET,
            )

    def test_ag_06_duplicate_bank_contribution_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-06: Supplying duplicate bank entries in accepted_participant_ids is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag06_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag06_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag06",
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
            model_update_digest="digest_a_06",
        )

        # Attacker supplies BANK_A twice
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="Duplicate participant ID in accepted aggregation|Duplicate participant 'BANK_A' in aggregation",
        ):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A", "BANK_A"],
                accepted_local_update_digests=["digest_a_06", "digest_a_06"],
                aggregated_global_model_digest="06" * 32,
                coordinator_token=COORD_SECRET,
            )

    def test_ag_07_replay_earlier_round_update_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-07: Replaying an update digest from an earlier round in a later round is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag07_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag07_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag07",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
            expected_rounds=2,
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Round 1
        source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_round1",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_round1"],
            aggregated_global_model_digest="07" * 32,
            coordinator_token=COORD_SECRET,
        )

        # Round 2: Attacker replays round 1 digest instead of a newly trained round 2 digest
        source.record_round_observation(
            job_id=job_id,
            round_number=2,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_round1",  # Replay!
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Replay of earlier round update digest .* rejected"):
            source.record_global_aggregation(
                job_id=job_id,
                round_number=2,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=["digest_round1"],
                aggregated_global_model_digest="07_r2" * 16,
                coordinator_token=COORD_SECRET,
            )

    def test_ag_08_reference_update_from_another_job_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-08: Referencing an update digest recorded under a different job is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_1 = f"job_ag08_1_{uuid.uuid4().hex[:8]}"
        job_2 = f"job_ag08_2_{uuid.uuid4().hex[:8]}"

        # Job 1 setup and observation
        source.schedule_training_job(
            job_id=job_1,
            training_run_id=f"run_1_{uuid.uuid4().hex[:8]}",
            simulation_id="sim_ag08",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_1, worker_token=BANK_A_SECRET)
        source.record_round_observation(
            job_id=job_1,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.3,
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest="digest_exclusive_job1",
        )

        # Job 2 setup
        source.schedule_training_job(
            job_id=job_2,
            training_run_id=f"run_2_{uuid.uuid4().hex[:8]}",
            simulation_id="sim_ag08",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A"],
        )
        source.start_training_job(job_id=job_2, worker_token=BANK_A_SECRET)

        # Job 2 attempts to aggregate using Job 1's update digest
        with pytest.raises(TrainingProvenanceSecurityError, match="has no verified completed round observation"):
            source.record_global_aggregation(
                job_id=job_2,
                round_number=1,
                aggregation_algorithm="fed_avg_weighted",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=["digest_exclusive_job1"],
                aggregated_global_model_digest="agg_08_stolen",
                coordinator_token=COORD_SECRET,
            )

    def test_ag_09_substitute_unrelated_final_model_bytes_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-09: Final completion artifact SHA-256 differing from last accepted aggregation digest is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag09_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag09_{uuid.uuid4().hex[:8]}"
        legit_agg_digest = "sha_legit_agg_" + "9" * 50

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag09",
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
            model_update_digest="digest_ag09",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_ag09"],
            aggregated_global_model_digest=legit_agg_digest,
            coordinator_token=COORD_SECRET,
        )
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.3,
            evaluated_artifact_digest=legit_agg_digest,
            coordinator_token=COORD_SECRET,
        )

        # Attacker tries to complete job claiming a completely different model SHA-256
        tampered_artifact_sha = "sha_tampered_model_bytes_" + "x" * 38
        with pytest.raises(TrainingProvenanceSecurityError, match="Artifact SHA-256 does not match final accepted aggregation digest"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.3},
                artifact_sha256=tampered_artifact_sha,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ag_10_attempt_completion_without_aggregation_evidence_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-10: Attempting completion without recorded global aggregation events is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag10_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag10_{uuid.uuid4().hex[:8]}"

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag10",
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
            model_update_digest="digest_ag10",
        )

        # Skipping record_global_aggregation!
        with pytest.raises(TrainingProvenanceSecurityError, match="missing global aggregation records"):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.3},
                artifact_sha256="some_sha_" + "0" * 55,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ag_11_evaluation_applied_to_different_artifact_digest_rejected(
        self, real_pg_instance: str
    ) -> None:
        """AG-11: Evaluation metric evaluated against a different model digest than the aggregated model is rejected."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag11_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag11_{uuid.uuid4().hex[:8]}"
        legit_agg_digest = "sha_legit_agg_11_" + "1" * 47

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag11",
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
            model_update_digest="digest_ag11",
        )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=["digest_ag11"],
            aggregated_global_model_digest=legit_agg_digest,
            coordinator_token=COORD_SECRET,
        )

        # Attacker attempts to record evaluation observation referencing an unaggregated model digest
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match=r"Evaluation forgery: evaluated artifact digest .* does not match any authentic global aggregation",
        ):
            source.record_evaluation_observation(
                job_id=job_id,
                metric_name="val_loss",
                metric_value=0.3,
                evaluated_artifact_digest="2" * 64,
                coordinator_token=COORD_SECRET,
            )

        with pytest.raises(
            TrainingProvenanceSecurityError,
            match=r"has no matching trusted evaluation event|without verified model evaluation observations",
        ):
            source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.3, "val_loss": 0.3},
                artifact_sha256=legit_agg_digest,
                worker_id="FEDERATED_COORDINATOR",
                coordinator_token=COORD_SECRET,
                enforce_lifecycle=True,
            )

    def test_ag_12_valid_two_round_federation_full_lineage_chain_verified(
        self, real_pg_instance: str
    ) -> None:
        """AG-12: Complete valid two-round federation independently verifies update-to-artifact lineage chain."""
        source = TrustedTrainingJobRecordSource(
            coordinator_token=COORD_SECRET,
            bank_tokens=BANK_TOKENS,
            db_url=real_pg_instance,
            enforce_lifecycle=True,
            app_env="production",
        )
        job_id = f"job_ag12_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ag12_{uuid.uuid4().hex[:8]}"
        round1_model_digest = hashlib.sha256(b"global_weights_round_1_model_state").hexdigest()
        round2_model_digest = hashlib.sha256(b"global_weights_round_2_final_state").hexdigest()

        source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_ag12",
            coordinator_token=COORD_SECRET,
            permitted_workers=["BANK_A", "BANK_B", "BANK_C"],
            expected_rounds=2,
            expected_epochs=2,
        )
        source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Round 1 execution
        r1_digests = [
            hashlib.sha256(f"w_{b}_r1".encode()).hexdigest() for b in ("BANK_A", "BANK_B", "BANK_C")
        ]
        for b_id, s_token, d in zip(("BANK_A", "BANK_B", "BANK_C"), (BANK_A_SECRET, BANK_B_SECRET, BANK_C_SECRET), r1_digests):
            source.record_round_observation(
                job_id=job_id,
                round_number=1,
                sample_count=100,
                actual_executed_epochs=1,
                observed_loss=0.35,
                worker_token=s_token,
                bank_id=b_id,
                model_update_digest=d,
            )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A", "BANK_B", "BANK_C"],
            accepted_local_update_digests=r1_digests,
            aggregated_global_model_digest=round1_model_digest,
            coordinator_token=COORD_SECRET,
        )

        # Round 2 execution
        r2_digests = [
            hashlib.sha256(f"w_{b}_r2".encode()).hexdigest() for b in ("BANK_A", "BANK_B", "BANK_C")
        ]
        for b_id, s_token, d in zip(("BANK_A", "BANK_B", "BANK_C"), (BANK_A_SECRET, BANK_B_SECRET, BANK_C_SECRET), r2_digests):
            source.record_round_observation(
                job_id=job_id,
                round_number=2,
                sample_count=100,
                actual_executed_epochs=1,
                observed_loss=0.20,
                worker_token=s_token,
                bank_id=b_id,
                model_update_digest=d,
            )
        source.record_global_aggregation(
            job_id=job_id,
            round_number=2,
            aggregation_algorithm="fed_avg_weighted",
            accepted_participant_ids=["BANK_A", "BANK_B", "BANK_C"],
            accepted_local_update_digests=r2_digests,
            aggregated_global_model_digest=round2_model_digest,
            coordinator_token=COORD_SECRET,
        )

        # Holdout evaluation
        source.record_evaluation_observation(
            job_id=job_id,
            metric_name="loss",
            metric_value=0.20,
            evaluated_artifact_digest=round2_model_digest,
            coordinator_token=COORD_SECRET,
        )

        completed = source.complete_training_job(
            job_id=job_id,
            executed_rounds=2,
            executed_epochs=2,
            sample_count=300,
            observed_metrics={"loss": 0.20},
            artifact_sha256=round2_model_digest,
            worker_id="FEDERATED_COORDINATOR",
            coordinator_token=COORD_SECRET,
            enforce_lifecycle=True,
        )

        assert completed.status == "COMPLETED"
        assert completed.artifact_sha256 == round2_model_digest
        assert completed.executed_rounds == 2
        assert completed.executed_epochs == 2
