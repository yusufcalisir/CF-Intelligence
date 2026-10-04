"""Mathematically verified Byzantine-resilient federated aggregation algorithms.

Enforces zero-silent-fallback invariants:
1. Krum: n >= 2f + 3
2. Multi-Krum: n >= 2f + 3, selects m candidates and averages them
3. Bulyan: n >= 4f + 3, two-stage (Multi-Krum candidate selection + coordinate trimmed mean)
4. Trimmed Mean: 2k < n (fails loudly if 2k >= n)
"""

from __future__ import annotations

import logging

import torch

from benchmarks.byzantine.config import AggregatorConfig, ByzantineAggregatorType

logger = logging.getLogger(__name__)


class InvalidConfigurationError(ValueError):
    """Raised when an aggregation algorithm's mathematical preconditions are violated."""
    pass


def _validate_deltas(deltas: list[torch.Tensor]) -> None:
    """Validates deltas list is non-empty and contains no non-finite values (NaN/Inf)."""
    if not deltas:
        raise ValueError("Cannot aggregate empty deltas list.")
    for i, d in enumerate(deltas):
        if torch.isnan(d).any() or torch.isinf(d).any():
            raise ValueError(f"Client update {i} contains non-finite values (NaN/Inf detected).")


def aggregate_fedavg(
    deltas: list[torch.Tensor],
    weights: list[float] | None = None,
) -> torch.Tensor:
    """Computes sample-weighted or unweighted arithmetic mean of client model deltas."""
    _validate_deltas(deltas)
    n = len(deltas)
    stacked = torch.stack(deltas, dim=0)  # shape (n, D)

    if weights is not None:
        if len(weights) != n:
            raise ValueError(f"Weights length ({len(weights)}) does not match deltas count ({n}).")
        total_w = sum(weights)
        if total_w <= 0:
            raise ValueError(f"Total weight must be positive, got {total_w}.")
        norm_weights = torch.tensor(
            [w / total_w for w in weights],
            device=stacked.device,
            dtype=stacked.dtype,
        ).unsqueeze(-1)  # shape (n, 1)
        return torch.sum(stacked * norm_weights, dim=0)

    return torch.mean(stacked, dim=0)


def aggregate_coordinate_median(deltas: list[torch.Tensor]) -> torch.Tensor:
    """Computes coordinate-wise median across client updates (breakdown point f < n/2)."""
    _validate_deltas(deltas)
    stacked = torch.stack(deltas, dim=0)  # shape (n, D)
    median_vals, _ = torch.median(stacked, dim=0)
    return median_vals


def aggregate_trimmed_mean(
    deltas: list[torch.Tensor],
    beta: float = 0.20,
) -> torch.Tensor:
    """Computes coordinate-wise trimmed mean, discarding beta fraction from lowest and highest tails.

    Precondition: 2k < n where k = floor(beta * n).
    """
    _validate_deltas(deltas)
    n = len(deltas)
    k = int(n * beta)

    if 2 * k >= n:
        raise InvalidConfigurationError(
            f"Trimmed Mean infeasible: beta={beta} on n={n} clients requires trimming 2k={2*k} >= {n} items."
        )

    stacked = torch.stack(deltas, dim=0)  # shape (n, D)
    if k == 0:
        return torch.mean(stacked, dim=0)

    sorted_vals, _ = torch.sort(stacked, dim=0)
    trimmed = sorted_vals[k : n - k, :]
    return torch.mean(trimmed, dim=0)


def _compute_krum_scores(deltas: list[torch.Tensor], f: int) -> list[float]:
    """Computes Blanchard et al. Krum score for each update vector."""
    _validate_deltas(deltas)
    n = len(deltas)
    num_closest = max(1, n - f - 2)
    stacked = torch.stack(deltas, dim=0)  # shape (n, D)

    # Compute pairwise squared Euclidean distances: ||u_i - u_j||^2
    diffs = stacked.unsqueeze(1) - stacked.unsqueeze(0)  # shape (n, n, D)
    dists = torch.sum(diffs ** 2, dim=-1)  # shape (n, n)

    scores: list[float] = []
    for i in range(n):
        # Exclude self distance (dists[i, i] == 0)
        row = dists[i]
        mask = torch.ones(n, dtype=torch.bool, device=stacked.device)
        mask[i] = False
        other_dists = row[mask]
        sorted_dists, _ = torch.sort(other_dists)
        score = float(torch.sum(sorted_dists[:num_closest]).item())
        scores.append(score)

    return scores


