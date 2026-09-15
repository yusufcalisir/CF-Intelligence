"""Domain value objects for Rényi Differential Privacy (RDP) & Dynamic DP Budget Auto-Scaler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Standard evaluation orders for Rényi DP numerical optimization
DEFAULT_RDP_ORDERS: list[float] = [
    1.5,
    1.75,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    12.0,
    16.0,
    24.0,
    32.0,
    48.0,
    64.0,
    128.0,
]


@dataclass(frozen=True)
class DynamicNoiseCalibration:
    """Calibrated noise multiplier parameters for an individual FL training round."""

    round_id: int
    current_loss: float
    loss_velocity: float
    batch_size: int
    sample_ratio_q: float
    calibrated_sigma: float
    gradient_clip_c: float
    instantaneous_epsilon: float
    optimal_alpha: float
    node_id: str = "global"


@dataclass(frozen=True)
class RDPAccountantState:
    """Cumulative state of Rényi Differential Privacy accountant across all completed rounds."""

    total_rounds: int
    cumulative_rdp: dict[float, float]  # alpha -> cumulative_eps(alpha)
    target_epsilon: float
    target_delta: float
    current_epsilon_at_delta: float
    optimal_alpha_order: float
    budget_exhaustion_pct: float
    is_budget_exceeded: bool
    node_id: str = "global"


@dataclass(frozen=True)
class PrivacyAuditEvent:
    """Cryptographically chained audit trail record for dynamic DP calibration events."""

    step: int
    timestamp_utc: str
    round_id: int
    node_id: str
    calibrated_sigma: float
    gradient_clip_c: float
    cumulative_epsilon: float
    target_epsilon: float
    risk_tier: str
    previous_hash: str
    block_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "timestamp_utc": self.timestamp_utc,
            "round_id": self.round_id,
            "node_id": self.node_id,
            "calibrated_sigma": self.calibrated_sigma,
            "gradient_clip_c": self.gradient_clip_c,
            "cumulative_epsilon": self.cumulative_epsilon,
            "target_epsilon": self.target_epsilon,
            "risk_tier": self.risk_tier,
            "previous_hash": self.previous_hash,
            "block_hash": self.block_hash,
        }


@dataclass(frozen=True)
class AutoScalerTelemetry:
    """Real-time telemetry and budget projection for the adaptive DP auto-scaler."""

    active_sigma: float
    active_clip_norm: float
    cumulative_epsilon: float
    target_epsilon: float
    remaining_budget_pct: float
    projected_final_epsilon: float
    snr_signal_to_noise: float
    risk_tier: str  # 'OPTIMAL', 'CALIBRATING', 'BUDGET_WARNING', 'EXHAUSTED'
    history: list[DynamicNoiseCalibration] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    audit_chain_valid: bool = True
    node_id: str = "global"
