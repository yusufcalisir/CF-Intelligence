"""Boundary, Fault-Tolerance & Edge Case Tests for Risk Scoring Engine.

Verifies:
  1. Strict half-open interval boundaries between all risk tiers:
     [0, 200) -> MINIMAL
     [200, 400) -> LOW
     [400, 600) -> MEDIUM
     [600, 800) -> HIGH
     [800, 1000] -> CRITICAL
  2. Gateway policy decision boundary at 900.0 (SAR escalation vs immediate block).
  3. Clamping and sanitization of out-of-bounds, negative, overflow, NaN, and Inf signals.
  4. Missing signals, empty inputs, and unrecognized dictionary keys.
  5. Weight configuration invariant enforcement (rejection of negative or non-finite weights).
  6. Zero-weight edge case division-by-zero protection.
"""

from __future__ import annotations

import math

import pytest

from app.domain.risk_engine import (
    DEFAULT_WEIGHTS,
    PolicyAction,
    RiskTier,
    SignalWeights,
    calculate_weighted_score,
    clamp_signal,
    classify_risk_tier,
    map_tier_to_action,
)


class TestRiskTierBoundaryTransitions:
    """Verifies strict half-open interval boundaries partitioning [0.0, 1000.0]."""

    @pytest.mark.parametrize(
        ("score", "expected_tier"),
        [
            (0.0, RiskTier.MINIMAL),
            (0.1, RiskTier.MINIMAL),
            (199.9, RiskTier.MINIMAL),
            (200.0, RiskTier.LOW),       # Boundary: 200.0 inclusive
            (200.1, RiskTier.LOW),
            (399.9, RiskTier.LOW),
            (400.0, RiskTier.MEDIUM),    # Boundary: 400.0 inclusive
            (400.1, RiskTier.MEDIUM),
            (599.9, RiskTier.MEDIUM),
            (600.0, RiskTier.HIGH),      # Boundary: 600.0 inclusive
            (600.1, RiskTier.HIGH),
            (799.9, RiskTier.HIGH),
            (800.0, RiskTier.CRITICAL),  # Boundary: 800.0 inclusive
            (800.1, RiskTier.CRITICAL),
            (999.9, RiskTier.CRITICAL),
            (1000.0, RiskTier.CRITICAL),
        ],
    )
    def test_tier_classification_boundary(self, score: float, expected_tier: RiskTier) -> None:
        assert classify_risk_tier(score) == expected_tier

    def test_out_of_bounds_score_clamping(self) -> None:
        """Scores < 0 clamp to MINIMAL, scores > 1000 clamp to CRITICAL."""
        assert classify_risk_tier(-50.0) == RiskTier.MINIMAL
        assert classify_risk_tier(1500.0) == RiskTier.CRITICAL

    def test_decision_boundary_at_900(self) -> None:
        """Scores 800.0 to 899.9 map to ESCALATE_TO_SAR; >= 900.0 map to BLOCK_TRANSACTION."""
        assert map_tier_to_action(RiskTier.CRITICAL, 800.0) == PolicyAction.ESCALATE_TO_SAR
        assert map_tier_to_action(RiskTier.CRITICAL, 899.9) == PolicyAction.ESCALATE_TO_SAR
        assert map_tier_to_action(RiskTier.CRITICAL, 900.0) == PolicyAction.BLOCK_TRANSACTION
        assert map_tier_to_action(RiskTier.CRITICAL, 950.0) == PolicyAction.BLOCK_TRANSACTION


