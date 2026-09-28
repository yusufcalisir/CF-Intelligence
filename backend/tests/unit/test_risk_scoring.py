"""Deterministic 9-Signal Risk Scoring Arithmetic & Hand-Computed Test Vectors.

Verifies:
  1. Exact floating-point calculation of composite risk score:
     S(x) = 1000 * sum(w_i * s_i) / sum(w_i)
  2. Hand-computed test vectors across canonical fraud, clean, and edge scenarios.
  3. Mathematical properties:
     - Convex combination bounds: min(s) <= S/1000 <= max(s)
     - Scale invariance: weights multiplied by c > 0 yields identical S(x)
     - Weight monotonicity: increasing weight on highest signal increases composite score
  4. Top-k signal attribution sorting.
  5. Optional GNN topological risk integration.
"""

from __future__ import annotations

from app.domain.risk_engine import (
    DEFAULT_WEIGHTS,
    STANDARD_SIGNAL_NAMES,
    PolicyAction,
    RiskTier,
    SignalWeights,
    calculate_weighted_score,
)


class TestDeterministicTestVectors:
    """Verifies hand-computed deterministic test vectors against exact arithmetic."""

    def test_vector_all_zeros_clean(self) -> None:
        """Vector 1: All 9 signals = 0.0 -> Score = 0.0, Tier = MINIMAL, Action = ALLOW."""
        signals = {name: 0.0 for name in STANDARD_SIGNAL_NAMES}
        res = calculate_weighted_score(signals)

        assert res.score == 0.0
        assert res.normalized_score == 0.0
        assert res.tier == RiskTier.MINIMAL
        assert res.decision == PolicyAction.ALLOW
        assert len(res.signals) == 9
        for s in res.signals:
            assert s.weighted_score == 0.0

    def test_vector_all_ones_maximum_fraud(self) -> None:
        """Vector 2: All 9 signals = 1.0 -> Score = 1000.0, Tier = CRITICAL, Action = BLOCK."""
        signals = {name: 1.0 for name in STANDARD_SIGNAL_NAMES}
        res = calculate_weighted_score(signals)

        assert res.score == 1000.0
        assert res.normalized_score == 1.0
        assert res.tier == RiskTier.CRITICAL
        assert res.decision == PolicyAction.BLOCK_TRANSACTION

    def test_vector_uniform_half(self) -> None:
        """Vector 3: All 9 signals = 0.50 -> Score = 500.0, Tier = MEDIUM, Action = REQUIRE_MFA."""
        signals = {name: 0.5 for name in STANDARD_SIGNAL_NAMES}
        res = calculate_weighted_score(signals)

        assert res.score == 500.0
        assert res.normalized_score == 0.50
        assert res.tier == RiskTier.MEDIUM
        assert res.decision == PolicyAction.REQUIRE_MFA

    def test_vector_single_signal_ml_only(self) -> None:
        """Vector 4: Only ML prediction = 1.0, all others 0.0.

        Hand calculation:
            Weight of ml_prediction = 0.25
            Total weight = 1.00
            S = 1000 * (0.25 * 1.0) / 1.0 = 250.0
            Tier: LOW [200.0, 400.0) -> ALLOW
        """
        signals = {name: 0.0 for name in STANDARD_SIGNAL_NAMES}
        signals["ml_prediction"] = 1.0

        res = calculate_weighted_score(signals)
        assert abs(res.score - 250.0) < 1e-5
        assert res.tier == RiskTier.LOW
        assert res.decision == PolicyAction.ALLOW
        assert res.top_signals[0].signal_name == "ml_prediction"
        assert abs(res.top_signals[0].weighted_score - 0.25) < 1e-5

    def test_vector_single_signal_velocity_only(self) -> None:
        """Vector 5: Only velocity = 1.0, all others 0.0.

        Hand calculation:
            Weight of velocity = 0.15
            S = 1000 * 0.15 = 150.0 -> Tier MINIMAL [0, 200)
        """
        signals = {name: 0.0 for name in STANDARD_SIGNAL_NAMES}
        signals["velocity_rules"] = 1.0

        res = calculate_weighted_score(signals)
        assert abs(res.score - 150.0) < 1e-5
        assert res.tier == RiskTier.MINIMAL
        assert res.decision == PolicyAction.ALLOW

    def test_vector_high_risk_hand_computed(self) -> None:
        """Vector 6: Realistic high-risk multi-signal composite.

        Inputs:
            ml_prediction:       0.90  (w=0.25 -> 0.225)
            velocity_rules:      0.80  (w=0.15 -> 0.120)
            merchant_reputation: 0.85  (w=0.10 -> 0.085)
            country_risk:        1.00  (w=0.10 -> 0.100)
            device_anomaly:      0.40  (w=0.08 -> 0.032)
            customer_history:    0.70  (w=0.10 -> 0.070)
            previous_alerts:     0.60  (w=0.08 -> 0.048)
            chargeback_history:  0.50  (w=0.07 -> 0.035)
            behavior_anomaly:    0.60  (w=0.07 -> 0.042)

        Hand calculation sum:
            0.225 + 0.120 + 0.085 + 0.100 + 0.032 + 0.070 + 0.048 + 0.035 + 0.042
            = 0.7570
            Total weight = 1.0000
            Score = 757.0 -> Tier: HIGH [600, 800) -> Action: HOLD_FOR_REVIEW
        """
        signals = {
            "ml_prediction": 0.90,
            "velocity_rules": 0.80,
            "merchant_reputation": 0.85,
            "country_risk": 1.00,
            "device_anomaly": 0.40,
            "customer_history": 0.70,
            "previous_alerts": 0.60,
            "chargeback_history": 0.50,
            "behavior_anomaly": 0.60,
        }

        res = calculate_weighted_score(signals)
        assert abs(res.score - 757.0) < 1e-4
        assert res.tier == RiskTier.HIGH
        assert res.decision == PolicyAction.HOLD_FOR_REVIEW
        assert res.top_signals[0].signal_name == "ml_prediction"

    def test_vector_critical_immediate_block(self) -> None:
        """Vector 7: Score >= 900.0 triggers BLOCK_TRANSACTION."""
        signals = {name: 0.92 for name in STANDARD_SIGNAL_NAMES}
        res = calculate_weighted_score(signals)

        assert res.score == 920.0
        assert res.tier == RiskTier.CRITICAL
        assert res.decision == PolicyAction.BLOCK_TRANSACTION

    def test_vector_critical_sar_escalation(self) -> None:
        """Vector 8: Score in [800.0, 900.0) triggers ESCALATE_TO_SAR."""
        signals = {name: 0.85 for name in STANDARD_SIGNAL_NAMES}
        res = calculate_weighted_score(signals)

        assert res.score == 850.0
        assert res.tier == RiskTier.CRITICAL
        assert res.decision == PolicyAction.ESCALATE_TO_SAR


