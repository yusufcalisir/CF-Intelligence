"""Deterministic Risk Scoring Engine Domain Module.

Formally implements the 9-signal weighted arithmetic, strict half-open interval
risk tier classifications, and boundary handling for composite fraud intelligence.

Mathematical Specification:
    Given independent normalized risk signals s_i in [0.0, 1.0] and non-negative
    weights w_i >= 0.0 for i in {1, ..., M}:

        S(x) = 1000.0 * (sum_{i=1}^M w_i * clamp(s_i(x), 0, 1)) / (sum_{i=1}^M w_i)

    When weights are normalized (sum w_i = 1.0):
        S(x) = 1000.0 * sum_{i=1}^M w_i * s_i(x)

Risk Tier Partition (Strict Half-Open Intervals on [0.0, 1000.0]):
    - MINIMAL:  [  0.0,  200.0)
    - LOW:      [200.0,  400.0)
    - MEDIUM:   [400.0,  600.0)
    - HIGH:     [600.0,  800.0)
    - CRITICAL: [800.0, 1000.0]

References:
    - Basel Committee on Banking Supervision (BCBS) Credit & Operational Risk Principles
    - EU AI Act Article 14 / SR 11-7 Algorithmic Transparency & Model Validation
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RiskTier(StrEnum):
    """Strict discrete risk tiers partitioning the [0.0, 1000.0] risk score spectrum."""

    MINIMAL = "MINIMAL"    # [0, 200)
    LOW = "LOW"            # [200, 400)
    MEDIUM = "MEDIUM"      # [400, 600)
    HIGH = "HIGH"          # [600, 800)
    CRITICAL = "CRITICAL"  # [800, 1000]


class PolicyAction(StrEnum):
    """Operational gateway disposition mapped deterministically from risk tiers."""

    ALLOW = "ALLOW"
    REQUIRE_MFA = "REQUIRE_MFA"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    BLOCK_TRANSACTION = "BLOCK_TRANSACTION"
    ESCALATE_TO_SAR = "ESCALATE_TO_SAR"


# ---------------------------------------------------------------------------
# Signal Definitions and Default Weights (Sum = 1.00)
# ---------------------------------------------------------------------------

STANDARD_SIGNAL_NAMES: tuple[str, ...] = (
    "ml_prediction",
    "velocity_rules",
    "merchant_reputation",
    "country_risk",
    "device_anomaly",
    "customer_history",
    "previous_alerts",
    "chargeback_history",
    "behavior_anomaly",
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "ml_prediction": 0.25,
    "velocity_rules": 0.15,
    "merchant_reputation": 0.10,
    "country_risk": 0.10,
    "device_anomaly": 0.08,
    "customer_history": 0.10,
    "previous_alerts": 0.08,
    "chargeback_history": 0.07,
    "behavior_anomaly": 0.07,
    "gnn_topological_risk": 0.00,
}


@dataclass(frozen=True)
class RiskSignalValue:
    """Evaluated single dimension of transaction risk."""

    signal_name: str
    raw_value: float
    normalized_score: float
    weight: float
    weighted_score: float
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_name": self.signal_name,
            "raw_value": round(self.raw_value, 4),
            "normalized_score": round(self.normalized_score, 4),
            "weight": round(self.weight, 4),
            "weighted_score": round(self.weighted_score, 4),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class SignalWeights:
    """Configurable non-negative weights for the composite scoring arithmetic."""

    ml_prediction: float = 0.25
    velocity_rules: float = 0.15
    merchant_reputation: float = 0.10
    country_risk: float = 0.10
    device_anomaly: float = 0.08
    customer_history: float = 0.10
    previous_alerts: float = 0.08
    chargeback_history: float = 0.07
    behavior_anomaly: float = 0.07
    gnn_topological_risk: float = 0.00

    def __post_init__(self) -> None:
        for k, v in self.to_dict().items():
            if v < 0.0:
                raise ValueError(f"Weight '{k}' must be non-negative, got {v}")
            if math.isnan(v) or math.isinf(v):
                raise ValueError(f"Weight '{k}' must be finite, got {v}")

    @property
    def total_weight(self) -> float:
        return sum(self.to_dict().values())

    def to_dict(self) -> dict[str, float]:
        return {
            "ml_prediction": self.ml_prediction,
            "velocity_rules": self.velocity_rules,
            "merchant_reputation": self.merchant_reputation,
            "country_risk": self.country_risk,
            "device_anomaly": self.device_anomaly,
            "customer_history": self.customer_history,
            "previous_alerts": self.previous_alerts,
            "chargeback_history": self.chargeback_history,
            "behavior_anomaly": self.behavior_anomaly,
            "gnn_topological_risk": self.gnn_topological_risk,
        }

    def get(self, signal_name: str, default: float = 0.0) -> float:
        return self.to_dict().get(signal_name, default)


@dataclass(frozen=True)
class CompositeRiskScoreResult:
    """Final calculated risk decision with complete mathematical provenance."""

    score: float                      # Composite score in [0.0, 1000.0]
    normalized_score: float           # Score in [0.0, 1.0]
    tier: RiskTier                    # MINIMAL, LOW, MEDIUM, HIGH, CRITICAL
    decision: PolicyAction            # ALLOW, REQUIRE_MFA, HOLD_FOR_REVIEW, BLOCK_TRANSACTION
    signals: list[RiskSignalValue]    # Contributing signal vectors
    top_signals: list[RiskSignalValue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "normalized_score": round(self.normalized_score, 4),
            "tier": self.tier.value,
            "decision": self.decision.value,
            "signals": [s.to_dict() for s in self.signals],
            "top_signals": [s.to_dict() for s in self.top_signals],
        }


# ---------------------------------------------------------------------------
# Pure Mathematical Calculation Functions
# ---------------------------------------------------------------------------

def clamp_signal(value: float) -> float:
    """Clamps an input signal value strictly to the unit interval [0.0, 1.0].

    Sanitizes NaN and Inf to 0.0 to prevent unhandled float errors.
    """
    try:
        val = float(value)
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return max(0.0, min(1.0, val))
    except (ValueError, TypeError):
        return 0.0


def classify_risk_tier(score: float) -> RiskTier:
    """Maps composite score in [0.0, 1000.0] into mutually exclusive, exhaustive tiers.

    Enforces strict half-open interval boundaries:
        [  0.0,  200.0) -> MINIMAL
        [200.0,  400.0) -> LOW
        [400.0,  600.0) -> MEDIUM
        [600.0,  800.0) -> HIGH
        [800.0, 1000.0] -> CRITICAL
    """
    s = max(0.0, min(1000.0, float(score)))

    if s < 200.0:
        return RiskTier.MINIMAL
    if s < 400.0:
        return RiskTier.LOW
    if s < 600.0:
        return RiskTier.MEDIUM
    if s < 800.0:
        return RiskTier.HIGH
    return RiskTier.CRITICAL


def map_tier_to_action(tier: RiskTier, score: float = 0.0) -> PolicyAction:
    """Deterministically maps a classified RiskTier to standard gateway policy disposition."""
    if tier in (RiskTier.MINIMAL, RiskTier.LOW):
        return PolicyAction.ALLOW
    if tier == RiskTier.MEDIUM:
        return PolicyAction.REQUIRE_MFA
    if tier == RiskTier.HIGH:
        return PolicyAction.HOLD_FOR_REVIEW
    # CRITICAL tier: score >= 900 triggers immediate block; 800-900 triggers SAR escalation
    if score >= 900.0:
        return PolicyAction.BLOCK_TRANSACTION
    return PolicyAction.ESCALATE_TO_SAR


def calculate_weighted_score(
    signals: dict[str, float] | list[RiskSignalValue],
    weights: SignalWeights | dict[str, float] | None = None,
) -> CompositeRiskScoreResult:
    """Computes exact floating-point weighted risk score and audit provenance.

    Args:
        signals: Mapping of signal_name -> score, or list of RiskSignalValue.
        weights: Configured weights (defaults to standard 9-signal configuration).

    Returns:
        CompositeRiskScoreResult with verified floating-point arithmetic and tier.
    """
    if weights is None:
        weight_cfg = SignalWeights()
    elif isinstance(weights, dict):
        weight_cfg = SignalWeights(**{k: v for k, v in weights.items() if k in DEFAULT_WEIGHTS})
    else:
        weight_cfg = weights

    signal_map: dict[str, float] = {}
    if isinstance(signals, list):
        for s in signals:
            signal_map[s.signal_name] = s.raw_value
    else:
        signal_map = dict(signals)

    evaluated_signals: list[RiskSignalValue] = []
    total_effective_weight = 0.0
    accumulated_weighted_sum = 0.0

    # Evaluate standard signals in deterministic order
    active_keys = list(STANDARD_SIGNAL_NAMES)
    # Include any custom or extra signals (e.g. gnn_topological_risk)
    for k in signal_map:
        if k not in active_keys and k in weight_cfg.to_dict():
            active_keys.append(k)

    for sig_name in active_keys:
        raw_val = signal_map.get(sig_name, 0.0)
        norm_val = clamp_signal(raw_val)
        w = weight_cfg.get(sig_name, 0.0)
        weighted_val = w * norm_val

        evaluated_signals.append(
            RiskSignalValue(
                signal_name=sig_name,
                raw_value=raw_val if not (math.isnan(raw_val) or math.isinf(raw_val)) else 0.0,
                normalized_score=norm_val,
                weight=w,
                weighted_score=weighted_val,
                explanation=f"{sig_name}: {norm_val:.1%} (weight={w:.2f})",
            )
        )

        total_effective_weight += w
        accumulated_weighted_sum += weighted_val

    if total_effective_weight > 0.0:
        normalized_composite = min(1.0, max(0.0, accumulated_weighted_sum / total_effective_weight))
    else:
        normalized_composite = 0.0

    composite_score = round(normalized_composite * 1000.0, 1)
    tier = classify_risk_tier(composite_score)
    decision = map_tier_to_action(tier, composite_score)

    # Sort signals by weighted contribution descending
    sorted_signals = sorted(evaluated_signals, key=lambda s: s.weighted_score, reverse=True)

    return CompositeRiskScoreResult(
        score=composite_score,
        normalized_score=round(normalized_composite, 4),
        tier=tier,
        decision=decision,
        signals=evaluated_signals,
        top_signals=sorted_signals,
    )