class TestSignalClampingAndSanitization:
    """Verifies pure clamping function against anomalous numerical types."""

    def test_negative_signal_clamped_to_zero(self) -> None:
        assert clamp_signal(-0.01) == 0.0
        assert clamp_signal(-100.0) == 0.0

    def test_overflow_signal_clamped_to_one(self) -> None:
        assert clamp_signal(1.0001) == 1.0
        assert clamp_signal(50.0) == 1.0

    def test_normal_signals_unaffected(self) -> None:
        assert clamp_signal(0.0) == 0.0
        assert clamp_signal(0.35) == 0.35
        assert clamp_signal(1.0) == 1.0

    def test_nan_and_inf_sanitized_to_zero(self) -> None:
        assert clamp_signal(float("nan")) == 0.0
        assert clamp_signal(float("inf")) == 0.0
        assert clamp_signal(float("-inf")) == 0.0

    def test_non_numeric_values_sanitized(self) -> None:
        assert clamp_signal("invalid_string") == 0.0  # type: ignore
        assert clamp_signal(None) == 0.0  # type: ignore


class TestInputMissingSignalsAndRobustness:
    """Verifies calculation behavior when signals are partially missing or malformed."""

    def test_empty_signals_dictionary(self) -> None:
        """Empty input dictionary defaults all 9 signals to 0.0."""
        res = calculate_weighted_score({})
        assert res.score == 0.0
        assert res.tier == RiskTier.MINIMAL
        assert res.decision == PolicyAction.ALLOW
        assert len(res.signals) == 9

    def test_partial_signals_dictionary(self) -> None:
        """Supplying only a subset of signals defaults unspecified signals to 0.0."""
        # Only ML=0.80 and velocity=0.50 supplied
        # ml: 0.25 * 0.80 = 0.20
        # velocity: 0.15 * 0.50 = 0.075
        # Total = 0.275 -> Score = 275.0 -> Tier LOW
        signals = {"ml_prediction": 0.80, "velocity_rules": 0.50}
        res = calculate_weighted_score(signals)

        assert abs(res.score - 275.0) < 1e-4
        assert res.tier == RiskTier.LOW

    def test_unknown_extra_keys_ignored(self) -> None:
        """Unrecognized keys in signal dictionary do not alter standard weights."""
        signals = {
            "ml_prediction": 0.5,
            "unrecognized_random_signal": 0.99,
            "another_extra_key": 100.0,
        }
        res = calculate_weighted_score(signals)
        # ml_prediction = 0.5 * 0.25 = 0.125 -> Score = 125.0
        assert abs(res.score - 125.0) < 1e-4

    def test_nan_values_in_signals_dict(self) -> None:
        """NaN values in signal dictionary are safely clamped to 0.0 without error."""
        signals = {
            "ml_prediction": float("nan"),
            "velocity_rules": 0.50,  # 0.15 * 0.50 = 0.075
        }
        res = calculate_weighted_score(signals)
        assert abs(res.score - 75.0) < 1e-4
        assert not math.isnan(res.score)


class TestWeightConfigurationInvariants:
    """Verifies SignalWeights validation contracts and edge states."""

    def test_rejects_negative_weight(self) -> None:
        with pytest.raises(ValueError, match="must be non-negative"):
            SignalWeights(ml_prediction=-0.1)

    def test_rejects_nan_or_inf_weight(self) -> None:
        with pytest.raises(ValueError, match="must be finite"):
            SignalWeights(velocity_rules=float("nan"))

        with pytest.raises(ValueError, match="must be finite"):
            SignalWeights(country_risk=float("inf"))

    def test_zero_total_weight_fallback(self) -> None:
        """When all weights are set to 0.0, score returns 0.0 safely without ZeroDivisionError."""
        zero_weights = SignalWeights(**{k: 0.0 for k in DEFAULT_WEIGHTS})
        signals = {"ml_prediction": 1.0, "velocity_rules": 1.0}

        res = calculate_weighted_score(signals, weights=zero_weights)
        assert res.score == 0.0
        assert res.normalized_score == 0.0
        assert res.tier == RiskTier.MINIMAL

    def test_signal_and_composite_serialization(self) -> None:
        """Verifies dictionary conversion for API and JSON serialization."""
        signals = {"ml_prediction": 0.60, "country_risk": 0.80}
        res = calculate_weighted_score(signals)

        d = res.to_dict()
        assert "score" in d
        assert "tier" in d
        assert "decision" in d
        assert "signals" in d
        assert "top_signals" in d
        assert len(d["signals"]) == 9
