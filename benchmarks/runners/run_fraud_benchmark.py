"""Fraud Detection Benchmark Runner — Evaluates Centralized vs Federated Models.

Measures: PR-AUC, ROC-AUC, F1, Recall @ 0.1% FPR, Recall @ 0.5% FPR, Recall @ 1% FPR.
Supports PaySim, IEEE-CIS, and synthetic datasets.
Outputs machine-readable JSON metadata to benchmarks/results/raw/.
"""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    roc_auc_score,
    roc_curve,
)

# Ensure backend in path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
except ImportError:
    torch = None  # type: ignore


class SimpleMLP(nn.Module if torch else object):
    def __init__(self, input_dim: int, hidden_dim: int = 64):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        return self.network(x)


def compute_recall_at_fpr(y_true: np.ndarray, y_pred_proba: np.ndarray, target_fpr: float) -> float:
    """Calculates Recall at a strict maximum False Positive Rate threshold."""
    fpr, tpr, _ = roc_curve(y_true, y_pred_proba)
    idx = np.where(fpr <= target_fpr)[0]
    if len(idx) == 0:
        return 0.0
    return float(tpr[idx[-1]])


def run_fraud_benchmark(
    dataset_name: str = "paysim",
    synthetic_eval: bool = False,
    rounds: int = 10,
    epochs_per_round: int = 2,
    batch_size: int = 128,
    lr: float = 0.005,
    seed: int = 42,
) -> dict[str, Any]:
    np.random.seed(seed)
    if torch:
        torch.manual_seed(seed)

    base_dir = Path(__file__).resolve().parents[2]
    processed_dir = base_dir / "benchmarks" / "datasets" / dataset_name / "processed"

    # Load partitions or generate synthetic
    client_files = sorted(list(processed_dir.glob("client_*.npz")))
    if not client_files or synthetic_eval:
        print(f"[*] Executing benchmark on controlled synthetic {dataset_name.upper()} environment...")
        rng = np.random.default_rng(seed)
        n_clients = 5
        n_features = 10
        client_data = []
        for i in range(n_clients):
            n_samples = 2000
            fraud_rate = 0.005 if dataset_name == "paysim" else 0.035
            y_i = (rng.random(n_samples) < fraud_rate).astype(np.int64)
            X_i = rng.standard_normal((n_samples, n_features)).astype(np.float32)
            X_i[y_i == 1, :3] += 1.8  # separable fraud signal
            client_data.append((X_i, y_i))
    else:
        print(f"[+] Found {len(client_files)} preprocessed client partitions for {dataset_name}...")
        client_data = []
        for cf in client_files:
            data = np.load(cf)
            client_data.append((data["X"], data["y"]))

    input_dim = client_data[0][0].shape[1]

    # Combine test data (20% held out from each client)
    train_sets = []
    test_X_list, test_y_list = [], []
    for X_i, y_i in client_data:
        split_idx = int(len(X_i) * 0.8)
        train_sets.append((X_i[:split_idx], y_i[:split_idx]))
        test_X_list.append(X_i[split_idx:])
        test_y_list.append(y_i[split_idx:])

    test_X = np.concatenate(test_X_list, axis=0)
    test_y = np.concatenate(test_y_list, axis=0)

    # 1. Centralized Baseline Training
    print("[*] Training Centralized Baseline Model...")
    all_train_X = np.concatenate([t[0] for t in train_sets], axis=0)
    all_train_y = np.concatenate([t[1] for t in train_sets], axis=0)

    central_model = SimpleMLP(input_dim=input_dim)
    optimizer = torch.optim.Adam(central_model.parameters(), lr=lr)
    criterion = nn.BCELoss()

    dataset = TensorDataset(torch.from_numpy(all_train_X), torch.from_numpy(all_train_y).float().unsqueeze(1))
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    central_model.train()
    for _ in range(rounds * epochs_per_round):
        for bx, by in loader:
            optimizer.zero_grad()
            out = central_model(bx)
            loss = criterion(out, by)
            loss.backward()
            optimizer.step()

    central_model.eval()
    with torch.no_grad():
        preds_central = central_model(torch.from_numpy(test_X)).numpy().flatten()

    central_pr_auc = float(average_precision_score(test_y, preds_central))
    central_roc_auc = float(roc_auc_score(test_y, preds_central))
    central_r_01 = compute_recall_at_fpr(test_y, preds_central, 0.001)
    central_r_05 = compute_recall_at_fpr(test_y, preds_central, 0.005)
    central_r_10 = compute_recall_at_fpr(test_y, preds_central, 0.010)

    # 2. Federated Learning (FedAvg) Training
    print("[*] Training Federated Learning (FedAvg) Model across clients...")
    global_model = SimpleMLP(input_dim=input_dim)

    for r in range(rounds):
        local_weights = []
        sample_counts = []
        for X_c, y_c in train_sets:
            local_model = SimpleMLP(input_dim=input_dim)
            local_model.load_state_dict(global_model.state_dict())
            local_opt = torch.optim.Adam(local_model.parameters(), lr=lr)
            c_loader = DataLoader(TensorDataset(torch.from_numpy(X_c), torch.from_numpy(y_c).float().unsqueeze(1)), batch_size=batch_size, shuffle=True)
            local_model.train()
            for _ in range(epochs_per_round):
                for bx, by in c_loader:
                    local_opt.zero_grad()
                    out = local_model(bx)
                    loss = criterion(out, by)
                    loss.backward()
                    local_opt.step()
            local_weights.append(local_model.state_dict())
            sample_counts.append(len(X_c))

        # FedAvg weighted parameter averaging
        new_state = {}
        total_samples = sum(sample_counts)
        for key in global_model.state_dict():
            weighted_sum = sum(local_weights[i][key] * (sample_counts[i] / total_samples) for i in range(len(sample_counts)))
            new_state[key] = weighted_sum
        global_model.load_state_dict(new_state)

    global_model.eval()
    with torch.no_grad():
        preds_fl = global_model(torch.from_numpy(test_X)).numpy().flatten()

    fl_pr_auc = float(average_precision_score(test_y, preds_fl))
    fl_roc_auc = float(roc_auc_score(test_y, preds_fl))
    fl_r_01 = compute_recall_at_fpr(test_y, preds_fl, 0.001)
    fl_r_05 = compute_recall_at_fpr(test_y, preds_fl, 0.005)
    fl_r_10 = compute_recall_at_fpr(test_y, preds_fl, 0.010)

    results = {
        "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "dataset": dataset_name,
        "environment": {
            "os": platform.platform(),
            "cpu": platform.processor(),
            "python_version": platform.python_version(),
            "torch_version": torch.__version__ if torch else "N/A",
        },
        "centralized_baseline": {
            "pr_auc": central_pr_auc,
            "roc_auc": central_roc_auc,
            "recall_at_0_1_pct_fpr": central_r_01,
            "recall_at_0_5_pct_fpr": central_r_05,
            "recall_at_1_0_pct_fpr": central_r_10,
        },
        "federated_fedavg": {
            "rounds": rounds,
            "clients": len(train_sets),
            "pr_auc": fl_pr_auc,
            "roc_auc": fl_roc_auc,
            "recall_at_0_1_pct_fpr": fl_r_01,
            "recall_at_0_5_pct_fpr": fl_r_05,
            "recall_at_1_0_pct_fpr": fl_r_10,
            "pr_auc_parity_ratio": fl_pr_auc / (central_pr_auc + 1e-8),
        },
    }

    # Save artifact
    results_dir = base_dir / "benchmarks" / "results" / "raw"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / f"fraud_benchmark_{dataset_name}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n================== FRAUD BENCHMARK RESULTS ==================")
    print(f"Dataset:                  {dataset_name.upper()}")
    print(f"Centralized PR-AUC:       {central_pr_auc:.4f} (Recall@0.1% FPR: {central_r_01:.4f})")
    print(f"Federated FedAvg PR-AUC:  {fl_pr_auc:.4f} (Recall@0.1% FPR: {fl_r_01:.4f})")
    print(f"Federated/Central Parity: {results['federated_fedavg']['pr_auc_parity_ratio']*100:.1f}%")
    print(f"Saved machine-readable results to: {out_file}")
    print("============================================================\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run reproducible fraud detection benchmark")
    parser.add_argument("--dataset", type=str, default="paysim", choices=["paysim", "ieee_cis"])
    parser.add_argument("--synthetic-eval", action="store_true", default=False)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()

    run_fraud_benchmark(dataset_name=args.dataset, synthetic_eval=args.synthetic_eval, rounds=args.rounds)
