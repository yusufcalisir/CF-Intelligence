"""Byzantine Anomaly Defense & Robust Aggregator Domain Engine (Phase 17).

Provides formal implementations of Byzantine-resilient federated aggregation algorithms
and theoretical breakdown point analysis:
  1. FedAvg (Standard baseline without defense; breakdown at f >= 1)
  2. Coordinate-wise Median (Breakdown point: f < n / 2)
  3. Coordinate-wise Trimmed Mean (Breakdown point: f <= beta * n, beta < 0.5)
  4. Krum (Blanchard et al., 2017: 2f + 2 < n, tolerance up to ~50% Byzantine)
  5. Bulyan (Guerraoui / El Mhamdi et al., 2018: n >= 4f + 3, high-dimensional collusion defense)
  6. SpectralByzantineDefense (MAD / SVD spectral norm outlier defense)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core Byzantine Aggregation Algorithms
# ---------------------------------------------------------------------------


def aggregate_fedavg(updates: list[np.ndarray]) -> np.ndarray:
    """Computes unweighted coordinate-wise arithmetic mean (standard FedAvg).

    Vulnerable to single Byzantine client updates (breakdown point f = 0).
    """
    if not updates:
        raise ValueError("Cannot aggregate empty updates list")
    arr = np.array(updates, dtype=np.float64)
    return np.mean(arr, axis=0)


def aggregate_coordinate_median(updates: list[np.ndarray]) -> np.ndarray:
    """Computes coordinate-wise median across client updates.

    Robust against outliers on individual coordinate axes.
    Breakdown point: f < n / 2 (tolerates up to ~50% Byzantine nodes).
    """
    if not updates:
        raise ValueError("Cannot aggregate empty updates list")
    arr = np.array(updates, dtype=np.float64)
    return np.median(arr, axis=0)


def aggregate_trimmed_mean(updates: list[np.ndarray], trim_ratio: float = 0.2) -> np.ndarray:
    """Computes coordinate-wise trimmed mean, discarding lowest and highest beta fraction.

    Args:
        updates: List of client update vectors.
        trim_ratio: Trimming fraction beta (default: 0.2 for 20% trimming per tail).

    Breakdown point: f <= beta * n where beta < 0.5.
    """
    if not updates:
        raise ValueError("Cannot aggregate empty updates list")
    arr = np.array(updates, dtype=np.float64)
    n = arr.shape[0]
    k = int(n * trim_ratio)

    if k == 0 or n <= 2 * k:
        return np.mean(arr, axis=0)

    arr_sorted = np.sort(arr, axis=0)
    trimmed = arr_sorted[k : n - k, :]
    return np.mean(trimmed, axis=0)


def aggregate_krum(updates: list[np.ndarray], f_byzantine: int = 1) -> np.ndarray:
    """Krum Byzantine-resilient aggregation (Blanchard et al., NeurIPS 2017).

    Selects the single update that minimizes the sum of squared Euclidean distances
    to its (n - f - 2) closest neighbors.

    Invariant condition: 2f + 2 < n (i.e. n >= 2f + 3).
    Fallback: If n <= 2f + 2, falls back to coordinate-wise median.
    """
    if not updates:
        raise ValueError("Cannot aggregate empty updates list")
    n = len(updates)
    arr = [np.asarray(u, dtype=np.float64) for u in updates]

    # Theoretical Krum condition
    if n <= 2 * f_byzantine + 2:
        return aggregate_coordinate_median(arr)

    num_closest = max(1, n - f_byzantine - 2)
    scores: list[float] = []

    for i in range(n):
        dists = [
            float(np.sum((arr[i] - arr[j]) ** 2))
            for j in range(n)
            if i != j
        ]
        dists.sort()
        score = sum(dists[:num_closest])
        scores.append(score)

    best_idx = int(np.argmin(scores))
    return arr[best_idx]


def aggregate_bulyan(updates: list[np.ndarray], f_byzantine: int = 1) -> np.ndarray:
    """Bulyan Byzantine-resilient aggregation (El Mhamdi / Guerraoui et al., ICML 2018).

    Two-stage aggregation combining Krum candidate selection and coordinate-wise Trimmed Mean:
      1. Iteratively applies Krum to select theta = n - 2f candidate updates.
      2. Computes coordinate-wise trimmed mean on the selected candidates, trimming 2f values.

    Theoretical requirement: n >= 4f + 3.
    Fallback: If n < 4f + 3, falls back to trimmed mean or Krum depending on n.
    """
    if not updates:
        raise ValueError("Cannot aggregate empty updates list")
    n = len(updates)
    arr = [np.asarray(u, dtype=np.float64) for u in updates]

    # Theoretical Bulyan condition
    if n < 4 * f_byzantine + 3:
        if n > 2 * f_byzantine + 2:
            return aggregate_krum(arr, f_byzantine=f_byzantine)
        return aggregate_coordinate_median(arr)

    theta = n - 2 * f_byzantine
    remaining_indices = list(range(n))
    selected_indices: list[int] = []

    for _ in range(theta):
        sub_updates = [arr[idx] for idx in remaining_indices]
        m = len(sub_updates)
        num_closest = max(1, m - f_byzantine - 2)

        scores: list[float] = []
        for i in range(m):
            dists = [
                float(np.sum((sub_updates[i] - sub_updates[j]) ** 2))
                for j in range(m)
                if i != j
            ]
            dists.sort()
            scores.append(sum(dists[:num_closest]))

        best_sub_idx = int(np.argmin(scores))
        orig_idx = remaining_indices.pop(best_sub_idx)
        selected_indices.append(orig_idx)

    selected_updates = [arr[idx] for idx in selected_indices]
    # Trimmed mean on selected set: trim f highest and f lowest per coordinate
    sel_arr = np.array(selected_updates, dtype=np.float64)
    trim_count = max(1, f_byzantine)
    if sel_arr.shape[0] > 2 * trim_count:
        sorted_sel = np.sort(sel_arr, axis=0)
        trimmed = sorted_sel[trim_count : sel_arr.shape[0] - trim_count, :]
        return np.mean(trimmed, axis=0)

    return np.mean(sel_arr, axis=0)


# ---------------------------------------------------------------------------
# Byzantine Breakdown Point Analyzer
# ---------------------------------------------------------------------------


class ByzantineBreakdownAnalyzer:
    """Calculates and verifies theoretical breakdown points for robust aggregators."""

    @staticmethod
    def get_max_tolerable_byzantine(n_clients: int, defense: str) -> int:
        """Computes maximum tolerable Byzantine clients f_max for given defense and consortium size n.

        Args:
            n_clients: Total participating banking nodes (n >= 1).
            defense: Defense algorithm ('fedavg', 'median', 'trimmed_mean', 'krum', 'bulyan').

        Returns:
            f_max: Maximum number of colluding Byzantine nodes the aggregator can safely tolerate.
        """
        n = max(1, n_clients)
        name = defense.lower().replace("-", "_").replace(" ", "_")

        if "fedavg" in name or name == "mean":
            return 0  # Zero tolerance: 1 malicious client can manipulate output arbitrarily

        if "bulyan" in name:
            # n >= 4f + 3 => 4f <= n - 3 => f <= (n - 3) / 4
            return max(0, (n - 3) // 4)

        if "krum" in name:
            # 2f + 2 < n => 2f <= n - 3 => f <= (n - 3) / 2
            return max(0, (n - 3) // 2)

        if "trimmed_mean" in name or "trim" in name:
            # beta = 0.2 default => f <= floor(0.2 * n)
            return max(0, int(0.2 * n))

        if "median" in name:
            # f < n / 2 => f <= (n - 1) // 2
            return max(0, (n - 1) // 2)

        return 0

    @classmethod
    def analyze_consortium(cls, n_clients: int, f_byzantine: int) -> dict[str, Any]:
        """Performs comprehensive breakdown risk assessment across all defensive aggregators."""
        defenses = ["fedavg", "coordinate_median", "trimmed_mean", "krum", "bulyan"]
        report: dict[str, Any] = {
            "n_clients": n_clients,
            "f_byzantine": f_byzantine,
            "byzantine_fraction": round(f_byzantine / max(1, n_clients), 4),
            "defenses": {},
        }

        for d in defenses:
            f_max = cls.get_max_tolerable_byzantine(n_clients, d)
            is_resilient = f_byzantine <= f_max
            report["defenses"][d] = {
                "max_tolerable_f": f_max,
                "max_tolerable_fraction": round(f_max / max(1, n_clients), 4),
                "is_resilient": is_resilient,
                "status": "RESILIENT" if is_resilient else "BREAKDOWN",
            }

        return report


# ---------------------------------------------------------------------------
# Spectral Byzantine Defense (Retained for backwards compatibility)
# ---------------------------------------------------------------------------


class SpectralByzantineDefense:
    """Detects and filters malicious model gradient updates using median absolute deviation (MAD) and spectral norm anomaly detection."""

    def __init__(self, contamination_ratio: float = 0.33) -> None:
        self.contamination_ratio = contamination_ratio

    def filter_anomalous_updates(
        self, updates: dict[str, np.ndarray | Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """Identifies and removes outlier gradients from malicious or corrupt nodes using robust median stats.

        Returns:
            Tuple of (sanitized_updates_dict, list_of_anomalous_bank_ids).
        """
        if len(updates) <= 2:
            return updates, []

        bank_ids = list(updates.keys())
        norms: list[float] = []

        for b_id in bank_ids:
            arr = np.asarray(updates[b_id], dtype=np.float64)
            norm_val = float(np.linalg.norm(arr))
            norms.append(norm_val)

        norms_arr = np.array(norms)
        median_norm = float(np.median(norms_arr))
        mad = float(np.median(np.abs(norms_arr - median_norm)))

        anomalies: list[str] = []
        sanitized: dict[str, Any] = {}

        for b_id, norm_val in zip(bank_ids, norms, strict=False):
            is_anomaly = False
            if (
                mad > 1e-4
                and (norm_val - median_norm) > 3.0 * mad
                or median_norm > 0
                and norm_val > 3.0 * max(median_norm, 1.0)
            ):
                is_anomaly = True

            if is_anomaly:
                anomalies.append(b_id)
                logger.warning(
                    "SpectralByzantineDefense: Isolated malicious update from '%s' (norm: %.2f vs median: %.2f)",
                    b_id,
                    norm_val,
                    median_norm,
                )
            else:
                sanitized[b_id] = updates[b_id]

        return sanitized, anomalies
