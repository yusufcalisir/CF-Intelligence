"""Comprehensive Evidence Reconciliation and Provenance Chain Suite (EV-01 through EV-14).

Verifies Module 01-A.1 Requirements P3, P4, and P5:
- EV-01: Final loss is derived from trusted observations.
- EV-02: AUC reported by evaluation evidence matches actual model evaluation.
- EV-03: Altered AUC is rejected.
- EV-04: Altered validation loss is rejected.
- EV-05: Missing evaluation evidence is rejected for required metrics.
- EV-06: Executed epoch count reconciles.
- EV-07: Sample accounting follows explicitly documented semantics.
- EV-08: Quorum policy enforced.
- EV-09: Failed and retried contributions are handled without double counting.
- EV-10: Local update digest matches actual update bytes.
- EV-11: Global aggregation references only authorized, accepted update digests.
- EV-12: Final artifact digest matches the actual aggregated model.
- EV-13: Cross-round update replay fails.
- EV-14: Missing or inconsistent global-round evidence prevents completion.
"""

from __future__ import annotations

import hashlib
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

COORD_SECRET = "coord_secret_evidence_9921_production"
BANK_A_SECRET = "bank_a_secret_evidence_1142_alpha"
BANK_B_SECRET = "bank_b_secret_evidence_2253_beta"
BANK_C_SECRET = "bank_c_secret_evidence_3364_gamma"

BANK_TOKENS = {
    "BANK_A": BANK_A_SECRET,
    "BANK_B": BANK_B_SECRET,
    "BANK_C": BANK_C_SECRET,
}


@pytest.fixture(scope="module")
def real_pg_instance() -> Generator[str, None, None]:
    """Start embedded real PostgreSQL 18.1 instance and yield connection URI."""
    from pg0 import Pg0

    pg = Pg0()
    pg.start()
    try:
        assert pg.uri is not None
        yield str(pg.uri)
    finally:
        pg.stop()


@pytest.fixture
def evidence_source(real_pg_instance: str) -> TrustedTrainingJobRecordSource:
    """Create a TrustedTrainingJobRecordSource backed by real PostgreSQL 18.1 in production mode."""
    source = TrustedTrainingJobRecordSource(
        coordinator_token=COORD_SECRET,
        bank_tokens=BANK_TOKENS,
        db_url=real_pg_instance,
        enforce_lifecycle=True,
        app_env="production",
    )
    source.clear()
    return source