def aggregate_krum(
    deltas: list[torch.Tensor],
    f: int = 1,
) -> torch.Tensor:
    """Krum Byzantine-resilient aggregation (Blanchard et al., NeurIPS 2017).

    Selects the single update that minimizes the sum of squared Euclidean distances
    to its (n - f - 2) closest neighbors.

    Invariant Precondition: n >= 2f + 3.
    """
    _validate_deltas(deltas)
    n = len(deltas)
    required_n = 2 * f + 3

    if n < required_n:
        raise InvalidConfigurationError(
            f"Krum theoretical precondition violated: requires n >= 2f + 3 (got n={n}, f={f}; required n >= {required_n})."
        )

    scores = _compute_krum_scores(deltas, f)
    best_idx = min(range(n), key=lambda i: (scores[i], i))
    return deltas[best_idx].clone()


def aggregate_multi_krum(
    deltas: list[torch.Tensor],
    f: int = 1,
    m: int | None = None,
) -> torch.Tensor:
    """Multi-Krum aggregation (Blanchard et al., NeurIPS 2017).

    Computes Krum scores for all n candidate updates once on the full pool,
    selects the m updates with the lowest Krum scores (breaking ties deterministically
    by original candidate index), and averages them.

    Invariant Precondition: n >= 2f + 3, 1 <= m <= n - f.
    """
    _validate_deltas(deltas)
    n = len(deltas)
    required_n = 2 * f + 3

    if n < required_n:
        raise InvalidConfigurationError(
            f"Multi-Krum precondition violated: requires n >= 2f + 3 (got n={n}, f={f}; required n >= {required_n})."
        )

    m_selected = m if m is not None else (n - f)
    if m_selected < 1 or m_selected > (n - f):
        raise InvalidConfigurationError(
            f"Multi-Krum candidate count m must satisfy 1 <= m <= n - f (got m={m_selected}, n={n}, f={f})."
        )

    scores = _compute_krum_scores(deltas, f)
    # Deterministic tie-breaking: sort candidate indices by (score, original_index)
    sorted_indices = sorted(range(n), key=lambda i: (scores[i], i))
    best_indices = sorted_indices[:m_selected]
    selected_deltas = [deltas[idx] for idx in best_indices]
    return torch.stack(selected_deltas, dim=0).mean(dim=0)


def get_bulyan_selection_sequence(
    deltas: list[torch.Tensor],
    f: int = 1,
) -> list[int]:
    """Extracts the Stage 1 recursive Krum candidate selection sequence for diagnostic audit."""
    _validate_deltas(deltas)
    n = len(deltas)
    theta = n - 2 * f
    pool: list[tuple[int, torch.Tensor]] = list(enumerate(deltas))
    selection_sequence: list[int] = []

    for t in range(theta):
        m = len(pool)
        if m < 2:
            raise InvalidConfigurationError(
                f"Bulyan Stage 1 defect at iteration {t}: remaining pool size m={m} < 2 (f={f})."
            )
        k = max(1, m - f - 2)

        pool_tensors = [p[1] for p in pool]
        stacked = torch.stack(pool_tensors, dim=0)
        diffs = stacked.unsqueeze(1) - stacked.unsqueeze(0)
        dists = torch.sum(diffs ** 2, dim=-1)

        scores: list[float] = []
        for i in range(m):
            row = dists[i]
            mask = torch.ones(m, dtype=torch.bool, device=stacked.device)
            mask[i] = False
            other_dists = row[mask]
            sorted_dists, _ = torch.sort(other_dists)
            score = float(torch.sum(sorted_dists[:k]).item())
            scores.append(score)

        best_p = min(range(m), key=lambda p_idx: (scores[p_idx], pool[p_idx][0]))
        selection_sequence.append(pool[best_p][0])
        pool.pop(best_p)

    return selection_sequence


