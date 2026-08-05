"""Spectral Anomaly Detection & Backdoor Poisoning Defense Domain Module (Section 6.5).

Protects the global federated model against subtle targeted backdoor attacks by applying
Singular Value Decomposition (SVD) to compute spectral projection scores across client
gradient matrices, identifying low-rank subspace anomalies characteristic of backdoor injections.

Mathematical Foundation:
    - Stack client updates: G ∈ ℝ^{K × d}
    - Compute thin SVD: G = U Σ V^T
    - Compute spectral scores: s_i = |⟨Δw_i, v₁⟩|²  (projection onto dominant singular vector)
    - Detect anomalies: s_i > μ_s + τ · σ_s  → quarantine
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration & Report Types
# ---------------------------------------------------------------------------


@dataclass
class SpectralDefenseConfig:
    """Configuration for the SVD-based spectral anomaly detector."""

    spectral_threshold_multiplier: float = 1.5
    """Multiplier τ applied to std-dev for anomaly threshold: θ = μ_s + τ · σ_s."""

    min_clients: int = 3
    """Minimum number of client updates required for spectral analysis; below this, fall back."""

    singular_value_rank: int = 3
    """Number of top singular vectors to use for multi-rank spectral projection (default: top-3)."""


@dataclass
class SpectralAnomalyReport:
    """Per-client spectral anomaly analysis report."""

    node_id: str
    spectral_score: float
    is_poisoned: bool
    singular_values: list[float] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# Minimal linear algebra helpers (pure stdlib — no numpy required in domain)
# ---------------------------------------------------------------------------


def _dot(a: list[float], b: list[float]) -> float:
    """Compute dot product of two vectors."""
    return sum(x * y for x, y in zip(a, b))


def _norm(v: list[float]) -> float:
    """Compute L2 norm of a vector."""
    return math.sqrt(sum(x * x for x in v))


def _normalize(v: list[float]) -> list[float]:
    """Return unit-length copy of vector v."""
    n = _norm(v)
    return [x / n for x in v] if n > 1e-12 else list(v)


def _flatten(weights: dict[str, list[float]]) -> list[float]:
    """Flatten a parameter dict into a single numeric vector."""
    result: list[float] = []
    for val in weights.values():
        if isinstance(val, (int, float)):
            result.append(float(val))
        elif isinstance(val, list):
            result.extend(val)
    return result


def _power_iteration(
    matrix_rows: list[list[float]], n_iters: int = 30
) -> tuple[list[float], float]:
    """Power iteration to estimate the top right singular vector v₁.

    Computes v₁ = argmax ‖A^T A v‖ via iterated A^T A v multiplication.
    Returns (v₁, σ₁) where σ₁ is the approximated top singular value.
    """
    if not matrix_rows or not matrix_rows[0]:
        return [], 0.0

    d = len(matrix_rows[0])
    # Initialise v with unit vector
    v: list[float] = [1.0 / math.sqrt(d)] * d

    for _ in range(n_iters):
        # Compute Av (K-vector)
        av: list[float] = [_dot(row, v) for row in matrix_rows]
        # Compute A^T Av (d-vector)
        atav: list[float] = [0.0] * d
        for row, scalar in zip(matrix_rows, av):
            for j, rj in enumerate(row):
                atav[j] += rj * scalar
        # Normalise
        n = _norm(atav)
        if n < 1e-12:
            break
        v = [x / n for x in atav]

    # Compute approximate top singular value σ₁
    av_final = [_dot(row, v) for row in matrix_rows]
    sigma1 = _norm(av_final)
    return v, sigma1


# ---------------------------------------------------------------------------
# Spectral Anomaly Detector
# ---------------------------------------------------------------------------


class SpectralAnomalyDetector:
    """SVD-based spectral anomaly detector for federated gradient backdoor defense.

    Detects targeted backdoor poisoning attacks by projecting client parameter update
    vectors onto the dominant right singular vector of the stacked gradient matrix.
    Clients with disproportionately high projection scores are flagged as poisoned.
    """

    def __init__(self, config: SpectralDefenseConfig | None = None) -> None:
        self.config = config or SpectralDefenseConfig()

    def compute_spectral_scores(
        self, client_updates: dict[str, dict[str, Any]]
    ) -> dict[str, float]:
        """Compute SVD spectral projection scores s_i = |⟨Δw_i, v₁⟩|² for each client.

        Args:
            client_updates: Mapping of {bank_id: parameter_update_dict}.

        Returns:
            Mapping of {bank_id: spectral_score}.
        """
        node_ids = list(client_updates.keys())
        flat_updates: list[list[float]] = [_flatten(client_updates[nid]) for nid in node_ids]

        if not flat_updates:
            return {}

        # Ensure all rows have the same length (pad or truncate to min)
        min_d = min(len(row) for row in flat_updates)
        matrix = [row[:min_d] for row in flat_updates]

        # Compute multi-rank singular vectors via power iteration and matrix deflation
        k_rank = max(1, min(self.config.singular_value_rank, len(matrix)))
        singular_vectors: list[list[float]] = []
        curr_matrix = [list(row) for row in matrix]

        for _ in range(k_rank):
            vr, sigmar = _power_iteration(curr_matrix)
            if not vr:
                break
            singular_vectors.append(vr)
            # Deflate matrix: A_next = A - σ_r u_r v_r^T
            for row_idx in range(len(curr_matrix)):
                proj_r = _dot(curr_matrix[row_idx], vr)
                for col_idx in range(len(vr)):
                    curr_matrix[row_idx][col_idx] -= proj_r * vr[col_idx]

        if not singular_vectors:
            return {nid: 0.0 for nid in node_ids}

        scores: dict[str, float] = {}
        for nid, row in zip(node_ids, matrix):
            multi_rank_score = sum(_dot(row, vr) ** 2 for vr in singular_vectors)
            scores[nid] = multi_rank_score

        logger.debug(
            "Multi-rank spectral scores computed: k=%d, clients=%d, scores=%s",
            len(singular_vectors),
            len(node_ids),
            {k: round(v, 4) for k, v in scores.items()},
        )
        return scores

    def detect_backdoor_anomalies(
        self, client_updates: dict[str, dict[str, Any]]
    ) -> list[SpectralAnomalyReport]:
        """Identify poisoned client updates exceeding the spectral anomaly threshold.

        Threshold: θ = μ_s + τ · σ_s  where τ = spectral_threshold_multiplier.

        Args:
            client_updates: Mapping of {bank_id: parameter_update_dict}.

        Returns:
            List of SpectralAnomalyReport per client, flagging poisoned nodes.
        """
        if len(client_updates) < self.config.min_clients:
            logger.warning(
                "Spectral defense: insufficient clients (%d < %d). Skipping SVD analysis.",
                len(client_updates),
                self.config.min_clients,
            )
            return [
                SpectralAnomalyReport(
                    node_id=nid,
                    spectral_score=0.0,
                    is_poisoned=False,
                    reason="insufficient_clients_fallback",
                )
                for nid in client_updates
            ]

        scores = self.compute_spectral_scores(client_updates)
        score_values = list(scores.values())

        mu = sum(score_values) / len(score_values)
        variance = sum((s - mu) ** 2 for s in score_values) / len(score_values)
        sigma = math.sqrt(variance)
        threshold = mu + self.config.spectral_threshold_multiplier * sigma

        logger.info(
            "Spectral defense: μ=%.4f, σ=%.4f, θ=%.4f (multiplier=%.1f)",
            mu,
            sigma,
            threshold,
            self.config.spectral_threshold_multiplier,
        )

        reports: list[SpectralAnomalyReport] = []
        for nid, score in scores.items():
            is_poisoned = score > threshold
            if is_poisoned:
                logger.warning(
                    "Backdoor detected: node=%s, score=%.4f > threshold=%.4f. Quarantined.",
                    nid,
                    score,
                    threshold,
                )
            reports.append(
                SpectralAnomalyReport(
                    node_id=nid,
                    spectral_score=round(score, 6),
                    is_poisoned=is_poisoned,
                    reason=(
                        f"score={score:.4f} > threshold={threshold:.4f}"
                        if is_poisoned
                        else f"score={score:.4f} <= threshold={threshold:.4f}"
                    ),
                )
            )

        poisoned_count = sum(1 for r in reports if r.is_poisoned)
        logger.info(
            "Spectral defense complete: %d/%d nodes quarantined as poisoned.",
            poisoned_count,
            len(reports),
        )
        return reports

    def aggregate_robust_spectral(
        self, client_updates: dict[str, dict[str, Any]]
    ) -> dict[str, float]:
        """Filter poisoned client updates and compute clean parameter average.

        Args:
            client_updates: Mapping of {bank_id: parameter_update_dict}.

        Returns:
            Robustly aggregated global parameter update dict.
        """
        reports = self.detect_backdoor_anomalies(client_updates)
        honest_ids = {r.node_id for r in reports if not r.is_poisoned}

        if not honest_ids:
            logger.error(
                "All %d clients quarantined. Returning empty global update.",
                len(client_updates),
            )
            return {}

        honest_updates: list[dict[str, Any]] = [client_updates[nid] for nid in honest_ids]

        # Compute honest-only parameter average
        aggregated: dict[str, float] = {}
        all_keys: set[str] = set().union(*(u.keys() for u in honest_updates))

        for key in all_keys:
            values: list[float] = []
            for update in honest_updates:
                val = update.get(key, 0.0)
                if isinstance(val, (int, float)):
                    values.append(float(val))
                elif isinstance(val, list) and val:
                    values.append(float(val[0]))
            aggregated[key] = sum(values) / len(values) if values else 0.0

        logger.info(
            "Robust spectral aggregation: %d honest / %d total clients, %d params.",
            len(honest_ids),
            len(client_updates),
            len(aggregated),
        )
        return aggregated
