"""Hypothesis Property-Based Test Suite for Explainability (XAI) Subsystem.

Verifies mathematical and explainability invariants across hundreds of randomized scenarios:
  Invariant 1:  Risk Signal Weights Sum to 1.0 & Normalized Scores in [0, 1]
  Invariant 2:  Counterfactual Remediated Score <= Target Score iff is_cleared == True
  Invariant 3:  Feature Contribution Array Length = 10 & Ordered by Magnitude
  Invariant 4:  GNN Edge Contribution Percentages Sum to Exactly 100.0%
  Invariant 5:  Real-Time Feature Attributions Contain Valid Directions & Scores in [0, 1]
  Invariant 6:  Decision Replay Evaluates Exactly 9 Policy Rules with Correct Contributions
"""

from __future__ import annotations

import sys
import pytest
import numpy as np
from hypothesis import given, settings, strategies as st

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.explainability_service import ExplainabilityService
from app.domain.realtime_explainer import FastInferenceExplainer
from app.domain.entities_phase2 import Alert, AlertSeverity

explainer_service = ExplainabilityService()
fast_explainer = FastInferenceExplainer()

# Strategies for generating randomized inputs
reason_code_strategy = st.lists(
    st.sampled_from([
        "ML-HIGH", "ML-FLAG", "VEL-001", "MERCH-RISK",
        "GEO-RISK", "NEW-ACCT", "CB-HIST", "HIGH-AMT", "ODD-HOUR"
    ]),
    unique=True,
    max_size=9,
)

mcc_strategy = st.one_of(
    st.sampled_from(["crypto_exchange", "gambling", "p2p_cash", "retail", "travel", "food", "other"]),
    st.text(min_size=0, max_size=15),
)


# =====================================================================
# Invariant 1: Risk Signal Weights Sum to 1.0 & Scores in [0, 1]
# =====================================================================

