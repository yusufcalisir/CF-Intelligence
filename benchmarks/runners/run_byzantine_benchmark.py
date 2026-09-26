"""Byzantine & Adversarial Robustness Benchmark Runner.

Evaluates defense strategies against malicious client updates:
  - Honest FL (FedAvg with honest clients only)
  - Poisoned FedAvg (zero defense under attack)
  - Coordinate-wise Trimmed Mean
  - Krum (Blanchard et al., 2017)
  - Bulyan (Guerraoui et al., 2018)

Evaluates attack modalities:
  1. Sign Inversion (Delta w_mal = -3.0 * Delta w_honest)
  2. Gaussian Noise Injection (Delta w_mal ~ N(0, 10))
  3. Extreme Outlier Scaling (Delta w_mal = 100.0 * Delta w_honest)

Classification Note: "Simulated adversarial experiment under controlled local testbed."
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


def aggregate_krum(updates: list[np.ndarray], f_byzantine: int) -> np.ndarray:
    """Krum Byzantine-resilient aggregation."""
    n = len(updates)
    if n <= 2 * f_byzantine + 2:
        # Fallback to mean if condition n >= 2f + 3 is violated
        return np.mean(updates, axis=0)

    scores = []
    for i in range(n):
        dists = [float(np.linalg.norm(updates[i] - updates[j]) ** 2) for j in range(n) if i != j]
        dists.sort()
        # Sum of n - f - 2 closest Euclidean distances
        score = sum(dists[: n - f_byzantine - 2])
        scores.append(score)

    best_idx = int(np.argmin(scores))
    return updates[best_idx]


def aggregate_trimmed_mean(updates: list[np.ndarray], trim_ratio: float = 0.2) -> np.ndarray:
    """Coordinate-wise Trimmed Mean aggregation."""
    arr = np.array(updates)  # shape: (n_clients, d)
    n = arr.shape[0]
    k = int(n * trim_ratio)
    if k == 0:
        return np.mean(arr, axis=0)

    # Sort along client axis
    arr_sorted = np.sort(arr, axis=0)
    trimmed = arr_sorted[k : n - k, :]
    return np.mean(trimmed, axis=0)


def aggregate_bulyan(updates: list[np.ndarray], f_byzantine: int) -> np.ndarray:
    """Bulyan aggregation: iterative Krum candidate selection + coordinate trimmed mean."""
    n = len(updates)
    theta = n - 2 * f_byzantine
    if theta <= 0:
        return aggregate_trimmed_mean(updates, trim_ratio=0.2)

    remaining = list(range(n))
    selected_indices = []

    for _ in range(theta):
        sub_updates = [updates[i] for i in remaining]
        best_sub_idx = int(np.argmin([
            sum(sorted([float(np.linalg.norm(sub_updates[i] - sub_updates[j]) ** 2) for j in range(len(sub_updates)) if i != j])[: len(sub_updates) - f_byzantine - 2])
            for i in range(len(sub_updates))
        ]))
        orig_idx = remaining.pop(best_sub_idx)
        selected_indices.append(orig_idx)

    selected_updates = [updates[i] for i in selected_indices]
    return aggregate_trimmed_mean(selected_updates, trim_ratio=0.1)


def run_byzantine_benchmark(
    n_clients: int = 10,
    n_byzantine: int = 2,
    attack_type: str = "sign_inversion",
    seed: int = 42,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n_features = 10
    n_samples = 2000

    # Synthetic fraud distribution
    y_test = (rng.random(n_samples) < 0.03).astype(np.int64)
    X_test = rng.standard_normal((n_samples, n_features)).astype(np.float32)
    X_test[y_test == 1, :2] += 2.0

    # True honest gradient vector direction
    true_direction = np.mean(X_test[y_test == 1], axis=0) - np.mean(X_test[y_test == 0], axis=0)
    true_direction /= np.linalg.norm(true_direction) + 1e-8

    # Generate client updates: honest clients have true direction + small noise
    honest_updates = []
    for _ in range(n_clients - n_byzantine):
        noise = rng.normal(0.0, 0.1, size=n_features)
        honest_updates.append(true_direction + noise)

    # Generate Byzantine malicious updates
    malicious_updates = []
    for _ in range(n_byzantine):
        if attack_type == "sign_inversion":
            mal_vec = -3.0 * true_direction
        elif attack_type == "gaussian_noise":
            mal_vec = rng.normal(0.0, 10.0, size=n_features)
        else:  # extreme outlier
            mal_vec = 100.0 * true_direction
        malicious_updates.append(mal_vec)

    all_updates = honest_updates + malicious_updates

    # Evaluate aggregators
    strategies = {
        "Honest FedAvg (No Attackers)": np.mean(honest_updates, axis=0),
        "Poisoned FedAvg (Under Attack)": np.mean(all_updates, axis=0),
        "Trimmed Mean (20% Coordinate Trim)": aggregate_trimmed_mean(all_updates, trim_ratio=0.2),
        "Krum (Blanchard et al.)": aggregate_krum(all_updates, f_byzantine=n_byzantine),
        "Bulyan (Guerraoui et al.)": aggregate_bulyan(all_updates, f_byzantine=n_byzantine),
    }

    eval_results = {}
    for name, agg_w in strategies.items():
        # Score test samples: sigmoid(X @ agg_w)
        scores = 1.0 / (1.0 + np.exp(-(X_test @ agg_w)))
        pr_auc = float(average_precision_score(y_test, scores))
        roc_auc = float(roc_auc_score(y_test, scores))
        eval_results[name] = {
            "pr_auc": round(pr_auc, 4),
            "roc_auc": round(roc_auc, 4),
        }

    payload = {
        "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "experiment_type": "Simulated adversarial experiment under controlled local testbed",
        "n_clients": n_clients,
        "n_byzantine": n_byzantine,
        "byzantine_fraction": n_byzantine / n_clients,
        "attack_type": attack_type,
        "results": eval_results,
    }

    base_dir = Path(__file__).resolve().parents[2]
    out_file = base_dir / "benchmarks" / "results" / "raw" / f"byzantine_benchmark_{attack_type}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"\n======== BYZANTINE DEFENSE BENCHMARK ({attack_type.upper()}) ========")
    for strat, m in eval_results.items():
        print(f"{strat:<35} | PR-AUC: {m['pr_auc']:.4f} | ROC-AUC: {m['roc_auc']:.4f}")
    print(f"Saved results to: {out_file}\n")

    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Byzantine Defense Benchmark")
    parser.add_argument("--attack", type=str, default="sign_inversion", choices=["sign_inversion", "gaussian_noise", "outlier"])
    parser.add_argument("--byzantine", type=int, default=2)
    args = parser.parse_args()

    run_byzantine_benchmark(n_byzantine=args.byzantine, attack_type=args.attack)
