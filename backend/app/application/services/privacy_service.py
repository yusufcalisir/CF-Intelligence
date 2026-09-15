"""Privacy-enhancing mechanisms for federated learning.

Implements:
1. Differential Privacy (DP) — adds calibrated noise to model updates
2. Privacy budget tracking — monitors cumulative epsilon across rounds

This is a simplified, educational implementation. Production DP would use
libraries like Opacus (PyTorch) or TensorFlow Privacy, which handle
per-sample gradient clipping and noise calibration more rigorously.

See docs/threat-model.md for an honest assessment of what this protects
against and what it doesn't.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.domain.value_objects import ModelWeights
from app.domain.value_objects_rdp import DEFAULT_RDP_ORDERS

logger = logging.getLogger(__name__)


class PrivacyBudgetExceededError(Exception):
    """Custom exception raised when the cumulative DP epsilon budget is exceeded."""

    pass


@dataclass
class PrivacyBudget:
    """Tracks cumulative privacy expenditure across rounds.

    Supports both:
    1. Basic composition: total epsilon grows linearly with rounds.
    2. Rényi Differential Privacy (RDP) composition: tighter bounds via convex dual optimization.
    """

    epsilon_per_round: float = 1.0
    delta: float = 1e-5
    rounds_spent: int = 0
    _epsilon_history: list[float] = field(default_factory=list)
    _sigma_history: list[float] = field(default_factory=list)
    _q_history: list[float] = field(default_factory=list)

    @property
    def total_epsilon(self) -> float:
        """Cumulative privacy loss under basic composition."""
        return sum(self._epsilon_history)

    @property
    def total_delta(self) -> float:
        """Cumulative delta under basic composition."""
        return self.delta * self.rounds_spent

    def rdp_total_epsilon(
        self,
        delta: float | None = None,
        orders: list[float] | None = None,
    ) -> float | None:
        """Computes tighter cumulative privacy loss under Rényi DP composition if noise history exists."""
        if not self._sigma_history:
            return None
        target_delta = delta or self.delta
        eval_orders = orders or DEFAULT_RDP_ORDERS
        best_eps = float("inf")

        for alpha in eval_orders:
            if alpha <= 1.0:
                continue
            rdp_sum = sum(
                (alpha * (q**2)) / (2.0 * (sigma**2))
                for sigma, q in zip(self._sigma_history, self._q_history, strict=False)
                if sigma > 0
            )
            converted_eps = rdp_sum + (np.log(1.0 / target_delta) / (alpha - 1.0))
            if converted_eps < best_eps:
                best_eps = float(converted_eps)

        return best_eps if best_eps != float("inf") else None

    def spend(
        self,
        epsilon: float,
        limit: float = 8.0,
        sigma: float | None = None,
        q: float = 1.0,
    ) -> None:
        """Record privacy expenditure for one round and verify cumulative budget.

        Raises:
            PrivacyBudgetExceededError: if total spent epsilon exceeds the security limit.
        """
        if epsilon <= 0:
            raise ValueError(f"Epsilon must be positive, got {epsilon}")
        self.rounds_spent += 1
        self._epsilon_history.append(epsilon)
        if sigma is not None and sigma > 0:
            self._sigma_history.append(sigma)
            self._q_history.append(q)
        if self.total_epsilon > limit:
            raise PrivacyBudgetExceededError(
                f"Cumulative privacy budget exceeded! Total: {self.total_epsilon:.4f} > Limit: {limit:.4f}"
            )

    @property
    def history(self) -> list[float]:
        return list(self._epsilon_history)

    @property
    def sigma_history(self) -> list[float]:
        return list(self._sigma_history)


class PrivacyService:
    """Applies differential privacy mechanisms to model updates.

    Gaussian mechanism: adds noise ~ N(0, σ²) to each parameter,
    where σ is calibrated to the sensitivity and desired ε, δ.
    Provides Rényi DP accountant and convex dual optimal composition.
    """

    MAX_TRACKED_BUDGETS: int = 200

    def __init__(self) -> None:
        self._budgets: OrderedDict[str, PrivacyBudget] = OrderedDict()
        self._lock = threading.Lock()

    def calculate_gaussian_noise_scale(
        self,
        epsilon: float = 1.0,
        delta: float = 1e-5,
        sensitivity: float = 1.0,
    ) -> float:
        """Computes analytical Gaussian mechanism noise scale sigma."""
        if epsilon <= 0:
            raise ValueError("Epsilon must be positive")
        if delta <= 0 or delta >= 1.0:
            raise ValueError("Delta must be in (0, 1)")
        if sensitivity <= 0:
            raise ValueError("Sensitivity must be positive")
        sigma = float(sensitivity * np.sqrt(2.0 * np.log(1.25 / delta)) / epsilon)
        if sigma <= 0 or np.isnan(sigma) or np.isinf(sigma):
            raise ValueError("Calibrated noise scale must be strictly positive and finite")
        return sigma

    def compute_rdp_gaussian(
        self,
        sigma: float,
        q: float = 1.0,
        alpha: float = 2.0,
    ) -> float:
        """Computes analytical Rényi Differential Privacy bound for subsampled Gaussian mechanism."""
        if sigma <= 0.0:
            return float("inf")
        if q <= 0.0:
            return 0.0
        if alpha <= 1.0:
            raise ValueError(f"Rényi order alpha must be > 1.0, got {alpha}")
        return float((alpha * (q**2)) / (2.0 * (sigma**2)))

    def convert_rdp_to_approx_dp(
        self,
        rdp_map: dict[float, float],
        delta: float,
        orders: list[float] | None = None,
    ) -> tuple[float, float]:
        """Converts cumulative RDP bounds to standard (epsilon, delta)-DP via convex dual minimization.

        Returns:
            (optimal_epsilon, optimal_alpha)
        """
        if delta <= 0.0 or delta >= 1.0:
            raise ValueError(f"Delta must be in (0, 1), got {delta}")

        eval_orders = orders or list(rdp_map.keys())
        best_eps = float("inf")
        best_alpha = eval_orders[0] if eval_orders else 2.0

        for alpha in eval_orders:
            if alpha <= 1.0 or alpha not in rdp_map:
                continue
            rdp_eps = rdp_map[alpha]
            converted_eps = rdp_eps + (np.log(1.0 / delta) / (alpha - 1.0))
            if converted_eps < best_eps:
                best_eps = float(converted_eps)
                best_alpha = float(alpha)

        return best_eps, best_alpha

    def compose_rdp(
        self,
        sigmas: list[float],
        delta: float = 1e-5,
        q: float = 1.0,
        orders: list[float] | None = None,
    ) -> tuple[float, float, dict[float, float]]:
        """Composes privacy loss across multiple rounds using Rényi Differential Privacy."""
        if not sigmas:
            raise ValueError("Must provide at least one noise multiplier (sigma)")
        if any(s <= 0 for s in sigmas):
            raise ValueError("All noise multipliers (sigmas) must be strictly positive")
        if delta <= 0.0 or delta >= 1.0:
            raise ValueError(f"Delta must be in (0, 1), got {delta}")
        if q <= 0.0 or q > 1.0:
            raise ValueError(f"Sample ratio q must be in (0, 1], got {q}")

        eval_orders = orders or DEFAULT_RDP_ORDERS
        rdp_map: dict[float, float] = {}

        for alpha in eval_orders:
            if alpha <= 1.0:
                continue
            rdp_sum = sum(self.compute_rdp_gaussian(s, q=q, alpha=alpha) for s in sigmas)
            rdp_map[alpha] = rdp_sum

        best_eps, best_alpha = self.convert_rdp_to_approx_dp(rdp_map, delta=delta, orders=eval_orders)
        return best_eps, best_alpha, rdp_map

    def get_or_create_budget(
        self,
        simulation_id: str,
        epsilon: float = 1.0,
        delta: float = 1e-5,
    ) -> PrivacyBudget:
        """Get or initialize a privacy budget for a simulation in a thread-safe manner."""
        with self._lock:
            if simulation_id not in self._budgets:
                if len(self._budgets) >= self.MAX_TRACKED_BUDGETS:
                    self._budgets.popitem(last=False)  # Evict oldest FIFO
                self._budgets[simulation_id] = PrivacyBudget(
                    epsilon_per_round=epsilon,
                    delta=delta,
                )
            else:
                self._budgets.move_to_end(simulation_id)
            return self._budgets[simulation_id]

    def add_noise_to_weights(
        self,
        weights: ModelWeights,
        epsilon: float = 1.0,
        delta: float = 1e-5,
        max_grad_norm: float = 1.0,
        sensitivity: float | None = None,
        rng: np.random.Generator | None = None,
    ) -> ModelWeights:
        """Apply Gaussian mechanism to model parameters.

        The noise scale σ is computed as:
            σ = sensitivity * sqrt(2 * ln(1.25/δ)) / ε

        where sensitivity = max_grad_norm (L2 sensitivity of the query).

        Lower epsilon → more noise → stronger privacy → lower utility.
        This is the fundamental privacy-utility tradeoff.

        Args:
            weights: Original model parameters.
            epsilon: Privacy parameter. Lower = more private.
            delta: Probability of privacy breach.
            max_grad_norm: Clipping bound for gradient sensitivity.
            sensitivity: Override for sensitivity calculation.
            rng: Random number generator.

        Returns:
            Noised model parameters.
        """
        if epsilon <= 0:
            raise ValueError("Epsilon must be positive")
        if delta <= 0 or delta >= 1.0:
            raise ValueError("Delta must be in (0, 1)")
        if max_grad_norm <= 0:
            raise ValueError("max_grad_norm must be positive")
        if sensitivity is not None and sensitivity <= 0:
            raise ValueError("sensitivity must be positive")

        if rng is None:
            rng = np.random.default_rng()

        if sensitivity is None:
            sensitivity = max_grad_norm

        sigma = self.calculate_gaussian_noise_scale(
            epsilon=epsilon, delta=delta, sensitivity=sensitivity
        )

        noise = rng.normal(0, sigma, len(weights.flat_weights))
        noised_weights = [float(w + n) for w, n in zip(weights.flat_weights, noise, strict=False)]

        logger.info(
            "Applied DP noise: ε=%.2f, δ=%.1e, σ=%.4f, params=%d",
            epsilon,
            delta,
            sigma,
            len(weights.flat_weights),
        )

        return ModelWeights(
            layer_shapes=weights.layer_shapes,
            flat_weights=noised_weights,
        )

    def clip_model_update(
        self,
        original_weights: ModelWeights,
        updated_weights: ModelWeights,
        max_norm: float = 1.0,
    ) -> ModelWeights:
        """Clip the model update (delta) to bound sensitivity.

        In DP-FL, we clip the difference between the local update and
        the global model, not the parameters themselves. This bounds
        the contribution of any single client's data.
        """
        if max_norm <= 0:
            raise ValueError(f"max_norm must be strictly positive, got {max_norm}")

        delta_w = np.array(updated_weights.flat_weights, dtype=np.float64) - np.array(
            original_weights.flat_weights, dtype=np.float64
        )
        norm = float(np.linalg.norm(delta_w))

        if norm > max_norm or np.isnan(norm) or np.isinf(norm):
            if np.isnan(norm) or np.isinf(norm):
                delta_w = np.nan_to_num(delta_w, nan=0.0, posinf=max_norm, neginf=-max_norm)
                cleaned_norm = float(np.linalg.norm(delta_w))
                if cleaned_norm > max_norm:
                    delta_w = delta_w * (max_norm / (cleaned_norm + 1e-12))
            else:
                delta_w = delta_w * (max_norm / norm)
            logger.debug("Clipped model update: norm %.4f → %.4f", norm, max_norm)

        clipped = np.array(original_weights.flat_weights, dtype=np.float64) + delta_w

        return ModelWeights(
            layer_shapes=updated_weights.layer_shapes,
            flat_weights=clipped.tolist(),
        )

    def record_opacus_epsilon(
        self,
        simulation_id: str,
        epsilon: float,
        limit: float = 8.0,
        sigma: float | None = None,
    ) -> None:
        """Record the actual privacy budget spent in Opacus mode for a round.

        Since Opacus computes the total Rényi Differential Privacy (RDP)
        epsilon across multiple steps using composition, we directly record the
        resulting epsilon computed by the PrivacyEngine.
        """
        budget = self.get_or_create_budget(simulation_id)
        budget.spend(epsilon, limit, sigma=sigma)
        logger.info(
            "Recorded Opacus DP spend for simulation %s: round_epsilon=%.4f, total_spent_epsilon=%.4f",
            simulation_id,
            epsilon,
            budget.total_epsilon,
        )

    def clear_budget(self, simulation_id: str) -> None:
        """Remove privacy budget for a completed simulation."""
        with self._lock:
            self._budgets.pop(simulation_id, None)

    def get_all_budgets_summary(self, epsilon_limit: float = 8.0) -> list[dict[str, Any]]:
        """Return cumulative privacy budget summary across all tracked simulations.

        Provides an enterprise-level view of epsilon consumption across multiple
        federated training sessions. Used by the Privacy Budget Log dashboard to
        detect budget exhaustion attack patterns.

        Args:
            epsilon_limit: Maximum allowed cumulative epsilon before flagging.

        Returns:
            List of dicts, one per simulation_id, with budget details.
        """
        summaries: list[dict[str, Any]] = []
        with self._lock:
            items = list(self._budgets.items())

        for simulation_id, budget in items:
            total_eps = budget.total_epsilon
            exhausted = total_eps > epsilon_limit
            rdp_eps = budget.rdp_total_epsilon()
            if exhausted:
                logger.warning(
                    "Budget exhaustion risk detected for simulation %s: ε=%.4f > limit=%.4f",
                    simulation_id,
                    total_eps,
                    epsilon_limit,
                )
            summaries.append(
                {
                    "simulation_id": simulation_id,
                    "total_epsilon": round(total_eps, 6),
                    "rdp_total_epsilon": round(rdp_eps, 6) if rdp_eps is not None else None,
                    "delta": budget.delta,
                    "rounds_spent": budget.rounds_spent,
                    "epsilon_per_round": budget.epsilon_per_round,
                    "epsilon_history": budget.history,
                    "sigma_history": budget.sigma_history,
                    "budget_exhausted": exhausted,
                    "epsilon_limit": epsilon_limit,
                }
            )
        # Sort by total_epsilon descending (highest consumption first)
        summaries.sort(key=lambda x: float(x["total_epsilon"]), reverse=True)  # type: ignore[arg-type]
        return summaries
