"""Independent Mathematical Reference Verification for Risk Scoring Engine Module.

Compares production RiskScoringEngine against pure-Python/NumPy reference
mathematical models with zero production code reuse.

Verifies:
  - Signal normalizations
  - Weighted score calculations
  - Score scaling to [0, 1000]
  - Score bounds [0, 1000]
  - Individual risk factor contributions
  - Final composite aggregation
  - Floating-point stability
"""

from __future__ import annotations

import sys
import math
import numpy as np

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.risk_engine import RiskScoringEngine
from app.domain.value_objects_phase2 import RiskWeightConfig, RiskScore


# =====================================================================
# INDEPENDENT MATHEMATICAL REFERENCE IMPLEMENTATIONS (NO PRODUCTION CODE)
# =====================================================================

REF_COUNTRY_RISK = {
    "KP": 1.00, "IR": 0.95, "MM": 0.90, "SY": 0.90, "NG": 0.85, "RU": 0.80,
    "PH": 0.75, "BR": 0.70, "TR": 0.55, "MX": 0.50, "CN": 0.45, "IN": 0.40,
    "ZA": 0.45, "AE": 0.30, "KR": 0.15, "JP": 0.10, "SG": 0.10, "AU": 0.08,
    "NL": 0.05, "DE": 0.05, "FR": 0.05, "CA": 0.04, "UK": 0.03, "US": 0.02,
}

REF_MERCHANT_RISK = {
    "gambling": 0.90, "crypto": 0.85, "wire_transfer": 0.75, "jewelry": 0.60,
    "online_marketplace": 0.45, "electronics": 0.35, "travel": 0.25, "atm_withdrawal": 0.30,
    "entertainment": 0.15, "dining": 0.05, "grocery": 0.03, "fuel": 0.03,
    "clothing": 0.05, "healthcare": 0.02, "education": 0.02, "home": 0.05,
    "automotive": 0.08, "subscription": 0.05, "insurance": 0.03, "charity": 0.10,
}

REF_DEVICE_SCORES = {
    "mobile_app": 0.10, "web_browser": 0.15, "pos_terminal": 0.05, "atm": 0.35, "phone_banking": 0.40,
}


def ref_eval_ml_prediction(p: float) -> float:
    return min(1.0, p)


def ref_eval_velocity(v: float) -> float:
    return min(1.0, max(0.0, (v - 2.0) / 8.0))


def ref_eval_merchant_reputation(m_score: float, cat: str) -> float:
    c_risk = REF_MERCHANT_RISK.get(cat, 0.10)
    return min(1.0, 0.6 * m_score + 0.4 * c_risk)


def ref_eval_country_risk(code: str) -> float:
    return REF_COUNTRY_RISK.get(code, 0.15)


def ref_eval_device_anomaly(dev: str) -> float:
    return REF_DEVICE_SCORES.get(dev, 0.20)


def ref_eval_customer_history(hist_score: float, age_days: int) -> float:
    r = 1.0 - min(1.0, hist_score)
    if age_days < 30:
        r = min(1.0, r + 0.3)
    return r


def ref_eval_previous_alerts(cnt: int) -> float:
    return min(1.0, float(cnt) / 5.0) if cnt > 0 else 0.0


def ref_eval_chargeback_history(rate: float) -> float:
    return min(1.0, rate * 10.0)


def ref_eval_behavior_anomaly(amt: float, mean_amt: float, std_amt: float) -> float:
    z = abs(amt - mean_amt) / std_amt if std_amt > 0 else 0.0
    return min(1.0, max(0.0, (z - 1.0) / 3.0))


def ref_composite_score(signals: list[tuple[float, float]]) -> float:
    """signals is list of (norm_score, weight)."""
    total_w = sum(w for _, w in signals)
    if total_w == 0:
        return 0.0
    weighted_sum = sum(s * w for s, w in signals)
    comp = min(1.0, weighted_sum / total_w)
    return round(comp * 1000.0, 1)


# =====================================================================
# VERIFICATION SUITE
# =====================================================================