def aggregate_bulyan(
    deltas: list[torch.Tensor],
    f: int = 1,
) -> torch.Tensor:
    """Bulyan Byzantine-resilient aggregation (El Mhamdi, Guerraoui, Rouault, ICML 2018).

    Two-stage aggregation combining recursive Krum selection and coordinate-wise median-closest averaging:
      Stage 1 (Recursive Krum Selection):
        Iteratively selects theta = n - 2f candidate updates.
        At each step t from 0 to theta - 1:
          - Evaluates Krum on the CURRENT REMAINING candidate pool P (size m = n - t).
          - Each candidate in P is scored by the sum of squared Euclidean distances to its
            k = max(1, m - f - 2) closest neighbors in P \\ {candidate}.
          - The candidate with the minimum Krum score is selected (ties broken stably by original client index).
          - The selected candidate is appended to selection set S and removed from P.
      Stage 2 (Coordinate-wise Median-Closest Averaging):
        For each coordinate j:
          - Computes coordinate median M_j across the theta candidates in S.
          - Sorts the candidates in S by absolute distance to M_j (|v_{i,j} - M_j|).
          - Retains the beta = theta - 2f = n - 4f closest values.
          - Averages the retained beta values.

    Invariant Preconditions:
      - n >= 4f + 3
      - beta = theta - 2f = n - 4f > 0
      - m >= 2 at all selection iterations
    ZERO SILENT FALLBACK: Fails loudly if n < 4f + 3 or beta <= 0.
    """
    _validate_deltas(deltas)
    n = len(deltas)
    required_n = 4 * f + 3

    if n < required_n:
        raise InvalidConfigurationError(
            f"Bulyan theoretical precondition violated: requires n >= 4f + 3 (got n={n}, f={f}; required n >= {required_n}). "
            f"Zero-fallback invariant enforced: Bulyan will not silently degrade to Krum or Median."
        )

    theta = n - 2 * f
    beta = theta - 2 * f  # beta = n - 4f
    if beta <= 0:
        raise InvalidConfigurationError(
            f"Bulyan stage 2 defect: retained count beta={beta} <= 0 (theta={theta}, f={f})."
        )

    # Stage 1: Recursive Krum selection with removal and recomputation on remaining candidate pool
    # Pool stores (original_index, tensor) to preserve unique candidate identities under duplicate values
    pool: list[tuple[int, torch.Tensor]] = list(enumerate(deltas))
    selected_tuples: list[tuple[int, torch.Tensor]] = []

    for t in range(theta):
        m = len(pool)
        if m < 2:
            raise InvalidConfigurationError(
                f"Bulyan Stage 1 defect at iteration {t}: remaining pool size m={m} < 2 (f={f})."
            )
        k = max(1, m - f - 2)

        pool_tensors = [p[1] for p in pool]
        stacked = torch.stack(pool_tensors, dim=0)  # shape (m, D)
        diffs = stacked.unsqueeze(1) - stacked.unsqueeze(0)  # shape (m, m, D)
        dists = torch.sum(diffs ** 2, dim=-1)  # shape (m, m)

        scores: list[float] = []
        for i in range(m):
            row = dists[i]
            mask = torch.ones(m, dtype=torch.bool, device=stacked.device)
            mask[i] = False
            other_dists = row[mask]
            sorted_dists, _ = torch.sort(other_dists)
            score = float(torch.sum(sorted_dists[:k]).item())
            scores.append(score)

        # Deterministic tie-breaking: primary key = score (min), secondary key = original index (min)
        best_p = min(range(m), key=lambda p_idx: (scores[p_idx], pool[p_idx][0]))
        selected_tuples.append(pool[best_p])
        pool.pop(best_p)

    selected_deltas = [s[1] for s in selected_tuples]
    cand_tensor = torch.stack(selected_deltas, dim=0)  # shape (theta, D)

    # Stage 2: Canonical Bulyan median-closest value selection
    med_vals, _ = torch.median(cand_tensor, dim=0)  # shape (D,)
    abs_diffs = torch.abs(cand_tensor - med_vals.unsqueeze(0))  # shape (theta, D)
    _, topk_indices = torch.topk(abs_diffs, k=beta, dim=0, largest=False)  # shape (beta, D)
    closest_vals = torch.gather(cand_tensor, dim=0, index=topk_indices)  # shape (beta, D)
    return torch.mean(closest_vals, dim=0)


def aggregate_by_strategy(
    agg_type: ByzantineAggregatorType,
    deltas: list[torch.Tensor],
    weights: list[float] | None = None,
    config: AggregatorConfig | None = None,
) -> torch.Tensor:
    """Dispatches to the requested aggregator with strict precondition enforcement."""
    cfg = config or AggregatorConfig(aggregator_type=agg_type)

    if agg_type == ByzantineAggregatorType.FEDAVG:
        return aggregate_fedavg(deltas, weights)
    if agg_type == ByzantineAggregatorType.COORDINATE_MEDIAN:
        return aggregate_coordinate_median(deltas)
    if agg_type == ByzantineAggregatorType.TRIMMED_MEAN:
        return aggregate_trimmed_mean(deltas, beta=cfg.trimmed_mean_beta)
    if agg_type == ByzantineAggregatorType.KRUM:
        return aggregate_krum(deltas, f=cfg.krum_f)
    if agg_type == ByzantineAggregatorType.MULTI_KRUM:
        return aggregate_multi_krum(deltas, f=cfg.krum_f, m=cfg.multi_krum_m)
    if agg_type == ByzantineAggregatorType.BULYAN:
        return aggregate_bulyan(deltas, f=cfg.bulyan_f)

    raise ValueError(f"Unsupported aggregator type: {agg_type}")
