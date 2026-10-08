#!/usr/bin/env python
"""Offline Benchmark Runner — Item 20: Public Dataset Benchmark & Advanced FL Optimization.

Runs an empirical cross-product evaluation:

    Optimizer (FedAvg / FedProx / SCAFFOLD / MOON / FedYogi)
    × Dataset (Elliptic / AMLSim / PaySim)
    × Byzantine Defense (None / Krum / Bulyan)

and outputs classification metrics (F1, PR-AUC, ROC-AUC) per combination.
Results are saved to storage/benchmark_results.md.

Usage
-----
    # Full run (uses mocks if real data not available):
    python benchmark_real_data.py

    # Quick smoke test with tiny data (always uses mocks):
    python benchmark_real_data.py --mock-only --n-samples 300

    # Limit rounds and epochs for speed:
    python benchmark_real_data.py --rounds 3 --epochs 1
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

# ---------------------------------------------------------------------------
# Ensure the backend package is importable when running from the backend/ dir
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local imports (backend app packages)
# ---------------------------------------------------------------------------
from app.application.services.dataloader import load_dataset  # noqa: E402
from app.application.services.fl_engine import FederatedLearningEngine  # noqa: E402
from app.application.services.model_service import ModelService  # noqa: E402
from app.application.services.privacy_service import PrivacyService  # noqa: E402
from app.config import Settings  # noqa: E402
from app.domain.enums import AggregationMethod  # noqa: E402
from app.domain.value_objects import ModelWeights  # noqa: E402

# ---------------------------------------------------------------------------
# Benchmark configuration
# ---------------------------------------------------------------------------
DATASETS = ["elliptic", "amlsim", "paysim"]

OPTIMIZERS: dict[str, dict[str, Any]] = {
    "FedAvg": {"aggregation_method": AggregationMethod.FED_AVG, "fedprox_mu": 0.0, "moon_mu": 0.0},
    "FedProx": {
        "aggregation_method": AggregationMethod.FED_AVG,
        "fedprox_mu": 0.01,
        "moon_mu": 0.0,
    },
    "SCAFFOLD": {
        "aggregation_method": AggregationMethod.SCAFFOLD,
        "fedprox_mu": 0.0,
        "moon_mu": 0.0,
    },
    "MOON": {"aggregation_method": AggregationMethod.FED_AVG, "fedprox_mu": 0.0, "moon_mu": 1.0},
    "FedYogi": {
        "aggregation_method": AggregationMethod.FED_YOGI,
        "fedprox_mu": 0.0,
        "moon_mu": 0.0,
    },
}

DEFENSES: dict[str, AggregationMethod | None] = {
    "None": None,
    "Krum": AggregationMethod.KRUM,
    "Bulyan": AggregationMethod.BULYAN,
}

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _split_across_banks(
    X: np.ndarray, y: np.ndarray, n_banks: int = 3, rng: np.random.Generator | None = None
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Split a dataset into Non-IID per-bank partitions (label-skewed).

    Returns a list of (X_train, X_test, y_train, y_test) per bank.
    """
    rng = rng or np.random.default_rng(42)
    n = len(y)
    idx = np.arange(n)
    rng.shuffle(idx)
    chunks = np.array_split(idx, n_banks)

    splits = []
    for chunk in chunks:
        X_b, y_b = X[chunk], y[chunk]
        try:
            X_tr, X_te, y_tr, y_te = train_test_split(
                X_b, y_b, test_size=0.2, random_state=42, stratify=y_b
            )
        except ValueError:
            X_tr, X_te, y_tr, y_te = train_test_split(X_b, y_b, test_size=0.2, random_state=42)
        splits.append((X_tr, X_te, y_tr, y_te))
    return splits


