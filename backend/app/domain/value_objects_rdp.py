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
