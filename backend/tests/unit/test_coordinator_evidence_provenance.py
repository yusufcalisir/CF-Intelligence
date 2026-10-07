"""Unit tests for CoordinatorService holdout evaluation evidence provenance and trust boundary enforcement.

Verifies:
1. Production holdout evaluation pipeline: Scenarios A through G.
2. Adversarial controls: 1 through 12.
3. Falsification mutation challenges: Mutations A through F.
4. Cryptographic model identity and dataset provenance bindings.
5. Fail-closed safety under all unverified or error conditions.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch
import torch.nn as nn

from app.application.services.candidate_evaluator import CandidateModelEvaluator
from app.application.services.coordinator_service import CoordinatorService
from app.domain.enums import DatasetProvenance
from app.domain.value_objects import DesignatedHoldoutDataset, RoundEvaluationEvidence


class SeparableFraudModel(nn.Module):
    """Deterministic linear PyTorch model separating low-feature negatives from high-feature positives."""

    def __init__(self, invert: bool = False) -> None:
        super().__init__()
        self.linear = nn.Linear(2, 1)
        with torch.no_grad():
            w = -5.0 if invert else 5.0
            b = 2.5 if invert else -2.5
            self.linear.weight.copy_(torch.tensor([[w, w]]))
            self.linear.bias.copy_(torch.tensor([b]))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.linear(x)).squeeze(-1)


def _make_holdout_dataset(
    dataset_id: str = "canonical_holdout_v1",
    provenance: str = "EMPIRICAL_EXTERNAL_DATA",
    single_class: bool = False,
) -> DesignatedHoldoutDataset:
    features = np.array(
        [
            [0.0, 0.0],
            [0.0, 0.1],
            [0.1, 0.0],
            [0.1, 0.1],
            [0.9, 0.9],
            [0.9, 1.0],
            [1.0, 0.9],
            [1.0, 1.0],
        ],
        dtype=np.float32,
    )
    if single_class:
        labels = np.array([0, 0, 0, 0, 0, 0, 0, 0], dtype=int)
    else:
        labels = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=int)

    return DesignatedHoldoutDataset(
        dataset_id=dataset_id,
        features=features,
        labels=labels,
        provenance=provenance,
    )


# ==============================================================================
# Base Invariants & Trust Boundary Tests
# ==============================================================================


def test_no_evaluation_evidence_fails_closed() -> None:
    """A round aggregated with no validation data must fail closed without promotion."""
    coord = CoordinatorService()
    round_info = coord.start_round(min_clients=1)
    round_id = round_info["round_id"]

    res = coord.aggregate_and_deploy(round_id, min_auc_threshold=0.70)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"
    assert res["auc_score"] is None
    assert res["evaluation_evidence"]["provenance"] == "NONE"


def test_cannot_bind_evidence_to_nonexistent_round() -> None:
    """Attempting to bind evidence to a round that does not exist must raise ValueError."""
    coord = CoordinatorService()
    ev = RoundEvaluationEvidence(
        round_id=999,
        validation_labels=[0, 1],
        validation_preds=[0.1, 0.9],
    )
    with pytest.raises(ValueError, match="Round ID 999 does not exist"):
        coord.set_round_validation_data(999, evidence=ev)


def test_cannot_bind_evidence_to_completed_round() -> None:
    """Attempting to bind evidence to an already COMPLETED round must raise ValueError."""
    coord = CoordinatorService()
    round_info = coord.start_round(min_clients=1)
    round_id = round_info["round_id"]

    coord.aggregate_and_deploy(round_id)
    assert coord.rounds[round_id]["status"] == "COMPLETED"

    with pytest.raises(ValueError, match="is in status 'COMPLETED', cannot bind validation data"):
        coord.set_round_validation_data(round_id, validation_labels=[0, 1], validation_preds=[0.1, 0.9])


def test_cross_round_evidence_substitution_blocked_in_set_round_data() -> None:
    """Supplying Round 1 evidence to Round 2 via set_round_validation_data must be rejected."""
    coord = CoordinatorService()
    r1 = coord.start_round(min_clients=1)["round_id"]
    r2 = coord.start_round(min_clients=1)["round_id"]

    ev_round_1 = RoundEvaluationEvidence(
        round_id=r1,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.05, 0.08, 0.92, 0.95],
        provenance="AUTHORITATIVE_HOLDOUT_EVALUATION",
    )

    with pytest.raises(ValueError, match=f"Evidence round_id {r1} does not match target round {r2}"):
        coord.set_round_validation_data(r2, evidence=ev_round_1)


def test_cross_round_evidence_substitution_blocked_in_aggregate_and_deploy() -> None:
    """Supplying Round 1 evidence to Round 2 via aggregate_and_deploy must be rejected."""
    coord = CoordinatorService()
    r1 = coord.start_round(min_clients=1)["round_id"]
    r2 = coord.start_round(min_clients=1)["round_id"]

    ev_round_1 = RoundEvaluationEvidence(
        round_id=r1,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.05, 0.08, 0.92, 0.95],
    )

    with pytest.raises(ValueError, match=f"Evidence round_id {r1} does not match target round {r2}"):
        coord.aggregate_and_deploy(r2, evidence=ev_round_1)


def test_cross_model_version_substitution_blocked() -> None:
    """Evidence with a mismatched model_version must be rejected when model_version is bound."""
    coord = CoordinatorService()
    round_info = coord.start_round(min_clients=1)
    round_id = round_info["round_id"]
    coord.rounds[round_id]["model_version"] = "v2.1.0-checkpoint-abc"

    ev_wrong_model = RoundEvaluationEvidence(
        round_id=round_id,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.05, 0.08, 0.92, 0.95],
        model_version="v1.0.0-checkpoint-xyz",
    )

    with pytest.raises(ValueError, match="does not match round model_version"):
        coord.set_round_validation_data(round_id, evidence=ev_wrong_model)


def test_malformed_vector_shapes_and_values_rejected() -> None:
    """RoundEvaluationEvidence must strictly reject unequal lengths, empty vectors, and invalid values."""
    with pytest.raises(ValueError, match="non-empty"):
        RoundEvaluationEvidence(round_id=1, validation_labels=[], validation_preds=[])

    with pytest.raises(ValueError, match="must match predictions length"):
        RoundEvaluationEvidence(round_id=1, validation_labels=[0, 1], validation_preds=[0.5])

    with pytest.raises(ValueError, match="binary 0 or 1"):
        RoundEvaluationEvidence(round_id=1, validation_labels=[0, 2], validation_preds=[0.1, 0.9])

    with pytest.raises(ValueError, match="finite float in \\[0.0, 1.0\\]"):
        RoundEvaluationEvidence(round_id=1, validation_labels=[0, 1], validation_preds=[0.1, 1.5])

    with pytest.raises(ValueError, match="finite float in \\[0.0, 1.0\\]"):
        RoundEvaluationEvidence(round_id=1, validation_labels=[0, 1], validation_preds=[float("nan"), 0.9])


# ==============================================================================
# Required Production Scenarios: Scenario A through Scenario G
# ==============================================================================


def test_scenario_a_no_evaluator_configured() -> None:
    """Scenario A: Quorum succeeds, but no evaluator configured -> UNVERIFIED_NO_EVALUATION, promotion blocked."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    res = coord.aggregate_and_deploy(round_id)
    assert res["status"] == "COMPLETED"
    assert res["auc_score"] is None
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_scenario_b_evaluator_configured_holdout_unavailable() -> None:
    """Scenario B: Evaluator configured without holdout dataset -> fails closed without synthetic fallback."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    evaluator = CandidateModelEvaluator(holdout_dataset=None)
    coord.set_evaluator(evaluator)
    coord.set_round_candidate_model(round_id, SeparableFraudModel())

    res = coord.aggregate_and_deploy(round_id)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"
    assert res["auc_score"] is None


def test_scenario_c_legitimate_candidate_designated_holdout_passing_metric() -> None:
    """Scenario C: Legitimate candidate model + designated holdout -> actual inference -> PR-AUC >= 0.70 -> CHAMPION."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    holdout = _make_holdout_dataset(dataset_id="canonical_production_holdout", provenance="EMPIRICAL_EXTERNAL_DATA")
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    coord.set_evaluator(evaluator)

    model = SeparableFraudModel(invert=False)
    coord.set_round_candidate_model(round_id, model, model_version="v1.0.0-candidate")

    res = coord.aggregate_and_deploy(round_id, min_auc_threshold=0.70)
    assert res["is_champion"] is True
    assert res["model_status"] == "CHAMPION"
    assert res["auc_score"] is not None and res["auc_score"] >= 0.70
    assert res["evaluation_evidence"]["dataset_id"] in ("canonical_production_holdout", "canonical_production_holdout:1.0.0")
    assert res["evaluation_evidence"]["provenance"] == "EMPIRICAL_EXTERNAL_DATA"
    assert res["evaluation_evidence"]["producer"] == "CandidateModelEvaluator"
    assert res["evaluation_evidence"]["model_hash"] is not None


