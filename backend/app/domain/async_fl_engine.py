"""Domain-level Asynchronous Federated Learning Engine (FedAsync).

Allows fast bank nodes to contribute parameter updates asynchronously without blocking
on slower straggler nodes. Down-weights older updates using staleness attenuation S(tau)
following Xie et al. (2019) 'Asynchronous Federated Optimization'.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class StalenessFunction(str, Enum):  # noqa: UP042
    """Supported staleness attenuation functions S(tau)."""

    POLYNOMIAL = "polynomial"
    EXPONENTIAL = "exponential"
    CONSTANT = "constant"
    HINGE = "hinge"


def staleness_attenuation(
    tau: int,
    alpha: float = 0.5,
    func: StalenessFunction | str = StalenessFunction.POLYNOMIAL,
    max_staleness: int | None = None,
) -> float:
    """Computes staleness attenuation factor S(tau).

    Parameters:
        tau: Staleness delay = current_round - update_submitted_round (tau >= 0).
        alpha: Damping coefficient / decay exponent (alpha > 0).
        func: Attenuation formulation (polynomial, exponential, constant, hinge).
        max_staleness: Upper bound cutoff for staleness. Returns 0.0 if tau > max_staleness.

    Returns:
        float in [0.0, 1.0] scaling factor for client update.
    """
    if max_staleness is not None and tau > max_staleness:
        return 0.0

    if tau <= 0:
        return 1.0

    func_str = func.value if isinstance(func, StalenessFunction) else str(func).lower()

    if func_str == StalenessFunction.EXPONENTIAL:
        return float(np.exp(-alpha * float(tau)))
    if func_str == StalenessFunction.CONSTANT:
        return 1.0
    if func_str == StalenessFunction.HINGE:
        # Hinge decay: S(tau) = 1 for tau <= 2, else 1 / (alpha * (tau - 2) + 1)
        if tau <= 2:
            return 1.0
        return float(1.0 / (alpha * float(tau - 2) + 1.0))

    # Canonical polynomial formulation: S(tau) = (1 + tau)^(-alpha)
    return float((1.0 + float(tau)) ** (-alpha))


@dataclass
class AsynchronousUpdateRecord:
    """Record of an asynchronous model parameter update submitted by a bank node."""

    node_id: str
    submitted_round: int
    staleness_tau: int
    weights: dict[str, np.ndarray]
    sample_count: int
    effective_alpha: float = 0.0
    applied: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass
class AsyncFLEngine:
    """FedAsync engine executing asynchronous global model updates with staleness attenuation."""

    current_round: int = 1
    alpha_staleness: float = 0.5
    learning_rate: float = 0.8
    max_staleness: int = 50
    staleness_func: StalenessFunction = StalenessFunction.POLYNOMIAL
    global_weights: dict[str, np.ndarray] = field(default_factory=dict)
    update_history: deque[AsynchronousUpdateRecord] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    total_updates: int = 0
    dropped_updates: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_global_weights(self, weights: dict[str, np.ndarray]) -> None:
        """Sets the baseline global model weights (thread-safe)."""
        with self._lock:
            self.global_weights = {layer: arr.copy() for layer, arr in weights.items()}

    def get_global_weights(self) -> dict[str, np.ndarray]:
        """Returns a thread-safe deep copy of global weights."""
        with self._lock:
            return {layer: arr.copy() for layer, arr in self.global_weights.items()}

    def apply_async_update(
        self,
        node_id: str,
        submitted_round: int,
        client_weights: dict[str, np.ndarray],
        sample_count: int = 100,
        advance_round: bool = False,
    ) -> dict[str, np.ndarray]:
        """Applies an asynchronous parameter update from a bank node.

        Calculates staleness tau = current_round - submitted_round,
        computes attenuation factor S(tau), and updates global weights:
          W^(t+1) = (1 - alpha_tau) * W^(t) + alpha_tau * W_client

        Guarantees:
        - Concurrency safety via internal mutex lock.
        - Byzantine defense against NaN and Inf numerical poisoning.
        - Layer shape compatibility validation.
        - Automatic drop of obsolete straggler updates exceeding max_staleness.
        """
        # 1. Validate numerical integrity (Byzantine defense against non-finite values)
        for layer, arr in client_weights.items():
            if not np.isfinite(arr).all():
                logger.warning(
                    "Rejected async update from node %s: non-finite (NaN/Inf) weights in layer '%s'",
                    node_id,
                    layer,
                )
                raise ValueError(
                    f"Client weights contain non-finite (NaN or Inf) values in layer '{layer}'"
                )

        with self._lock:
            if not self.global_weights:
                self.global_weights = {layer: arr.copy() for layer, arr in client_weights.items()}
                rec = AsynchronousUpdateRecord(
                    node_id=node_id,
                    submitted_round=submitted_round,
                    staleness_tau=0,
                    weights={layer_k: arr_v.copy() for layer_k, arr_v in client_weights.items()},
                    sample_count=sample_count,
                    effective_alpha=self.learning_rate,
                    applied=True,
                )
                self.update_history.append(rec)
                self.total_updates += 1
                return {layer: arr.copy() for layer, arr in self.global_weights.items()}

            # Validate layer shapes against active global model
            for layer, current_arr in self.global_weights.items():
                if layer in client_weights and client_weights[layer].shape != current_arr.shape:
                    raise ValueError(
                        f"Layer shape mismatch for '{layer}': expected {current_arr.shape}, "
                        f"got {client_weights[layer].shape}"
                    )

            tau = max(0, self.current_round - submitted_round)

            # Straggler cutoff check
            if tau > self.max_staleness:
                logger.warning(
                    "Dropped stale update from node %s (tau=%d > max_staleness=%d)",
                    node_id,
                    tau,
                    self.max_staleness,
                )
                rec = AsynchronousUpdateRecord(
                    node_id=node_id,
                    submitted_round=submitted_round,
                    staleness_tau=tau,
                    weights={layer_k: arr_v.copy() for layer_k, arr_v in client_weights.items()},
                    sample_count=sample_count,
                    effective_alpha=0.0,
                    applied=False,
                )
                self.update_history.append(rec)
                self.dropped_updates += 1
                return {layer: arr.copy() for layer, arr in self.global_weights.items()}

            s_tau = staleness_attenuation(
                tau,
                alpha=self.alpha_staleness,
                func=self.staleness_func,
                max_staleness=self.max_staleness,
            )
            effective_alpha = float(self.learning_rate * s_tau)

            logger.info(
                "Async update from node %s (round %d, tau=%d, s(tau)=%.4f, eff_alpha=%.6f)",
                node_id,
                submitted_round,
                tau,
                s_tau,
                effective_alpha,
            )

            new_global: dict[str, np.ndarray] = {}
            for layer, current_arr in self.global_weights.items():
                if layer in client_weights:
                    client_arr = client_weights[layer]
                    new_global[layer] = (
                        1.0 - effective_alpha
                    ) * current_arr + effective_alpha * client_arr
                else:
                    new_global[layer] = current_arr.copy()

            self.global_weights = new_global
            self.total_updates += 1
            if advance_round:
                self.current_round += 1

            rec = AsynchronousUpdateRecord(
                node_id=node_id,
                submitted_round=submitted_round,
                staleness_tau=tau,
                weights={layer_k: arr_v.copy() for layer_k, arr_v in client_weights.items()},
                sample_count=sample_count,
                effective_alpha=effective_alpha,
                applied=True,
            )
            self.update_history.append(rec)

            return {layer: arr.copy() for layer, arr in self.global_weights.items()}

    def get_staleness_metrics(self) -> dict[str, Any]:
        """Returns empirical staleness and throughput statistics."""
        with self._lock:
            taus = [r.staleness_tau for r in self.update_history if r.applied]
            avg_tau = float(np.mean(taus)) if taus else 0.0
            max_tau = int(max(taus)) if taus else 0

            return {
                "current_round": self.current_round,
                "alpha_staleness": self.alpha_staleness,
                "learning_rate": self.learning_rate,
                "max_staleness": self.max_staleness,
                "staleness_function": self.staleness_func.value
                if isinstance(self.staleness_func, StalenessFunction)
                else str(self.staleness_func),
                "total_updates": self.total_updates,
                "dropped_updates": self.dropped_updates,
                "applied_updates": len(taus),
                "average_staleness": round(avg_tau, 2),
                "max_observed_staleness": max_tau,
            }
