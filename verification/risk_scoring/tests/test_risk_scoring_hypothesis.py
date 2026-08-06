"""Comprehensive Hypothesis Property-Based Tests for Risk Scoring Engine Module.

Verifies mathematical invariants across hundreds of randomized transaction scenarios:
- Property 1: Score Boundedness Invariant (0.0 <= Score <= 1000.0)
- Property 2: Weight Uniform Scale Invariance (Score(W) == Score(c * W) for c > 0)
- Property 3: ML Prediction Signal Monotonicity (p1 > p2 => Score(p1) >= Score(p2))
- Property 4: Risk Level Partition Completeness & Monotonicity
- Property 5: Top Signals Explainability Ranking Invariant (weighted_score[i] >= weighted_score[i+1])
- Property 6: Missing Field Robustness Invariant (Empty payload fallback safety)
"""

from __future__ import annotations

import sys
import math
import numpy as np

from hypothesis import given, settings, strategies as st

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.risk_engine import RiskScoringEngine, COUNTRY_RISK, MERCHANT_RISK
from app.domain.value_objects_phase2 import RiskWeightConfig, RiskScore, RiskSignal

engine = RiskScoringEngine()


# =====================================================================
# HYPOTHESIS STRATEGIES
# =====================================================================

@st.composite
def random_transaction_payload(draw):
    velocity = draw(st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False))
    m_score = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    cat = draw(st.sampled_from(list(MERCHANT_RISK.keys()) + ["unknown_category", ""]))
    country = draw(st.sampled_from(list(COUNTRY_RISK.keys()) + ["UNKNOWN", "kp", ""]))
    device = draw(st.sampled_from(["mobile_app", "web_browser", "pos_terminal", "atm", "phone_banking", "unknown_device"]))
    hist_score = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    age_days = draw(st.integers(min_value=0, max_value=10000))
    amt = draw(st.floats(min_value=0.0, max_value=1000000.0, allow_nan=False, allow_infinity=False))
    ml_pred = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))

    txn = {
        "velocity": velocity,
        "merchant_risk_score": m_score,
        "merchant_category": cat,
        "country_code": country,
        "device_type": device,
        "customer_history_score": hist_score,
        "account_age_days": age_days,
        "transaction_amount": amt,
    }
    return txn, ml_pred


@st.composite
def random_risk_weights(draw):
    w1 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w2 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w3 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w4 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w5 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w6 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w7 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w8 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    w9 = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))

    return RiskWeightConfig(
        ml_prediction=w1, velocity_rules=w2, merchant_reputation=w3,
        country_risk=w4, device_anomaly=w5, customer_history=w6,
        previous_alerts=w7, chargeback_history=w8, behavior_anomaly=w9
    )


# =====================================================================
# PROPERTY-BASED TEST SUITE
# =====================================================================

@settings(max_examples=100, deadline=None)
@given(txn_data=random_transaction_payload(), weights=random_risk_weights())
def test_property_1_score_boundedness_invariant(txn_data, weights):
    r"""PROPERTY 1: Composite Score Boundedness Invariant.

    Math Invariant:
        \forall \text{inputs}, \quad 0.0 \le \text{Score} \le 1000.0
    """
    txn, ml_pred = txn_data
    eng = RiskScoringEngine(weights=weights)
    res = eng.score_transaction(txn, ml_prediction=ml_pred, entity_hash="")

    assert 0.0 <= res.score <= 1000.0, f"Score out of bounds: {res.score}"
    assert not math.isnan(res.score), "Score is NaN"
    assert not math.isinf(res.score), "Score is Inf"


@settings(max_examples=100, deadline=None)
@given(
    txn_data=random_transaction_payload(),
    weights=random_risk_weights(),
    scale=st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False)
)
def test_property_2_weight_scale_invariance(txn_data, weights, scale):
    r"""PROPERTY 2: Weight Scale Invariance.

    Math Invariant:
        \forall c > 0, \quad \text{Score}(W) \equiv \text{Score}(c \cdot W)
    """
    txn, ml_pred = txn_data
    eng1 = RiskScoringEngine(weights=weights)
    res1 = eng1.score_transaction(txn, ml_prediction=ml_pred, entity_hash="")

    scaled_weights = RiskWeightConfig(
        ml_prediction=weights.ml_prediction * scale,
        velocity_rules=weights.velocity_rules * scale,
        merchant_reputation=weights.merchant_reputation * scale,
        country_risk=weights.country_risk * scale,
        device_anomaly=weights.device_anomaly * scale,
        customer_history=weights.customer_history * scale,
        previous_alerts=weights.previous_alerts * scale,
        chargeback_history=weights.chargeback_history * scale,
        behavior_anomaly=weights.behavior_anomaly * scale,
    )
    eng2 = RiskScoringEngine(weights=scaled_weights)
    res2 = eng2.score_transaction(txn, ml_prediction=ml_pred, entity_hash="")

    # Invariance check (if sum(W) > 0)
    total_w = sum(scaled_weights.to_dict().values())
    if total_w > 1e-6:
        assert abs(res1.score - res2.score) < 1e-4, f"Scale invariance violated: {res1.score} vs {res2.score}"