@given(
    risk_score=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    reason_codes=reason_code_strategy,
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(deadline=None, max_examples=100)
def test_inv1_risk_signal_breakdown_properties(risk_score: float, reason_codes: list[str], confidence: float):
    """Invariant 1: Signal weights sum to 1.0 and normalized scores lie in [0.0, 1.0]."""
    alert = Alert(
        id="alt_hyp_01",
        risk_score=risk_score,
        severity=AlertSeverity.HIGH if risk_score > 500 else AlertSeverity.LOW,
        model_confidence=confidence,
        bank_id="bank_test",
        reason_codes=reason_codes,
    )

    rpt = explainer_service.explain_alert(alert)

    # 1. Check weight summation
    weight_sum = sum(s.weight for s in rpt.risk_score_breakdown)
    assert abs(weight_sum - 1.0) < 1e-5, f"Weights must sum to 1.0, got {weight_sum}"

    # 2. Check score bounds [0, 1]
    for signal in rpt.risk_score_breakdown:
        assert 0.0 <= signal.normalized_score <= 1.0 + 1e-6, (
            f"Normalized score {signal.normalized_score} outside [0, 1]"
        )


# =====================================================================
# Invariant 2: Counterfactual Remediated Score <= Target Score
# =====================================================================

@given(
    orig_score=st.floats(min_value=350.1, max_value=1000.0, allow_nan=False, allow_infinity=False),
    target_score=st.floats(min_value=100.0, max_value=350.0, allow_nan=False, allow_infinity=False),
    reason_codes=reason_code_strategy,
)
@settings(deadline=None, max_examples=100)
def test_inv2_counterfactual_remediation_invariant(orig_score: float, target_score: float, reason_codes: list[str]):
    """Invariant 2: Remediated score <= target_score iff is_cleared == True; remediated < orig."""
    alert = Alert(
        id="alt_hyp_02",
        risk_score=orig_score,
        severity=AlertSeverity.CRITICAL if orig_score > 800 else AlertSeverity.HIGH,
        model_confidence=0.90,
        bank_id="bank_test",
        reason_codes=reason_codes,
    )

    cf = explainer_service.generate_counterfactuals(alert, target_score=target_score)

    if cf.is_cleared:
        assert cf.remediated_score <= target_score + 1e-4, (
            f"Remediated score {cf.remediated_score} exceeds target {target_score}"
        )
    assert cf.remediated_score <= orig_score, (
        f"Remediated score {cf.remediated_score} must be less than or equal to orig {orig_score}"
    )
    assert (cf.remediated_score <= target_score) == cf.is_cleared, (
        "is_cleared boolean mismatch with score threshold comparison"
    )


# =====================================================================
# Invariant 3: Feature Contribution Array Structure & Sorting
# =====================================================================

@given(
    amount=st.one_of(st.floats(min_value=0.0, max_value=1e6), st.just(0.0), st.just(1e12)),
    velocity=st.one_of(st.floats(min_value=0.0, max_value=100.0), st.just(0.0)),
    account_age=st.floats(min_value=0.0, max_value=3650.0),
    chargebacks=st.integers(min_value=0, max_value=50),
)
@settings(deadline=None, max_examples=100)
def test_inv3_feature_contribution_array_properties(amount: float, velocity: float, account_age: float, chargebacks: int):
    """Invariant 3: Feature contribution list has 10 items, sorted by contribution descending."""
    txn_dict = {
        "transaction_amount": amount,
        "velocity": velocity,
        "account_age_days": account_age,
        "chargeback_count": chargebacks,
        "merchant_risk_score": 0.75,
        "customer_history_score": 0.85,
    }

    contributions = explainer_service.compute_shap_values(txn_dict)

    # 1. Length must be exactly 10 features
    assert len(contributions) == 10, f"Expected 10 features, got {len(contributions)}"

    # 2. Contributions must be sorted descending by absolute value or contribution
    values = [f["contribution"] for f in contributions]
    for i in range(len(values) - 1):
        assert abs(values[i]) >= abs(values[i + 1]) - 1e-6, (
            f"Contributions not sorted descending: {values[i]} < {values[i+1]}"
        )


# =====================================================================
# Invariant 4: GNN Edge Contribution Percentages Sum to 100%
# =====================================================================

@given(node_suffix=st.integers(min_value=1, max_value=10000))
@settings(deadline=None, max_examples=50)
def test_inv4_gnn_edge_percentage_sum_invariant(node_suffix: int):
    """Invariant 4: Edge contribution percentages sum to exactly 100.0%."""
    node_id = f"entity_node_{node_suffix}"
    gnn_rpt = explainer_service.explain_gnn_embedding(node_id)

    assert len(gnn_rpt.top_contributing_edges) > 0, "GNN explanation must return top edges"

    pct_sum = sum(c.contribution_percentage for c in gnn_rpt.top_contributing_edges)
    assert abs(pct_sum - 100.0) < 0.5, f"Edge percentages must sum to 100%, got {pct_sum}"


# =====================================================================
# Invariant 5: Real-Time Feature Attribution Bounds & Valid Directions
# =====================================================================

@given(
    amount=st.floats(min_value=0.0, max_value=1e6),
    velocity_1h=st.integers(min_value=0, max_value=100),
    mcc=mcc_strategy,
    risk_score=st.floats(min_value=0.0, max_value=1.0),
)
@settings(deadline=None, max_examples=100)
def test_inv5_realtime_feature_attribution_bounds(amount: float, velocity_1h: int, mcc: str, risk_score: float):
    """Invariant 5: Real-time attributions return valid directions and scores in [0.0, 1.0]."""
    attributions = fast_explainer.explain_realtime_score(
        amount=amount,
        velocity_1h=velocity_1h,
        merchant_category=mcc,
        risk_score=risk_score,
    )

    for attr in attributions:
        assert attr.direction in ("INCREASES_RISK", "DECREASES_RISK"), (
            f"Invalid direction: {attr.direction}"
        )
        assert 0.0 <= attr.contribution_score <= 1.0, (
            f"Contribution score {attr.contribution_score} outside [0, 1]"
        )


# =====================================================================
# Invariant 6: Decision Replay Policy Rule Evaluation List Integrity
# =====================================================================

@given(
    risk_score=st.floats(min_value=50.0, max_value=950.0),
    reason_codes=reason_code_strategy,
)
@settings(deadline=None, max_examples=100)
def test_inv6_decision_replay_policy_rule_integrity(risk_score: float, reason_codes: list[str]):
    """Invariant 6: Exactly 9 policy rules evaluated with contribution = weight * norm_val."""
    alert = Alert(
        id="alt_hyp_06",
        risk_score=risk_score,
        severity=AlertSeverity.HIGH,
        model_confidence=0.88,
        bank_id="bank_test",
        reason_codes=reason_codes,
    )

    replay_rpt = explainer_service.replay_inference_audit(alert)

    # Exactly 9 policy rules evaluated
    assert len(replay_rpt.policy_rules_evaluated) == 9, (
        f"Expected 9 policy rules, got {len(replay_rpt.policy_rules_evaluated)}"
    )

    # Check contribution formula for each rule
    base_norm = risk_score / 1000.0
    for rule in replay_rpt.policy_rules_evaluated:
        unrounded_norm = base_norm if rule.triggered else base_norm * 0.25
        expected_contrib = round(rule.weight * unrounded_norm, 4)
        assert abs(rule.contribution - expected_contrib) < 5e-4, (
            f"Rule {rule.signal_name} contribution mismatch: {rule.contribution} vs {expected_contrib}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