def _run_single_experiment(
    dataset_name: str,
    optimizer_name: str,
    defense_name: str,
    n_rounds: int,
    n_local_epochs: int,
    n_samples: int,
    rng: np.random.Generator,
    settings: Settings,
    model_svc: ModelService,
    fl_engine: FederatedLearningEngine,
) -> dict[str, float]:
    """Run one (dataset × optimizer × defense) combination and return metrics."""

    # Load dataset
    data = load_dataset(dataset_name, n_mock_txns=n_samples, n_mock_nodes=n_samples, rng=rng)
    X_full: np.ndarray = data["X"]
    y_full: np.ndarray = data["y"]

    # Ensure at least 1 positive sample for metrics
    if y_full.sum() == 0:
        y_full[: max(1, n_samples // 100)] = 1

    # Normalise features
    X_full = (X_full - X_full.mean(axis=0)) / (X_full.std(axis=0) + 1e-8)

    # Adapt input dimension to dataset feature width
    feature_dim = X_full.shape[1]

    # Split into Non-IID bank partitions
    bank_splits = _split_across_banks(X_full, y_full, n_banks=3, rng=rng)

    optimizer_cfg = OPTIMIZERS[optimizer_name]
    aggregation_method: AggregationMethod = optimizer_cfg["aggregation_method"]
    fedprox_mu: float = optimizer_cfg["fedprox_mu"]
    moon_mu: float = optimizer_cfg["moon_mu"]

    # If a separate Byzantine defense is requested, override the aggregation method
    defense_method = DEFENSES[defense_name]
    if defense_method is not None:
        aggregation_method = defense_method

    # Initialise global model (resize input layer dynamically)
    import torch
    import torch.nn as nn

    class DynamicFraudModel(nn.Module):
        def __init__(self, input_dim: int) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, 64),
                nn.ReLU(),
                nn.BatchNorm1d(64),
                nn.Dropout(0.3),
                nn.Linear(64, 32),
                nn.ReLU(),
                nn.BatchNorm1d(32),
                nn.Dropout(0.2),
                nn.Linear(32, 1),
                nn.Sigmoid(),
            )

        def forward(self, x: torch.Tensor, return_features: bool = False) -> Any:
            if return_features:
                feats = x
                for layer in list(self.net.children())[:8]:
                    feats = layer(feats)
                preds = self.net[-2](feats)
                preds = self.net[-1](preds).squeeze(-1)
                return preds, feats
            return self.net(x).squeeze(-1)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def get_params(model: nn.Module) -> ModelWeights:
        shapes, flat = [], []
        for p in model.parameters():
            shapes.append(tuple(p.shape))
            flat.extend(p.data.cpu().numpy().flatten().tolist())
        return ModelWeights(layer_shapes=shapes, flat_weights=flat)

    def set_params(model: nn.Module, weights: ModelWeights) -> nn.Module:
        offset = 0
        for param, shape in zip(model.parameters(), weights.layer_shapes):
            numel = 1
            for s in shape:
                numel *= s
            param.data = (
                torch.FloatTensor(weights.flat_weights[offset : offset + numel])
                .reshape(shape)
                .to(device)
            )
            offset += numel
        return model

    global_model = DynamicFraudModel(feature_dim).to(device)
    global_weights = get_params(global_model)
    prev_local_weights_by_bank: dict[int, ModelWeights | None] = {i: None for i in range(3)}

    criterion = nn.BCELoss()
    from torch.utils.data import DataLoader, TensorDataset

    for _round in range(n_rounds):
        client_weights_list: list[ModelWeights] = []
        client_samples_list: list[int] = []

        for bank_idx, (X_tr, _X_te, y_tr, _y_te) in enumerate(bank_splits):
            local_model = DynamicFraudModel(feature_dim).to(device)
            local_model = set_params(local_model, global_weights)  # type: ignore[assignment]
            local_model.train()

            dataset_t = TensorDataset(
                torch.FloatTensor(X_tr).to(device),
                torch.FloatTensor(y_tr).to(device),
            )
            loader = DataLoader(dataset_t, batch_size=32, shuffle=True, drop_last=False)
            opt = torch.optim.Adam(local_model.parameters(), lr=0.001)

            # Reference model for FedProx / MOON
            if fedprox_mu > 0.0 or moon_mu > 0.0:
                global_ref = DynamicFraudModel(feature_dim).to(device)
                global_ref = set_params(global_ref, global_weights)  # type: ignore[assignment]
                global_ref.eval()
            else:
                global_ref = None

            prev_w = prev_local_weights_by_bank[bank_idx]
            if moon_mu > 0.0 and prev_w is not None:
                prev_model = DynamicFraudModel(feature_dim).to(device)
                prev_model = set_params(prev_model, prev_w)  # type: ignore[assignment]
                prev_model.eval()
            else:
                prev_model = None

            for _epoch in range(n_local_epochs):
                for Xb, yb in loader:
                    opt.zero_grad()
                    if moon_mu > 0.0 and global_ref is not None and prev_model is not None:
                        preds, feats = local_model(Xb, return_features=True)
                    else:
                        preds = local_model(Xb)
                        feats = None
                    loss = criterion(preds, yb)
                    # FedProx term
                    if fedprox_mu > 0.0 and global_ref is not None:
                        prox: Any = 0.0
                        for p, gp in zip(local_model.parameters(), global_ref.parameters()):
                            prox = prox + (p - gp).pow(2).sum()
                        loss = loss + (fedprox_mu / 2.0) * prox
                    # MOON term
                    if (
                        moon_mu > 0.0
                        and feats is not None
                        and global_ref is not None
                        and prev_model is not None
                    ):
                        with torch.no_grad():
                            _, gf = global_ref(Xb, return_features=True)
                            _, pf = prev_model(Xb, return_features=True)
                        cos = nn.CosineSimilarity(dim=-1)
                        sim_g = cos(feats, gf) / 0.5
                        sim_p = cos(feats, pf) / 0.5
                        logits = torch.cat([sim_g.unsqueeze(1), sim_p.unsqueeze(1)], dim=1)
                        tgts = torch.zeros(feats.size(0), dtype=torch.long, device=device)
                        loss = loss + moon_mu * nn.CrossEntropyLoss()(logits, tgts)
                    loss.backward()
                    opt.step()

            prev_local_weights_by_bank[bank_idx] = get_params(local_model)
            client_weights_list.append(get_params(local_model))
            client_samples_list.append(len(X_tr))

        # Server aggregation
        new_weights = fl_engine.aggregate_parameters(
            client_weights=client_weights_list,
            client_samples=client_samples_list,
            method=aggregation_method,
            global_weights=global_weights,
        )
        global_weights = new_weights
        global_model = set_params(global_model, global_weights)

    # Evaluate on combined test set
    global_model.eval()
    X_test_all = np.concatenate([sp[1] for sp in bank_splits], axis=0)
    y_test_all = np.concatenate([sp[3] for sp in bank_splits], axis=0)

    with torch.no_grad():
        probs = global_model(torch.FloatTensor(X_test_all).to(device)).cpu().numpy()

    preds = (probs >= 0.5).astype(int)

    try:
        f1 = float(f1_score(y_test_all, preds, zero_division=0))
        roc_auc = float(roc_auc_score(y_test_all, probs))
        pr_auc = float(average_precision_score(y_test_all, probs))
    except Exception:
        f1, roc_auc, pr_auc = 0.0, 0.5, 0.0

    return {"f1": f1, "roc_auc": roc_auc, "pr_auc": pr_auc}