@settings(max_examples=100, deadline=None)
@given(
    txn_data=random_transaction_payload(),
    p1=st.floats(min_value=0.0, max_value=1.0),
    p2=st.floats(min_value=0.0, max_value=1.0)
)
def test_property_3_ml_prediction_monotonicity(txn_data, p1, p2):
    r"""PROPERTY 3: ML Prediction Signal Monotonicity.

    Math Invariant:
        p_1 \ge p_2 \implies \text{Score}(p_1) \ge \text{Score}(p_2)
    """
    txn, _ = txn_data
    high_p = max(p1, p2)
    low_p = min(p1, p2)

    res_high = engine.score_transaction(txn, ml_prediction=high_p, entity_hash="")
    res_low  = engine.score_transaction(txn, ml_prediction=low_p, entity_hash="")

    assert res_high.score >= res_low.score, f"Monotonicity violated: p_high={high_p} score={res_high.score} < p_low={low_p} score={res_low.score}"


@settings(max_examples=100, deadline=None)
@given(score_val=st.floats(min_value=0.0, max_value=1000.0))
def test_property_4_risk_level_partition_completeness(score_val):
    r"""PROPERTY 4: Risk Level Partition Completeness & Monotonicity.

    Math Invariant:
        \forall S \in [0, 1000], \quad \text{risk\_level}(S) \in \{\text{minimal, low, medium, high, critical}\}
    """
    rs = RiskScore(score=score_val, signals=[])
    lvl = rs.risk_level
    assert lvl in {"minimal", "low", "medium", "high", "critical"}, f"Invalid tier: {lvl}"

    if score_val >= 800:
        assert lvl == "critical"
    elif score_val >= 600:
        assert lvl == "high"
    elif score_val >= 400:
        assert lvl == "medium"
    elif score_val >= 200:
        assert lvl == "low"
    else:
        assert lvl == "minimal"


@settings(max_examples=100, deadline=None)
@given(txn_data=random_transaction_payload(), weights=random_risk_weights())
def test_property_5_top_signals_ranking_invariant(txn_data, weights):
    r"""PROPERTY 5: Top Signals Explainability Ranking Invariant.

    Math Invariant:
        \forall k, \quad w_{(k)} \cdot s_{(k)} \ge w_{(k+1)} \cdot s_{(k+1)}
    """
    txn, ml_pred = txn_data
    eng = RiskScoringEngine(weights=weights)
    res = eng.score_transaction(txn, ml_prediction=ml_pred, entity_hash="")

    top = res.top_signals
    for i in range(len(top) - 1):
        assert top[i].weighted_score >= top[i+1].weighted_score - 1e-12, (
            f"Sort invariant violated at index {i}: {top[i].weighted_score} < {top[i+1].weighted_score}"
        )


@settings(max_examples=50, deadline=None)
@given(
    partial_keys=st.sets(st.sampled_from([
        "velocity", "merchant_risk_score", "merchant_category",
        "country_code", "device_type", "customer_history_score",
        "account_age_days", "transaction_amount"
    ])),
    ml_pred=st.floats(min_value=0.0, max_value=1.0)
)
def test_property_6_missing_field_robustness(partial_keys, ml_pred):
    r"""PROPERTY 6: Missing Field Robustness Invariant.

    Math Invariant:
        \forall \text{KeySubsets} \subseteq \text{Fields}, \quad \text{Engine}(\text{Subset}) \text{ succeeds without KeyError}.
    """
    full_txn = {
        "velocity": 5.0, "merchant_risk_score": 0.5, "merchant_category": "crypto",
        "country_code": "US", "device_type": "web_browser", "customer_history_score": 0.8,
        "account_age_days": 100, "transaction_amount": 250.0
    }
    partial_txn = {k: full_txn[k] for k in partial_keys}

    res = engine.score_transaction(partial_txn, ml_prediction=ml_pred, entity_hash="")
    assert 0.0 <= res.score <= 1000.0
    assert len(res.signals) == 9