class TestEvidenceReconciliation:
    """Rigorous verification of the unbroken evidence chain from local updates to final artifact."""

    def test_ev_01_final_loss_derived_from_trusted_observations(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-01: Final completion loss must match observed training loss from verified rounds."""
        job_id = f"job_ev01_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev01_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        digest_a = hashlib.sha256(b"weights_a").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=200,
            actual_executed_epochs=1,
            observed_loss=0.345,
            metrics={"loss": 0.345},
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest=digest_a,
        )

        global_digest = hashlib.sha256(b"global_r1").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[digest_a],
            aggregated_global_model_digest=global_digest,
            coordinator_token=COORD_SECRET,
        )

        # Altered loss: 0.123 instead of observed 0.345
        with pytest.raises(TrainingProvenanceSecurityError, match="Observed loss mismatch"):
            evidence_source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=200,
                observed_metrics={"loss": 0.123},
                artifact_sha256=global_digest,
                worker_token=BANK_A_SECRET,
            )

        # Genuine loss: exactly matches observed loss
        record = evidence_source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=200,
            observed_metrics={"loss": 0.345},
            artifact_sha256=global_digest,
            worker_token=BANK_A_SECRET,
        )
        assert record.status == "COMPLETED"
        assert record.observed_metrics["loss"] == 0.345

    def test_ev_02_and_ev_03_auc_reported_matches_evaluation_and_altered_is_rejected(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-02 & EV-03: AUC in completion must originate from trusted evaluation; altered AUC is rejected."""
        job_id = f"job_ev02_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev02_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        digest_a = hashlib.sha256(b"weights_a_ev02").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=150,
            actual_executed_epochs=1,
            observed_loss=0.25,
            metrics={"loss": 0.25},
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest=digest_a,
        )

        global_digest = hashlib.sha256(b"global_ev02").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[digest_a],
            aggregated_global_model_digest=global_digest,
            coordinator_token=COORD_SECRET,
        )

        # Genuine evaluation observation recorded by coordinator
        evidence_source.record_evaluation_observation(
            job_id=job_id,
            metric_name="auc_roc",
            metric_value=0.892,
            evaluated_artifact_digest=global_digest,
            evaluator_identity="COORDINATOR",
            coordinator_token=COORD_SECRET,
        )

        # EV-03: Attempting to claim altered AUC (e.g. 0.950 instead of 0.892) is rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Evaluation metric 'auc_roc' value mismatch"):
            evidence_source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=150,
                observed_metrics={"loss": 0.25, "auc_roc": 0.950},
                artifact_sha256=global_digest,
                worker_token=BANK_A_SECRET,
            )

        # EV-02: Completion with genuine observed AUC 0.892 succeeds
        record = evidence_source.complete_training_job(
            job_id=job_id,
            executed_rounds=1,
            executed_epochs=1,
            sample_count=150,
            observed_metrics={"loss": 0.25, "auc_roc": 0.892},
            artifact_sha256=global_digest,
            worker_token=BANK_A_SECRET,
        )
        assert record.observed_metrics["auc_roc"] == 0.892

    def test_ev_04_altered_validation_loss_rejected(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-04: Altered validation loss is rejected when differing from recorded evaluation."""
        job_id = f"job_ev04_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev04_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        digest_a = hashlib.sha256(b"weights_a_ev04").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.30,
            metrics={"loss": 0.30},
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest=digest_a,
        )

        global_digest = hashlib.sha256(b"global_ev04").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[digest_a],
            aggregated_global_model_digest=global_digest,
            coordinator_token=COORD_SECRET,
        )

        evidence_source.record_evaluation_observation(
            job_id=job_id,
            metric_name="val_loss",
            metric_value=0.285,
            evaluated_artifact_digest=global_digest,
            coordinator_token=COORD_SECRET,
        )

        # Altered validation loss: 0.150 instead of 0.285
        with pytest.raises(TrainingProvenanceSecurityError, match="Evaluation metric 'val_loss' value mismatch"):
            evidence_source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.30, "val_loss": 0.150},
                artifact_sha256=global_digest,
                worker_token=BANK_A_SECRET,
            )

    def test_ev_05_missing_evaluation_evidence_rejected(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-05: Claiming an unobserved metric (e.g. pr_auc) without trusted evaluation is rejected."""
        job_id = f"job_ev05_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev05_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        digest_a = hashlib.sha256(b"weights_a_ev05").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=1,
            observed_loss=0.30,
            metrics={"loss": 0.30},
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest=digest_a,
        )

        global_digest = hashlib.sha256(b"global_ev05").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[digest_a],
            aggregated_global_model_digest=global_digest,
            coordinator_token=COORD_SECRET,
        )

        # Case 1: Claiming pr_auc when NO evaluation events exist at all
        with pytest.raises(TrainingProvenanceSecurityError, match="without verified model evaluation observations"):
            evidence_source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.30, "pr_auc": 0.85},
                artifact_sha256=global_digest,
                worker_token=BANK_A_SECRET,
            )

        # Record genuine evaluation observation for auc_roc
        evidence_source.record_evaluation_observation(
            job_id=job_id,
            metric_name="auc_roc",
            metric_value=0.88,
            evaluated_artifact_digest=global_digest,
            coordinator_token=COORD_SECRET,
        )

        # Case 2: Claiming unobserved pr_auc when auc_roc evaluation exists
        with pytest.raises(TrainingProvenanceSecurityError, match="has no matching trusted evaluation event"):
            evidence_source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=1,
                sample_count=100,
                observed_metrics={"loss": 0.30, "auc_roc": 0.88, "pr_auc": 0.85},
                artifact_sha256=global_digest,
                worker_token=BANK_A_SECRET,
            )

    def test_ev_06_executed_epoch_count_reconciliation(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-06: Claimed epochs must reconcile with observed local executed epochs."""
        job_id = f"job_ev06_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev06_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=3,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        digest_a = hashlib.sha256(b"weights_a_ev06").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id,
            round_number=1,
            sample_count=100,
            actual_executed_epochs=3,
            observed_loss=0.28,
            metrics={"loss": 0.28},
            worker_token=BANK_A_SECRET,
            bank_id="BANK_A",
            model_update_digest=digest_a,
        )

        global_digest = hashlib.sha256(b"global_ev06").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id,
            round_number=1,
            aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[digest_a],
            aggregated_global_model_digest=global_digest,
            coordinator_token=COORD_SECRET,
        )

        # Claiming 50 epochs when only 3 were observed
        with pytest.raises(TrainingProvenanceSecurityError, match="Executed epochs mismatch"):
            evidence_source.complete_training_job(
                job_id=job_id,
                executed_rounds=1,
                executed_epochs=50,
                sample_count=100,
                observed_metrics={"loss": 0.28},
                artifact_sha256=global_digest,
                worker_token=BANK_A_SECRET,
            )

    def test_ev_07_sample_accounting_semantics(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-07: Sample accounting adheres to explicit semantics (unique bank partitions vs exposures)."""
        job_id = f"job_ev07_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev07_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=2,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A", "BANK_B"),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Round 1: Bank A (100 samples), Bank B (200 samples) -> round total = 300
        d_a1 = hashlib.sha256(b"a1").hexdigest()
        d_b1 = hashlib.sha256(b"b1").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.40, metrics={"loss": 0.40}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d_a1,
        )
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, sample_count=200, actual_executed_epochs=1,
            observed_loss=0.42, metrics={"loss": 0.42}, worker_token=BANK_B_SECRET,
            bank_id="BANK_B", model_update_digest=d_b1,
        )
        g_d1 = hashlib.sha256(b"g1").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A", "BANK_B"],
            accepted_local_update_digests=[d_a1, d_b1],
            aggregated_global_model_digest=g_d1, coordinator_token=COORD_SECRET,
        )

        # Round 2: Bank A (100 samples), Bank B (200 samples) -> exposures = 600, unique = 300
        d_a2 = hashlib.sha256(b"a2").hexdigest()
        d_b2 = hashlib.sha256(b"b2").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=2, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.30, metrics={"loss": 0.30}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d_a2,
        )
        evidence_source.record_round_observation(
            job_id=job_id, round_number=2, sample_count=200, actual_executed_epochs=1,
            observed_loss=0.32, metrics={"loss": 0.32}, worker_token=BANK_B_SECRET,
            bank_id="BANK_B", model_update_digest=d_b2,
        )
        g_d2 = hashlib.sha256(b"g2").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id, round_number=2, aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A", "BANK_B"],
            accepted_local_update_digests=[d_a2, d_b2],
            aggregated_global_model_digest=g_d2, coordinator_token=COORD_SECRET,
        )

        # Claiming an arbitrary number like 9999 fails
        with pytest.raises(TrainingProvenanceSecurityError, match="Sample count mismatch"):
            evidence_source.complete_training_job(
                job_id=job_id, executed_rounds=2, executed_epochs=1,
                sample_count=9999, observed_metrics={"loss": 0.30},
                artifact_sha256=g_d2, worker_token=BANK_A_SECRET,
            )

        # Claiming unique_bank_samples (300) or total exposures (600) succeeds
        rec = evidence_source.complete_training_job(
            job_id=job_id, executed_rounds=2, executed_epochs=1,
            sample_count=300, observed_metrics={"loss": 0.30},
            artifact_sha256=g_d2, worker_token=BANK_A_SECRET,
        )
        assert rec.sample_count == 300

    def test_ev_08_quorum_policy_enforced(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-08: Global aggregation rejects execution when participant quorum is unsatisfied."""
        job_id = f"job_ev08_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev08_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A", "BANK_B", "BANK_C"),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        d_a = hashlib.sha256(b"weights_a_ev08").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.35, metrics={"loss": 0.35}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d_a,
        )

        # Configured min_quorum = 2; only Bank A present
        with pytest.raises(TrainingProvenanceSecurityError, match="Quorum failure"):
            evidence_source.record_global_aggregation(
                job_id=job_id,
                round_number=1,
                aggregation_algorithm="fedavg",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=[d_a],
                aggregated_global_model_digest=hashlib.sha256(b"g_fail").hexdigest(),
                aggregation_configuration={"min_quorum": 2},
                coordinator_token=COORD_SECRET,
            )

    def test_ev_09_failed_and_retried_contributions_handled_without_double_counting(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-09: Failed observations are excluded; retry attempt replaces failed attempt without double counting."""
        job_id = f"job_ev09_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev09_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Bank A Attempt 1 fails
        d_fail = hashlib.sha256(b"fail").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, attempt_number=1, sample_count=100,
            actual_executed_epochs=1, observed_loss=1.5, metrics={"loss": 1.5},
            worker_token=BANK_A_SECRET, bank_id="BANK_A", model_update_digest=d_fail,
            execution_status="FAILED",
        )

        # Aggregation cannot accept failed attempt
        with pytest.raises(TrainingProvenanceSecurityError, match="has no verified completed round observation"):
            evidence_source.record_global_aggregation(
                job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=[d_fail],
                aggregated_global_model_digest=hashlib.sha256(b"g_fail").hexdigest(),
                coordinator_token=COORD_SECRET,
            )

        # Bank A Attempt 2 succeeds
        d_succ = hashlib.sha256(b"succ").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, attempt_number=2, sample_count=100,
            actual_executed_epochs=1, observed_loss=0.31, metrics={"loss": 0.31},
            worker_token=BANK_A_SECRET, bank_id="BANK_A", model_update_digest=d_succ,
            execution_status="COMPLETED",
        )

        g_succ = hashlib.sha256(b"g_succ").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[d_succ],
            aggregated_global_model_digest=g_succ,
            coordinator_token=COORD_SECRET,
        )

        # Completed: sample_count is 100 (not 200) because failed attempt is not counted as completed contribution
        rec = evidence_source.complete_training_job(
            job_id=job_id, executed_rounds=1, executed_epochs=1, sample_count=100,
            observed_metrics={"loss": 0.31}, artifact_sha256=g_succ,
            worker_token=BANK_A_SECRET,
        )
        assert rec.sample_count == 100

    def test_ev_10_and_ev_11_local_update_digest_bound_and_only_authorized_accepted(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-10 & EV-11: Aggregation accepts only matching digests from authorized scheduled workers."""
        job_id = f"job_ev10_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev10_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A", "BANK_B"),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        real_bytes = b"real_pytorch_update_tensor_bytes"
        expected_digest = hashlib.sha256(real_bytes).hexdigest()

        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.33, metrics={"loss": 0.33}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=expected_digest,
        )

        # 1. EV-10: Claiming a tampered digest for Bank A is rejected
        tampered_digest = hashlib.sha256(b"tampered_update").hexdigest()
        with pytest.raises(TrainingProvenanceSecurityError, match="does not match observed digest"):
            evidence_source.record_global_aggregation(
                job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
                accepted_participant_ids=["BANK_A"],
                accepted_local_update_digests=[tampered_digest],
                aggregated_global_model_digest=hashlib.sha256(b"g").hexdigest(),
                coordinator_token=COORD_SECRET,
            )

        # 2. EV-11: Unauthorized participant (BANK_C not in permitted_workers) is rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Participant 'BANK_C' is not in permitted workers"):
            evidence_source.record_global_aggregation(
                job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
                accepted_participant_ids=["BANK_C"],
                accepted_local_update_digests=[expected_digest],
                aggregated_global_model_digest=hashlib.sha256(b"g").hexdigest(),
                coordinator_token=COORD_SECRET,
            )

    def test_ev_12_final_artifact_digest_matches_actual_aggregated_model(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-12: Final completion artifact SHA-256 must match final accepted global aggregation digest."""
        job_id = f"job_ev12_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev12_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=1,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        d_a = hashlib.sha256(b"a_ev12").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.30, metrics={"loss": 0.30}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d_a,
        )

        real_global_digest = hashlib.sha256(b"real_aggregated_model_bytes").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"],
            accepted_local_update_digests=[d_a],
            aggregated_global_model_digest=real_global_digest,
            coordinator_token=COORD_SECRET,
        )

        # Substituting unrelated artifact hash at completion is rejected
        substituted_sha = hashlib.sha256(b"unrelated_model_weights").hexdigest()
        with pytest.raises(TrainingProvenanceSecurityError, match="Artifact SHA-256 does not match final accepted aggregation digest"):
            evidence_source.complete_training_job(
                job_id=job_id, executed_rounds=1, executed_epochs=1, sample_count=100,
                observed_metrics={"loss": 0.30}, artifact_sha256=substituted_sha,
                worker_token=BANK_A_SECRET,
            )

    def test_ev_13_cross_round_update_replay_fails(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-13: Replaying an update digest from Round 1 in Round 2 is rejected."""
        job_id = f"job_ev13_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev13_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=2,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        # Round 1
        d_r1 = hashlib.sha256(b"round_1_update").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.40, metrics={"loss": 0.40}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d_r1,
        )
        g_r1 = hashlib.sha256(b"g_r1").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"], accepted_local_update_digests=[d_r1],
            aggregated_global_model_digest=g_r1, coordinator_token=COORD_SECRET,
        )

        # Round 2: Maliciously replaying d_r1 in observation and aggregation
        evidence_source.record_round_observation(
            job_id=job_id, round_number=2, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.35, metrics={"loss": 0.35}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d_r1,
        )
        with pytest.raises(TrainingProvenanceSecurityError, match="Replay of earlier round update digest .* rejected"):
            evidence_source.record_global_aggregation(
                job_id=job_id, round_number=2, aggregation_algorithm="fedavg",
                accepted_participant_ids=["BANK_A"], accepted_local_update_digests=[d_r1],
                aggregated_global_model_digest=hashlib.sha256(b"g_r2").hexdigest(),
                coordinator_token=COORD_SECRET,
            )

    def test_ev_14_missing_or_inconsistent_global_round_evidence_prevents_completion(
        self, evidence_source: TrustedTrainingJobRecordSource
    ) -> None:
        """EV-14: Inconsistent or non-continuous global aggregation rounds prevent completion."""
        job_id = f"job_ev14_{uuid.uuid4().hex[:8]}"
        run_id = f"run_ev14_{uuid.uuid4().hex[:8]}"

        evidence_source.schedule_training_job(
            job_id=job_id,
            training_run_id=run_id,
            simulation_id="sim_01",
            coordinator_token=COORD_SECRET,
            expected_rounds=2,
            expected_epochs=1,
            input_dim=len(FEATURE_NAMES),
            feature_names=list(FEATURE_NAMES),
            permitted_workers=("BANK_A",),
        )
        evidence_source.start_training_job(job_id=job_id, worker_token=BANK_A_SECRET)

        d1 = hashlib.sha256(b"r1").hexdigest()
        d2 = hashlib.sha256(b"r2").hexdigest()
        evidence_source.record_round_observation(
            job_id=job_id, round_number=1, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.45, metrics={"loss": 0.45}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d1,
        )
        evidence_source.record_round_observation(
            job_id=job_id, round_number=2, sample_count=100, actual_executed_epochs=1,
            observed_loss=0.35, metrics={"loss": 0.35}, worker_token=BANK_A_SECRET,
            bank_id="BANK_A", model_update_digest=d2,
        )

        # Only Round 1 global aggregation recorded, Round 2 omitted
        g1 = hashlib.sha256(b"g1").hexdigest()
        evidence_source.record_global_aggregation(
            job_id=job_id, round_number=1, aggregation_algorithm="fedavg",
            accepted_participant_ids=["BANK_A"], accepted_local_update_digests=[d1],
            aggregated_global_model_digest=g1, coordinator_token=COORD_SECRET,
        )

        with pytest.raises(TrainingProvenanceSecurityError, match="Global aggregation round continuity mismatch"):
            evidence_source.complete_training_job(
                job_id=job_id, executed_rounds=2, executed_epochs=1, sample_count=100,
                observed_metrics={"loss": 0.35}, artifact_sha256=g1,
                worker_token=BANK_A_SECRET,
            )