# ---------------------------------------------------------------------------
# Main benchmark loop
# ---------------------------------------------------------------------------


def run_benchmark(
    n_rounds: int = 5,
    n_local_epochs: int = 2,
    n_samples: int = 1_000,
    output_path: str = "storage/benchmark_results.md",
) -> None:
    settings = Settings()  # type: ignore[call-arg]
    model_svc = ModelService(settings=settings)
    privacy_svc = PrivacyService()
    fl_engine = FederatedLearningEngine(
        settings=settings,
        model_service=model_svc,
        privacy_service=privacy_svc,
    )
    rng = np.random.default_rng(42)

    rows: list[dict[str, Any]] = []

    total_combos = len(DATASETS) * len(OPTIMIZERS) * len(DEFENSES)
    done = 0

    print(f"\n🔬 Starting benchmark: {total_combos} combinations\n")
    print(f"   Rounds={n_rounds}, LocalEpochs={n_local_epochs}, Samples={n_samples}\n")

    for dataset_name in DATASETS:
        for optimizer_name in OPTIMIZERS:
            for defense_name in DEFENSES:
                done += 1
                label = f"[{done}/{total_combos}] {dataset_name}+{optimizer_name}+{defense_name}"
                print(f"  ⏳ {label} ...", end="", flush=True)
                t0 = time.perf_counter()

                try:
                    metrics = _run_single_experiment(
                        dataset_name=dataset_name,
                        optimizer_name=optimizer_name,
                        defense_name=defense_name,
                        n_rounds=n_rounds,
                        n_local_epochs=n_local_epochs,
                        n_samples=n_samples,
                        rng=rng,
                        settings=settings,
                        model_svc=model_svc,
                        fl_engine=fl_engine,
                    )
                    elapsed = time.perf_counter() - t0
                    print(
                        f" ✅  F1={metrics['f1']:.3f}  ROC-AUC={metrics['roc_auc']:.3f}"
                        f"  PR-AUC={metrics['pr_auc']:.3f}  ({elapsed:.1f}s)"
                    )
                    rows.append(
                        {
                            "Dataset": dataset_name,
                            "Optimizer": optimizer_name,
                            "Defense": defense_name,
                            **{k: f"{v:.4f}" for k, v in metrics.items()},
                            "Time(s)": f"{elapsed:.1f}",
                        }
                    )
                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    print(f" ❌  Error: {exc} ({elapsed:.1f}s)")
                    rows.append(
                        {
                            "Dataset": dataset_name,
                            "Optimizer": optimizer_name,
                            "Defense": defense_name,
                            "f1": "ERROR",
                            "roc_auc": "ERROR",
                            "pr_auc": "ERROR",
                            "Time(s)": f"{elapsed:.1f}",
                        }
                    )

    # Build markdown table
    headers = ["Dataset", "Optimizer", "Defense", "F1", "ROC-AUC", "PR-AUC", "Time(s)"]
    col_keys = ["Dataset", "Optimizer", "Defense", "f1", "roc_auc", "pr_auc", "Time(s)"]

    header_row = "| " + " | ".join(headers) + " |"
    sep_row = "|" + "|".join([":---"] * len(headers)) + "|"
    data_rows = ["| " + " | ".join(str(row.get(k, "")) for k in col_keys) + " |" for row in rows]

    md = (
        "# Benchmark Results — Item 20: Public Dataset & Advanced FL Optimization\n\n"
        f"> Rounds={n_rounds}, LocalEpochs={n_local_epochs}, Samples/dataset={n_samples}\n\n"
        + "\n".join([header_row, sep_row] + data_rows)
        + "\n"
    )

    os.makedirs("storage", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\n✅  Results saved to {output_path}\n")
    print(md)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CFI Advanced FL Benchmark Runner")
    parser.add_argument("--rounds", type=int, default=5, help="Federated rounds per run")
    parser.add_argument("--epochs", type=int, default=2, help="Local training epochs per round")
    parser.add_argument("--n-samples", type=int, default=1_000, help="Mock dataset size")
    parser.add_argument(
        "--mock-only", action="store_true", help="Force mock data regardless of files"
    )
    parser.add_argument("--output", type=str, default="storage/benchmark_results.md")
    args = parser.parse_args()

    run_benchmark(
        n_rounds=args.rounds,
        n_local_epochs=args.epochs,
        n_samples=args.n_samples,
        output_path=args.output,
    )