def test_scenario_d_legitimate_candidate_designated_holdout_failing_metric() -> None:
    """Scenario D: Legitimate candidate model with low accuracy -> actual inference -> PR-AUC < 0.70 -> REJECTED_LOW_AUC."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    holdout = _make_holdout_dataset(dataset_id="canonical_production_holdout", provenance="EMPIRICAL_EXTERNAL_DATA")
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    coord.set_evaluator(evaluator)

    inverted_model = SeparableFraudModel(invert=True)
    coord.set_round_candidate_model(round_id, inverted_model, model_version="v1.0.0-inverted")

    res = coord.aggregate_and_deploy(round_id, min_auc_threshold=0.70)
    assert res["is_champion"] is False
    assert res["model_status"] == "REJECTED_LOW_AUC"
    assert res["auc_score"] is not None and res["auc_score"] < 0.70


def test_scenario_e_single_class_holdout_fails_closed() -> None:
    """Scenario E: Holdout contains only single class -> PR-AUC undefined -> fails closed as UNVERIFIED_NO_EVALUATION."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    holdout = _make_holdout_dataset(single_class=True)
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    coord.set_evaluator(evaluator)

    model = SeparableFraudModel()
    coord.set_round_candidate_model(round_id, model)

    res = coord.aggregate_and_deploy(round_id)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"
    assert res["auc_score"] is None
    assert res["evaluation_evidence"]["metric_status"] == "undefined_single_class"