class TestMathematicalProperties:
    """Verifies axiomatic mathematical invariants of weighted linear combinations."""

    def test_convex_combination_bound(self) -> None:
        """Composite score normalized to [0, 1] must lie within [min(s_i), max(s_i)]."""
        signals = {
            "ml_prediction": 0.80,
            "velocity_rules": 0.30,
            "merchant_reputation": 0.45,
            "country_risk": 0.60,
            "device_anomaly": 0.20,
            "customer_history": 0.35,
            "previous_alerts": 0.10,
            "chargeback_history": 0.25,
            "behavior_anomaly": 0.50,
        }
        res = calculate_weighted_score(signals)
        norm_score = res.normalized_score

        min_val = min(signals.values())
        max_val = max(signals.values())
        assert min_val <= norm_score <= max_val

    def test_scale_invariance_of_weights(self) -> None:
        """Multiplying all weights by a positive scalar c > 0 must not change the score."""
        signals = {
            "ml_prediction": 0.70,
            "velocity_rules": 0.40,
            "merchant_reputation": 0.50,
            "country_risk": 0.80,
            "device_anomaly": 0.30,
            "customer_history": 0.20,
            "previous_alerts": 0.10,
            "chargeback_history": 0.60,
            "behavior_anomaly": 0.40,
        }
        base_res = calculate_weighted_score(signals)

        # Scale weights by 5.0
        scaled_weights = {k: v * 5.0 for k, v in DEFAULT_WEIGHTS.items()}
        scaled_res = calculate_weighted_score(signals, weights=scaled_weights)

        assert abs(base_res.score - scaled_res.score) < 1e-4

    def test_weight_monotonicity(self) -> None:
        """Increasing the weight of a signal that is higher than average must increase composite score."""
        signals = {
            "ml_prediction": 1.0,  # highest signal
            "velocity_rules": 0.2,
            "merchant_reputation": 0.2,
            "country_risk": 0.2,
            "device_anomaly": 0.2,
            "customer_history": 0.2,
            "previous_alerts": 0.2,
            "chargeback_history": 0.2,
            "behavior_anomaly": 0.2,
        }

        # Baseline: ml_prediction weight = 0.25
        res_baseline = calculate_weighted_score(signals)

        # Higher ML weight: ml_prediction weight = 0.50
        custom_weights = SignalWeights(ml_prediction=0.50)
        res_higher = calculate_weighted_score(signals, weights=custom_weights)

        assert res_higher.score > res_baseline.score

    def test_top_k_signals_sorting(self) -> None:
        """top_signals must be sorted in strictly descending order of weighted_score."""
        signals = {
            "ml_prediction": 0.90,  # 0.25 * 0.90 = 0.225
            "velocity_rules": 0.10,  # 0.15 * 0.10 = 0.015
            "merchant_reputation": 0.95,  # 0.10 * 0.95 = 0.095
            "country_risk": 0.80,  # 0.10 * 0.80 = 0.080
            "device_anomaly": 0.10,
            "customer_history": 0.10,
            "previous_alerts": 0.10,
            "chargeback_history": 0.10,
            "behavior_anomaly": 0.10,
        }
        res = calculate_weighted_score(signals)

        for i in range(len(res.top_signals) - 1):
            assert (
                res.top_signals[i].weighted_score >= res.top_signals[i + 1].weighted_score - 1e-9
            )

    def test_gnn_topological_risk_signal_activation(self) -> None:
        """When GNN weight > 0, 10th signal is integrated into the linear sum."""
        signals = {name: 0.5 for name in STANDARD_SIGNAL_NAMES}
        signals["gnn_topological_risk"] = 0.90

        weights = SignalWeights(gnn_topological_risk=0.15)
        res = calculate_weighted_score(signals, weights=weights)

        assert any(s.signal_name == "gnn_topological_risk" for s in res.signals)
        assert len(res.signals) == 10
        # GNN score (0.90) is above 0.50, so score should be higher than 500
        assert res.score > 500.0
