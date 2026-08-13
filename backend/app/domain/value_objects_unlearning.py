"""Domain value objects for Confidential Federated Unlearning & Anti-Poisoning Erasure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class UnlearningMethod(StrEnum):
    """Supported federated model unlearning & gradient erasure strategies."""

    EXACT_LINEAGE_SUBTRACTION = "EXACT_LINEAGE_SUBTRACTION"
    FIRST_ORDER_HESSIAN_INVERSION = "FIRST_ORDER_HESSIAN_INVERSION"
    SUB_SAMPLED_NEWTON_STEPS = "SUB_SAMPLED_NEWTON_STEPS"


@dataclass(frozen=True)
class FederatedUnlearningRequest:
    """Request payload for triggering federated model unlearning for an evicted bank."""

    target_bank_id: str
    unlearning_method: UnlearningMethod = UnlearningMethod.FIRST_ORDER_HESSIAN_INVERSION
    start_round: int = 1
    end_round: int = 42
    verification_threshold_mia: float = 0.52


@dataclass(frozen=True)
class FederatedUnlearningResult:
    """Container holding metrics and audit logs from a federated model weight unlearning run."""

    target_bank_id: str
    unlearning_method: str
    initial_model_l2_norm: float
    unlearned_model_l2_norm: float
    parameter_drift_delta: float
    hessian_spectral_radius: float
    mia_membership_probability: float  # <0.52 indicates indistinguishable from random guessing
    execution_time_ms: float
    erasure_verified: bool
    lineage_hash: str
    audit_log: list[dict[str, Any]]