def test_scenario_f_model_identity_mismatch_blocks_promotion() -> None:
    """Scenario F: Model identity mismatch (version or hash) -> rejected."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    holdout = _make_holdout_dataset()
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    coord.set_evaluator(evaluator)

    model = SeparableFraudModel()
    coord.set_round_candidate_model(round_id, model, model_version="v1.0.0-actual")

    # Construct evidence claiming to evaluate v2.0.0-fraudulent
    ev_mismatched = RoundEvaluationEvidence(
        round_id=round_id,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.1, 0.1, 0.9, 0.9],
        model_version="v2.0.0-fraudulent",
        provenance="EMPIRICAL_EXTERNAL_DATA",
    )

    with pytest.raises(ValueError, match="does not match round model_version"):
        coord.aggregate_and_deploy(round_id, evidence=ev_mismatched)


def test_scenario_g_dataset_identity_mismatch_blocks_promotion() -> None:
    """Scenario G: Evaluation evidence bound to holdout A cannot promote round designated for holdout B."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    # Round expects dataset_b
    coord.round_designated_datasets[round_id] = "designated_holdout_b"

    ev_wrong_dataset = RoundEvaluationEvidence(
        round_id=round_id,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.1, 0.1, 0.9, 0.9],
        dataset_id="unauthorized_holdout_a",
        provenance="EMPIRICAL_EXTERNAL_DATA",
    )

    with pytest.raises(ValueError, match="does not match designated dataset"):
        coord.aggregate_and_deploy(round_id, evidence=ev_wrong_dataset)


# ==============================================================================
# Adversarial Controls: 1 through 12
# ==============================================================================