def run_risk_scoring_reference_verification():
    print("=" * 85)
    print("RISK SCORING ENGINE: INDEPENDENT MATHEMATICAL REFERENCE VERIFICATION")
    print("=" * 85)

    engine = RiskScoringEngine()
    overall_pass = True

    # ----------------------------------------------------------------
    # Test 1: Standard Transactions (100 synthetic cases, no feature store override)
    # ----------------------------------------------------------------
    print("\n--- 1. Standard Transaction Scoring Accuracy (100 cases) ---")
    rng = np.random.default_rng(42)

    max_abs_err = 0.0
    max_rel_err = 0.0

    for i in range(100):
        ml_pred = float(rng.uniform(0.0, 1.0))
        velocity = float(rng.uniform(0.0, 20.0))
        m_score = float(rng.uniform(0.0, 1.0))
        cat = str(rng.choice(list(REF_MERCHANT_RISK.keys())))
        country = str(rng.choice(list(REF_COUNTRY_RISK.keys())))
        device = str(rng.choice(list(REF_DEVICE_SCORES.keys())))
        hist_score = float(rng.uniform(0.0, 1.0))
        age_days = int(rng.integers(1, 1000))
        alerts = int(rng.integers(0, 10))
        cb_rate = float(rng.uniform(0.0, 0.2))
        amount = float(rng.uniform(10.0, 5000.0))
        mean_amt = 500.0
        std_amt = 200.0

        txn = {
            "velocity": velocity,
            "merchant_risk_score": m_score,
            "merchant_category": cat,
            "country_code": country,
            "device_type": device,
            "customer_history_score": hist_score,
            "account_age_days": age_days,
            "transaction_amount": amount,
        }
        # Clear histories
        engine._alert_history.clear()
        engine._chargeback_history.clear()
        engine._behavior_baselines.clear()

        # Score without entity_hash so feature store doesn't override txn payload
        prod_res = engine.score_transaction(txn, ml_prediction=ml_pred, entity_hash="")
        prod_score = prod_res.score

        # Reference Execution (without baseline -> behavior anomaly defaults to 0.1)
        ref_signals = [
            (ref_eval_ml_prediction(ml_pred), 0.25),
            (ref_eval_velocity(velocity), 0.15),
            (ref_eval_merchant_reputation(m_score, cat), 0.10),
            (ref_eval_country_risk(country), 0.10),
            (ref_eval_device_anomaly(device), 0.08),
            (ref_eval_customer_history(hist_score, age_days), 0.10),
            (ref_eval_previous_alerts(0), 0.08),
            (ref_eval_chargeback_history(0.0), 0.07),
            (0.10, 0.07),  # no baseline established -> defaults to 0.10
        ]
        ref_score = ref_composite_score(ref_signals)

        abs_err = abs(prod_score - ref_score)
        rel_err = abs_err / (ref_score + 1e-12)

        max_abs_err = max(max_abs_err, abs_err)
        max_rel_err = max(max_rel_err, rel_err)

    print(f"100 Cases Verified | Max Abs Error: {max_abs_err:.2e} | Max Rel Error: {max_rel_err:.2e}")
    passed = max_abs_err < 1e-10
    overall_pass &= passed
    print(f"Standard Scoring Precision: {'PASSED' if passed else 'FAILED'}")

    # ----------------------------------------------------------------
    # Test 2: Individual Signal Evaluator Equivalence
    # ----------------------------------------------------------------
    print("\n--- 2. Individual Signal Evaluator Equivalence ---")
    sig_pass = True

    # ML prediction
    s_prod = engine._eval_ml_prediction(0.75).normalized_score
    s_ref = ref_eval_ml_prediction(0.75)
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Velocity
    s_prod = engine._eval_velocity({"velocity": 6.0}).normalized_score
    s_ref = ref_eval_velocity(6.0)  # (6-2)/8 = 0.5
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Merchant reputation
    s_prod = engine._eval_merchant_reputation({"merchant_category": "crypto", "merchant_risk_score": 0.8}).normalized_score
    s_ref = ref_eval_merchant_reputation(0.8, "crypto")  # 0.6*0.8 + 0.4*0.85 = 0.82
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Country risk
    s_prod = engine._eval_country_risk({"country_code": "KP"}).normalized_score
    s_ref = ref_eval_country_risk("KP")  # 1.00
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Device anomaly
    s_prod = engine._eval_device_anomaly({"device_type": "atm"}).normalized_score
    s_ref = ref_eval_device_anomaly("atm")  # 0.35
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Customer history
    s_prod = engine._eval_customer_history({"customer_history_score": 0.8, "account_age_days": 20}).normalized_score
    s_ref = ref_eval_customer_history(0.8, 20)  # (1-0.8) + 0.3 = 0.5
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Previous alerts
    engine._alert_history["test_entity"] = 3
    s_prod = engine._eval_previous_alerts("test_entity").normalized_score
    s_ref = ref_eval_previous_alerts(3)  # 3/5 = 0.6
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Chargeback history
    engine.register_chargeback("test_entity", 0.05)
    s_prod = engine._eval_chargeback_history("test_entity").normalized_score
    s_ref = ref_eval_chargeback_history(0.05)  # 0.05 * 10 = 0.5
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    # Behavior anomaly
    engine.register_baseline("test_entity", {"mean_amount": 100.0, "std_amount": 50.0})
    s_prod = engine._eval_behavior_anomaly({"transaction_amount": 250.0}, "test_entity").normalized_score
    s_ref = ref_eval_behavior_anomaly(250.0, 100.0, 50.0)  # z=(250-100)/50=3.0 -> (3-1)/3 = 0.66666...
    sig_pass &= (abs(s_prod - s_ref) < 1e-12)

    overall_pass &= sig_pass
    print(f"All 9 Individual Evaluators Match Reference: {'PASSED' if sig_pass else 'FAILED'}")

    # ----------------------------------------------------------------
    # Test 3: Zero Weights Invariant
    # ----------------------------------------------------------------
    print("\n--- 3. Zero Weights Invariant ---")
    zero_weights_engine = RiskScoringEngine(weights=RiskWeightConfig(
        ml_prediction=0.0, velocity_rules=0.0, merchant_reputation=0.0,
        country_risk=0.0, device_anomaly=0.0, customer_history=0.0,
        previous_alerts=0.0, chargeback_history=0.0, behavior_anomaly=0.0
    ))
    res_zero = zero_weights_engine.score_transaction({"velocity": 10.0}, ml_prediction=1.0)
    passed_zero = res_zero.score == 0.0
    overall_pass &= passed_zero
    print(f"Zero Weights Score: {res_zero.score} | Expected: 0.0 | {'PASSED' if passed_zero else 'FAILED'}")

    # ----------------------------------------------------------------
    # Test 4: Risk Tier Monotonic Partitioning Check
    # ----------------------------------------------------------------
    print("\n--- 4. Risk Tier Monotonic Partitioning Check ---")
    tier_cases = [
        (0.0, "minimal"), (150.0, "minimal"), (199.9, "minimal"),
        (200.0, "low"), (350.0, "low"), (399.9, "low"),
        (400.0, "medium"), (550.0, "medium"), (599.9, "medium"),
        (600.0, "high"), (750.0, "high"), (799.9, "high"),
        (800.0, "critical"), (950.0, "critical"), (1000.0, "critical"),
    ]

    tier_pass = True
    for s_val, expected_tier in tier_cases:
        rs = RiskScore(score=s_val, signals=[])
        actual_tier = rs.risk_level
        if actual_tier != expected_tier:
            tier_pass = False
            print(f"FAIL: Score {s_val} mapped to {actual_tier}, expected {expected_tier}")

    overall_pass &= tier_pass
    print(f"Risk Tier Partitioning (15 boundary cases): {'PASSED' if tier_pass else 'FAILED'}")

    # ----------------------------------------------------------------
    # Final Report Summary
    # ----------------------------------------------------------------
    print("\n" + "=" * 85)
    status = "ALL TESTS PASSED" if overall_pass else "SOME TESTS FAILED"
    print(f"RISK SCORING ENGINE REFERENCE VERIFICATION: {status}")
    print("=" * 85)


if __name__ == "__main__":
    run_risk_scoring_reference_verification()
