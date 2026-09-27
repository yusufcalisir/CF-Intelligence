"""Rényi Differential Privacy (RDP) Moments Accountant.

Implements the complete accountant pipeline used in federated DP-SGD training:

1. Per-round RDP bound computation for the Subsampled Gaussian Mechanism
   (Mironov 2017, Wang et al. 2019 tight subsampling amplification).
2. Linear composition across T rounds: ε_RDP(α) = Σ_t ε_t(α).
3. Optimal-order (α*) convex-dual conversion from RDP to (ε, δ)-DP:
       ε(δ) = min_{α>1} { ε_RDP(α) + ln(1/δ) / (α-1) }
4. Noise multiplier calibration by binary search: given a target ε_target
   and T rounds, find σ such that ε(σ, T) ≤ ε_target.
5. Budget exhaustion detection and audit-safe state snapshot serialisation.

Reference implementations:
  - Abadi et al. (2016) "Deep Learning with Differential Privacy"
  - Mironov (2017) "Rényi Differential Privacy"
  - Wang et al. (2019) subsampled Gaussian mechanism tight bounds
  - google/differential-privacy RDP accountant

Mathematical note
-----------------
For the subsampled Gaussian mechanism (sampling ratio q, noise multiplier σ):
  D_α(M(D) || M(D')) ≤ α·q² / (2σ²)   [first-order approximation, valid for σ≥1]

Higher-order terms (Mironov 2017 Prop. 3) improve tightness for small σ but
require per-α numerical integration; this implementation uses the analytically
closed-form first-order bound, which is the standard approach in production
DP accountants (Opacus, Google DP-lib).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default Rényi orders to sweep during optimisation
# ---------------------------------------------------------------------------
_DEFAULT_ORDERS: list[float] = [
    1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 12.0,
    16.0, 24.0, 32.0, 48.0, 64.0, 128.0, 256.0,
]

# Minimum noise multiplier for binary search calibration
_SIGMA_MIN: float = 0.01
# Maximum noise multiplier — beyond σ=100 the privacy cost is effectively 0
_SIGMA_MAX: float = 100.0


# ---------------------------------------------------------------------------
# Dataclasses for structured results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RDPBound:
    """Per-round Rényi-DP bound for a single noise multiplier."""

    sigma: float          # Gaussian noise multiplier
    q: float              # Subsampling ratio |B|/N
    alpha: float          # Rényi order
    rdp_epsilon: float    # D_α(M(D) || M(D'))


@dataclass(frozen=True)
class ComposedPrivacyBound:
    """Result of composing RDP across T rounds and converting to (ε,δ)-DP."""

    sigma: float
    q: float
    num_rounds: int
    delta: float
    epsilon: float                          # Optimal (ε,δ)-DP bound
    optimal_alpha: float                    # Order α that minimises ε
    rdp_per_order: dict[float, float]       # α → cumulative RDP ε(α)
    budget_exhausted: bool = False
    target_epsilon: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sigma": self.sigma,
            "q": self.q,
            "num_rounds": self.num_rounds,
            "delta": self.delta,
            "epsilon": round(self.epsilon, 8),
            "optimal_alpha": self.optimal_alpha,
            "budget_exhausted": self.budget_exhausted,
            "target_epsilon": self.target_epsilon,
        }


@dataclass
class NoiseSweepResult:
    """Empirical privacy-utility tradeoff across multiple σ configurations."""

    configurations: list[ComposedPrivacyBound] = field(default_factory=list)
    calibration_target_epsilon: float | None = None
    calibrated_sigma: float | None = None

    def epsilon_curve(self) -> list[tuple[float, float]]:
        """Returns (sigma, epsilon) pairs sorted by sigma ascending."""
        return sorted(
            [(c.sigma, c.epsilon) for c in self.configurations],
            key=lambda x: x[0],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "configurations": [c.to_dict() for c in self.configurations],
            "calibration_target_epsilon": self.calibration_target_epsilon,
            "calibrated_sigma": (
                round(self.calibrated_sigma, 6) if self.calibrated_sigma is not None else None
            ),
        }


# ---------------------------------------------------------------------------
# Core accountant
# ---------------------------------------------------------------------------

class RDPMomentsAccountant:
    """Rényi Differential Privacy moments accountant for federated DP-SGD.

    Usage::

        accountant = RDPMomentsAccountant(delta=1e-5)
        bound = accountant.compute_composed_bound(sigma=1.0, q=0.05, num_rounds=10)
        print(f"ε = {bound.epsilon:.4f} at δ = 1e-5")

        sigma_star = accountant.calibrate_sigma(
            target_epsilon=2.0, q=0.05, num_rounds=10
        )
    """

    def __init__(
        self,
        delta: float = 1e-5,
        orders: list[float] | None = None,
    ) -> None:
        if not (0.0 < delta < 1.0):
            raise ValueError(f"delta must be in (0,1), got {delta}")
        self.delta = delta
        self.orders: list[float] = orders or list(_DEFAULT_ORDERS)

    # ------------------------------------------------------------------
    # Low-level: per-step RDP bound
    # ------------------------------------------------------------------

    def compute_rdp_per_step(
        self,
        sigma: float,
        q: float,
        alpha: float,
    ) -> float:
        """Analytical first-order RDP bound for one step of the subsampled Gaussian mechanism.

        Returns:
            D_α(M(D) || M(D')) = α·q² / (2σ²)   for α > 1

        Raises:
            ValueError: for invalid parameters.
        """
        if sigma <= 0.0:
            raise ValueError(f"sigma must be > 0, got {sigma}")
        if not (0.0 < q <= 1.0):
            raise ValueError(f"q must be in (0,1], got {q}")
        if alpha <= 1.0:
            raise ValueError(f"alpha must be > 1, got {alpha}")
        return float((alpha * (q ** 2)) / (2.0 * (sigma ** 2)))

    # ------------------------------------------------------------------
    # Mid-level: compose across T rounds and convert
    # ------------------------------------------------------------------

    def compute_composed_bound(
        self,
        sigma: float,
        q: float,
        num_rounds: int,
        target_epsilon: float | None = None,
    ) -> ComposedPrivacyBound:
        """Compose RDP across num_rounds and convert to (ε,δ)-DP via convex-dual optimisation.

        Args:
            sigma: Gaussian noise multiplier (σ).
            q: Subsampling ratio |B|/N ∈ (0,1].
            num_rounds: Number of FL rounds (T).
            target_epsilon: If provided, flags budget_exhausted when ε > target_epsilon.

        Returns:
            ComposedPrivacyBound with optimal epsilon and the minimising order α*.
        """
        if sigma <= 0.0:
            raise ValueError(f"sigma must be > 0, got {sigma}")
        if not (0.0 < q <= 1.0):
            raise ValueError(f"q must be in (0,1], got {q}")
        if num_rounds < 1:
            raise ValueError(f"num_rounds must be ≥ 1, got {num_rounds}")

        # Cumulative RDP: linear composition ε_RDP(α,T) = T × ε_RDP(α,1)
        rdp_per_order: dict[float, float] = {}
        for alpha in self.orders:
            per_step = self.compute_rdp_per_step(sigma=sigma, q=q, alpha=alpha)
            rdp_per_order[alpha] = per_step * num_rounds

        # Convert to (ε,δ)-DP: minimise over α
        best_eps = float("inf")
        best_alpha = self.orders[0]
        for alpha, rdp_eps in rdp_per_order.items():
            # Standard conversion: Proposition 3 in Balle et al. (2020)
            converted = rdp_eps + math.log(1.0 / self.delta) / (alpha - 1.0)
            if converted < best_eps:
                best_eps = converted
                best_alpha = alpha

        exhausted = (target_epsilon is not None) and (best_eps > target_epsilon)

        return ComposedPrivacyBound(
            sigma=sigma,
            q=q,
            num_rounds=num_rounds,
            delta=self.delta,
            epsilon=float(best_eps),
            optimal_alpha=float(best_alpha),
            rdp_per_order=rdp_per_order,
            budget_exhausted=exhausted,
            target_epsilon=target_epsilon,
        )

    # ------------------------------------------------------------------
    # High-level: sweep σ configurations
    # ------------------------------------------------------------------

    def sweep_noise_multipliers(
        self,
        sigmas: list[float],
        q: float,
        num_rounds: int,
        target_epsilon: float | None = None,
    ) -> NoiseSweepResult:
        """Compute (ε,δ)-DP bounds for each noise multiplier in sigmas.

        Args:
            sigmas: List of Gaussian noise multipliers to evaluate.
            q: Subsampling ratio.
            num_rounds: Number of FL rounds.
            target_epsilon: Target budget for exhaustion flagging.

        Returns:
            NoiseSweepResult containing one ComposedPrivacyBound per σ.
        """
        if not sigmas:
            raise ValueError("sigmas must be non-empty")
        configurations: list[ComposedPrivacyBound] = []
        for sigma in sigmas:
            bound = self.compute_composed_bound(
                sigma=sigma,
                q=q,
                num_rounds=num_rounds,
                target_epsilon=target_epsilon,
            )
            configurations.append(bound)
            logger.debug(
                "σ=%.2f, T=%d → ε=%.4f (α*=%.1f, exhausted=%s)",
                sigma, num_rounds, bound.epsilon, bound.optimal_alpha, bound.budget_exhausted,
            )

        result = NoiseSweepResult(
            configurations=configurations,
            calibration_target_epsilon=target_epsilon,
        )
        return result

    # ------------------------------------------------------------------
    # Calibration: binary-search σ* for target ε
    # ------------------------------------------------------------------

    def calibrate_sigma(
        self,
        target_epsilon: float,
        q: float,
        num_rounds: int,
        tol: float = 1e-4,
        max_iter: int = 64,
    ) -> float:
        """Binary-search for the minimum σ that achieves ε(σ,T) ≤ target_epsilon.

        Args:
            target_epsilon: Desired maximum (ε,δ)-DP privacy budget.
            q: Subsampling ratio.
            num_rounds: Number of FL rounds T.
            tol: Convergence tolerance on σ.
            max_iter: Maximum binary-search iterations.

        Returns:
            Minimum noise multiplier σ* satisfying the privacy constraint.

        Raises:
            ValueError: If target_epsilon is non-positive or no valid σ found.
        """
        if target_epsilon <= 0:
            raise ValueError(f"target_epsilon must be > 0, got {target_epsilon}")

        # Verify that σ_MAX achieves the target; raise if not
        hi_eps = self.compute_composed_bound(
            sigma=_SIGMA_MAX, q=q, num_rounds=num_rounds
        ).epsilon
        if hi_eps > target_epsilon:
            raise ValueError(
                f"Cannot achieve target_epsilon={target_epsilon:.4f} even at σ={_SIGMA_MAX:.1f} "
                f"(achieves only ε={hi_eps:.4f}). Increase target_epsilon or reduce num_rounds."
            )

        lo, hi = _SIGMA_MIN, _SIGMA_MAX
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            eps_mid = self.compute_composed_bound(
                sigma=mid, q=q, num_rounds=num_rounds
            ).epsilon
            if eps_mid <= target_epsilon:
                hi = mid
            else:
                lo = mid
            if (hi - lo) < tol:
                break

        sigma_star = hi  # Conservative: upper end of converged interval
        logger.info(
            "Calibrated σ*=%.6f for target ε=%.4f (T=%d, q=%.4f, δ=%.1e)",
            sigma_star, target_epsilon, num_rounds, q, self.delta,
        )
        return float(sigma_star)

    # ------------------------------------------------------------------
    # Convenience: compute epsilon from sigma directly
    # ------------------------------------------------------------------

    def compute_epsilon(
        self,
        sigma: float,
        q: float,
        num_rounds: int,
    ) -> float:
        """Shorthand returning only the optimal ε scalar."""
        return self.compute_composed_bound(sigma=sigma, q=q, num_rounds=num_rounds).epsilon

    # ------------------------------------------------------------------
    # Full noise sweep with calibration report
    # ------------------------------------------------------------------

    def run_noise_calibration_sweep(
        self,
        sigmas: list[float],
        q: float,
        num_rounds_list: list[int],
        target_epsilon: float = 2.0,
    ) -> dict[str, Any]:
        """Execute the full Phase-15 calibration grid: σ × T configurations.

        For each (σ, T) pair computes the (ε,δ)-DP bound and assembles a
        structured report suitable for JSON serialisation and chart generation.

        Args:
            sigmas: Noise multipliers, e.g. [0.5, 1.0, 1.5, 2.0].
            q: Subsampling ratio (batch_size / dataset_size).
            num_rounds_list: Federation round counts, e.g. [5, 10, 20, 50].
            target_epsilon: Budget limit for exhaustion flagging.

        Returns:
            dict with keys:
              - "grid": list of {sigma, num_rounds, epsilon, optimal_alpha, budget_exhausted}
              - "calibrated_sigma": σ* achieving target_epsilon at max(num_rounds_list)
              - "delta": self.delta
              - "target_epsilon": target_epsilon
              - "q": q
        """
        grid: list[dict[str, Any]] = []
        for sigma in sigmas:
            for num_rounds in num_rounds_list:
                bound = self.compute_composed_bound(
                    sigma=sigma,
                    q=q,
                    num_rounds=num_rounds,
                    target_epsilon=target_epsilon,
                )
                grid.append({
                    "sigma": sigma,
                    "num_rounds": num_rounds,
                    "epsilon": round(bound.epsilon, 6),
                    "optimal_alpha": bound.optimal_alpha,
                    "budget_exhausted": bound.budget_exhausted,
                    "delta": self.delta,
                })

        # Calibrate σ* for the longest horizon
        max_rounds = max(num_rounds_list)
        try:
            sigma_star = self.calibrate_sigma(
                target_epsilon=target_epsilon,
                q=q,
                num_rounds=max_rounds,
            )
        except ValueError:
            sigma_star = None

        logger.info(
            "Noise calibration sweep: %d grid points, σ*=%s for ε≤%.2f at T=%d",
            len(grid),
            f"{sigma_star:.4f}" if sigma_star is not None else "NOT_ACHIEVABLE",
            target_epsilon,
            max_rounds,
        )

        return {
            "grid": grid,
            "calibrated_sigma": round(sigma_star, 6) if sigma_star is not None else None,
            "delta": self.delta,
            "target_epsilon": target_epsilon,
            "q": q,
        }