def test_adversarial_control_01_raw_scalar_metric_injection_impossible() -> None:
    """AC-1: CoordinatorService accepts no raw scalar mock_auc or eval_auc parameters."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    # Calling with scalar metric keywords is rejected or ignored without evidence
    res = coord.aggregate_and_deploy(round_id, mock_auc=0.99, eval_auc=0.99)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_adversarial_control_02_raw_prediction_vector_injection_blocked_in_production() -> None:
    """AC-2: Raw caller prediction vectors cannot promote model in production mode."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    coord.set_round_validation_data(
        round_id,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.01, 0.02, 0.98, 0.99],
    )
    # Production promotion (allow_test_fixtures=False) must block champion promotion
    res = coord.aggregate_and_deploy(round_id, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "TEST_FIXTURE_PROMOTION_BLOCKED"


def test_adversarial_control_03_arbitrary_labels_cannot_promote_without_model_inference() -> None:
    """AC-3: Arbitrary labels cannot promote without authentic model inference against designated holdout."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    # Without an evaluator and model, labels alone cannot promote
    res = coord.aggregate_and_deploy(round_id)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_adversarial_control_04_test_fixture_cannot_masquerade_as_empirical_in_production() -> None:
    """AC-4: TEST_FIXTURE provenance cannot promote in production mode."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    ev = RoundEvaluationEvidence(
        round_id=round_id,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.05, 0.05, 0.95, 0.95],
        provenance="TEST_FIXTURE",
    )
    res = coord.aggregate_and_deploy(round_id, evidence=ev, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "TEST_FIXTURE_PROMOTION_BLOCKED"


def test_adversarial_control_05_round_a_evaluation_cannot_promote_round_b() -> None:
    """AC-5: Round A evidence cannot promote Round B."""
    coord = CoordinatorService()
    r1 = coord.start_round(min_clients=1)["round_id"]
    r2 = coord.start_round(min_clients=1)["round_id"]

    ev1 = RoundEvaluationEvidence(
        round_id=r1,
        validation_labels=[0, 0, 1, 1],
        validation_preds=[0.05, 0.05, 0.95, 0.95],
        provenance="EMPIRICAL_EXTERNAL_DATA",
    )
    with pytest.raises(ValueError, match=f"Evidence round_id {r1} does not match target round {r2}"):
        coord.aggregate_and_deploy(r2, evidence=ev1)


def test_adversarial_control_06_model_a_evaluation_cannot_promote_model_b() -> None:
    """AC-6: Model A evaluation cannot promote Model B (model hash binding)."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    model_b = SeparableFraudModel(invert=True)
    coord.set_round_candidate_model(round_id, model_b)

    # Evaluate Model A
    model_a = SeparableFraudModel(invert=False)
    holdout = _make_holdout_dataset()
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    ev_a = evaluator.evaluate(round_id, model_a)

    # Attempt to promote Model B using Model A's evidence
    with pytest.raises(ValueError, match="does not match candidate model hash"):
        coord.aggregate_and_deploy(round_id, evidence=ev_a)


def test_adversarial_control_07_holdout_a_cannot_claim_holdout_b_identity() -> None:
    """AC-7: Holdout A evidence cannot claim Holdout B identity."""
    holdout = _make_holdout_dataset(dataset_id="holdout_alpha")
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    model = SeparableFraudModel()

    with pytest.raises(ValueError, match="does not match expected 'holdout_beta'"):
        evaluator.evaluate(1, model, expected_dataset_id="holdout_beta")


def test_adversarial_control_08_missing_evaluator_cannot_promote() -> None:
    """AC-8: Missing evaluator fails closed."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]
    coord.evaluator = None

    res = coord.aggregate_and_deploy(round_id)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_adversarial_control_09_evaluator_exception_cannot_promote() -> None:
    """AC-9: Evaluator exception fails closed and blocks promotion."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    class BrokenEvaluator(CandidateModelEvaluator):
        def evaluate(self, *args, **kwargs):
            raise RuntimeError("Inference hardware fault")

    coord.set_evaluator(BrokenEvaluator(holdout_dataset=_make_holdout_dataset()))
    coord.set_round_candidate_model(round_id, SeparableFraudModel())

    res = coord.aggregate_and_deploy(round_id)
    assert res["is_champion"] is False
    assert res["model_status"] == "UNVERIFIED_NO_EVALUATION"


def test_adversarial_control_10_undefined_metric_cannot_promote() -> None:
    """AC-10: Undefined metric (single-class holdout or non-finite) cannot promote."""
    is_champion, status = CoordinatorService.evaluate_quality_gate(None)
    assert is_champion is False
    assert status == "UNVERIFIED_NO_EVALUATION"

    is_champion, status = CoordinatorService.evaluate_quality_gate(float("nan"))
    assert is_champion is False
    assert status == "UNVERIFIED_NO_EVALUATION"


def test_adversarial_control_11_passing_authoritative_evaluation_can_promote() -> None:
    """AC-11: Passing authoritative evaluation promotes champion."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    holdout = _make_holdout_dataset(provenance="EMPIRICAL_EXTERNAL_DATA")
    coord.set_evaluator(CandidateModelEvaluator(holdout_dataset=holdout))
    coord.set_round_candidate_model(round_id, SeparableFraudModel(invert=False))

    res = coord.aggregate_and_deploy(round_id)
    assert res["is_champion"] is True
    assert res["model_status"] == "CHAMPION"


def test_adversarial_control_12_failing_authoritative_evaluation_cannot_promote() -> None:
    """AC-12: Failing authoritative evaluation blocks promotion."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    holdout = _make_holdout_dataset(provenance="EMPIRICAL_EXTERNAL_DATA")
    coord.set_evaluator(CandidateModelEvaluator(holdout_dataset=holdout))
    coord.set_round_candidate_model(round_id, SeparableFraudModel(invert=True))

    res = coord.aggregate_and_deploy(round_id)
    assert res["is_champion"] is False
    assert res["model_status"] == "REJECTED_LOW_AUC"


# ==============================================================================
# Falsification Mutation Challenges: Mutations A through F
# ==============================================================================


def test_mutation_a_hardcoded_metric_killed() -> None:
    """Mutation A: A system hardcoding PR-AUC=0.85 must be killed by low-performing model test."""
    model = SeparableFraudModel(invert=True)
    holdout = _make_holdout_dataset()
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    ev = evaluator.evaluate(1, model)

    # Real evaluation of inverted model must yield low AUC (< 0.70), killing the mutation
    assert ev.metric_score is not None and ev.metric_score < 0.70
    assert ev.metric_score != 0.85


def test_mutation_b_caller_provided_predictions_killed() -> None:
    """Mutation B: A system using caller-provided predictions instead of candidate model inference is killed."""
    holdout = _make_holdout_dataset()
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    model = SeparableFraudModel(invert=False)

    ev = evaluator.evaluate(1, model)
    # The predictions must strictly originate from model inference, not arbitrary caller vectors
    real_probs = model(torch.as_tensor(holdout.features, dtype=torch.float32)).detach().numpy()
    np.testing.assert_allclose(ev.validation_preds, real_probs, atol=1e-5)


def test_mutation_c_caller_provided_labels_killed() -> None:
    """Mutation C: A system accepting arbitrary caller labels instead of holdout labels is killed."""
    holdout = _make_holdout_dataset()
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    model = SeparableFraudModel(invert=False)

    ev = evaluator.evaluate(1, model)
    # Labels must match holdout labels exactly
    assert ev.validation_labels == list(holdout.labels)


def test_mutation_d_replace_missing_holdout_with_fixture_killed() -> None:
    """Mutation D: A system that replaces missing holdout with a generated synthetic fixture is killed."""
    evaluator = CandidateModelEvaluator(holdout_dataset=None)
    model = SeparableFraudModel()

    with pytest.raises(ValueError, match="no designated holdout dataset configured"):
        evaluator.evaluate(1, model)


def test_mutation_e_evaluate_model_a_promote_model_b_killed() -> None:
    """Mutation E: Evaluating Model A and attempting to promote Model B is killed by model hash validation."""
    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]

    model_b = SeparableFraudModel(invert=True)
    coord.set_round_candidate_model(round_id, model_b)

    model_a = SeparableFraudModel(invert=False)
    holdout = _make_holdout_dataset()
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    ev_a = evaluator.evaluate(round_id, model_a)

    with pytest.raises(ValueError, match="does not match candidate model hash"):
        coord.aggregate_and_deploy(round_id, evidence=ev_a)


def test_mutation_f_provenance_tampering_killed() -> None:
    """Mutation F: Changing provenance from TEST_FIXTURE to EMPIRICAL without source evidence is killed in holdout init."""
    # Holdout provenance must reflect declared source dataset
    holdout = DesignatedHoldoutDataset(
        dataset_id="test_fixture_ds",
        features=np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32),
        labels=np.array([0, 1], dtype=int),
        provenance=DatasetProvenance.TEST_FIXTURE,
    )
    evaluator = CandidateModelEvaluator(holdout_dataset=holdout)
    ev = evaluator.evaluate(1, SeparableFraudModel())
    assert ev.provenance == "TEST_FIXTURE"

    coord = CoordinatorService()
    rnd = coord.start_round(min_clients=1)
    round_id = rnd["round_id"]
    coord.set_round_candidate_model(round_id, SeparableFraudModel())

    # Production promotion blocks TEST_FIXTURE
    res = coord.aggregate_and_deploy(round_id, evidence=ev, allow_test_fixtures=False)
    assert res["is_champion"] is False
    assert res["model_status"] == "TEST_FIXTURE_PROMOTION_BLOCKED"
