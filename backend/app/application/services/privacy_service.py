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
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)


class PrivacyBudgetExceededError(Exception):
    """Custom exception raised when the cumulative DP epsilon budget is exceeded."""

    pass


@dataclass
class PrivacyBudget:
    """Tracks cumulative privacy expenditure across rounds.

    Uses simple composition: total epsilon grows linearly with rounds.
    Advanced composition (Moments Accountant / Rényi DP) would give
    tighter bounds but is beyond the scope of this simulator.
    """

    epsilon_per_round: float = 1.0
    delta: float = 1e-5
    rounds_spent: int = 0
    _epsilon_history: list[float] = field(default_factory=list)

    @property
    def total_epsilon(self) -> float:
        """Cumulative privacy loss."""
        return sum(self._epsilon_history)

    @property
    def total_delta(self) -> float:
        """Cumulative delta under basic composition."""
        return self.delta * self.rounds_spent

    def spend(self, epsilon: float, limit: float = 8.0) -> None:
        """Record privacy expenditure for one round and verify cumulative budget.

        Raises:
            PrivacyBudgetExceededError: if total spent epsilon exceeds the security limit.
        """
        self.rounds_spent += 1
        self._epsilon_history.append(epsilon)
        if self.total_epsilon > limit:
            raise PrivacyBudgetExceededError(
                f"Cumulative privacy budget exceeded! Total: {self.total_epsilon:.4f} > Limit: {limit:.4f}"
            )

    @property
    def history(self) -> list[float]:
        return list(self._epsilon_history)


class PrivacyService:
    """Applies differential privacy mechanisms to model updates.

    Gaussian mechanism: adds noise ~ N(0, σ²) to each parameter,
    where σ is calibrated to the sensitivity and desired ε, δ.
    """

    def __init__(self) -> None:
        self._budgets: dict[str, PrivacyBudget] = {}

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
        return float(sensitivity * np.sqrt(2.0 * np.log(1.25 / delta)) / epsilon)

    def get_or_create_budget(
        self,
        simulation_id: str,
        epsilon: float = 1.0,
        delta: float = 1e-5,
    ) -> PrivacyBudget:
        """Get or initialize a privacy budget for a simulation."""
        if simulation_id not in self._budgets:
            self._budgets[simulation_id] = PrivacyBudget(
                epsilon_per_round=epsilon,
                delta=delta,
            )
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
        if rng is None:
            rng = np.random.default_rng()

        if sensitivity is None:
            sensitivity = max_grad_norm

        # Gaussian mechanism noise scale
        sigma = sensitivity * np.sqrt(2 * np.log(1.25 / delta)) / epsilon

        noise = rng.normal(0, sigma, len(weights.flat_weights))
        noised_weights = [w + n for w, n in zip(weights.flat_weights, noise, strict=False)]

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
        delta_w = np.array(updated_weights.flat_weights) - np.array(original_weights.flat_weights)
        norm = np.linalg.norm(delta_w)

        if norm > max_norm or np.isnan(norm) or np.isinf(norm):
            if np.isnan(norm) or np.isinf(norm):
                delta_w = np.nan_to_num(delta_w, nan=0.0, posinf=max_norm, neginf=-max_norm)
            else:
                delta_w = delta_w * (max_norm / norm)
            logger.debug("Clipped model update: norm %.4f → %.4f", norm, max_norm)

        clipped = np.array(original_weights.flat_weights) + delta_w

        return ModelWeights(
            layer_shapes=updated_weights.layer_shapes,
            flat_weights=clipped.tolist(),
        )

    def record_opacus_epsilon(self, simulation_id: str, epsilon: float, limit: float = 8.0) -> None:
        """Record the actual privacy budget spent in Opacus mode for a round.

        Since Opacus computes the total Rényi Differential Privacy (RDP)
        epsilon across multiple steps using composition, we directly record the
        resulting epsilon computed by the PrivacyEngine.
        """
        budget = self.get_or_create_budget(simulation_id)
        budget.spend(epsilon, limit)
        logger.info(
            "Recorded Opacus DP spend for simulation %s: round_epsilon=%.4f, total_spent_epsilon=%.4f",
            simulation_id,
            epsilon,
            budget.total_epsilon,
        )

    def clear_budget(self, simulation_id: str) -> None:
        """Remove privacy budget for a completed simulation."""
        self._budgets.pop(simulation_id, None)

    def get_all_budgets_summary(self, epsilon_limit: float = 8.0) -> list[dict[str, Any]]:
        """Return cumulative privacy budget summary across all tracked simulations.

        Provides an enterprise-level view of epsilon consumption across multiple
        federated training sessions. Used by the Privacy Budget Log dashboard to
        detect budget exhaustion attack patterns (where an attacker triggers many
        short simulations to slowly accumulate delta patterns).

        Args:
            epsilon_limit: Maximum allowed cumulative epsilon before flagging.

        Returns:
            List of dicts, one per simulation_id, with budget details.
        """
        summaries: list[dict[str, Any]] = []
        for simulation_id, budget in self._budgets.items():
            total_eps = budget.total_epsilon
            exhausted = total_eps > epsilon_limit
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
                    "delta": budget.delta,
                    "rounds_spent": budget.rounds_spent,
                    "epsilon_per_round": budget.epsilon_per_round,
                    "epsilon_history": budget.history,
                    "budget_exhausted": exhausted,
                    "epsilon_limit": epsilon_limit,
                }
            )
        # Sort by total_epsilon descending (highest consumption first)
        summaries.sort(key=lambda x: float(x["total_epsilon"]), reverse=True)  # type: ignore[arg-type]
        return summaries
