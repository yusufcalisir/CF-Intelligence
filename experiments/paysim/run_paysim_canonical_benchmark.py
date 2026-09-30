"""PaySim Canonical Real-Data Benchmark — Centralized vs Federated Learning.

Methodology:
    Dataset    : Real PaySim CSV (PS_20174392719_1491204439457_log.csv)
    Sampling   : Systematic every-10th-row sample (~636k rows, ~817 fraud).
                 Preserves temporal ordering, natural fraud prevalence (~0.13%),
                 and full step-range coverage (steps 1–742).
    Split      : Stratified random 80/20 split (seed=42 for data, configurable
                 per run for model). Rationale: PaySim is a Monte-Carlo financial
                 simulation with uniform fraud across steps (no concept drift);
                 temporal split would introduce a 4x fraud-rate imbalance between
                 train and test (confirmed empirically), making PR-AUC
                 incomparable between splits.
    Test set   : ~127k samples, ~179 fraud — statistically meaningful for PR-AUC.
    Partition  : Dirichlet alpha=0.5, 3 clients.
    Budget     : Centralized: num_rounds * local_epochs epochs over pooled data.
                 Federated: num_rounds rounds × local_epochs local epochs/client.
                 Both receive the same optimizer-step budget.
    Seeds      : 42, 123, 456 (model/training seeds, fixed data split seed=42).
    Dataset mode: EXPLICIT — never falls back silently to synthetic data.

Usage:
    python experiments/paysim/run_paysim_canonical_benchmark.py
    python experiments/paysim/run_paysim_canonical_benchmark.py --dataset-mode synthetic
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
from torch.utils.data import DataLoader, TensorDataset

# Ensure repo root on path
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
for p in (REPO_ROOT, BACKEND_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
PAYSIM_CSV_NAMES = [
    "PS_20174392719_1491204439457_log.csv",
    "paysim.csv",
    "paysim1.csv",
]
PAYSIM_FEATURE_COLS = [
    "step",
    "type_TRANSFER", "type_CASH_OUT", "type_PAYMENT", "type_DEBIT", "type_CASH_IN",
    "amount",
    "oldbalanceOrg", "newbalanceOrig",
    "oldbalanceDest", "newbalanceDest",
    "errorBalanceOrig", "errorBalanceDest",
]
SAMPLE_EVERY_NTH = 10          # systematic sample: keep every 10th row
DATA_SPLIT_SEED = 42           # fixed for reproducible data split across seeds
DIRICHLET_ALPHA = 0.5
NUM_CLIENTS = 3
CLIENT_NAMES = ["bank_a", "bank_b", "bank_c"]

OUTPUT_DIR = REPO_ROOT / "experiments" / "paysim"
LEGACY_DIR = OUTPUT_DIR / "legacy_limited_30k"


# ─────────────────────────────────────────────────────────────────────────────
# §1  Dataset loading with explicit real/synthetic mode
# ─────────────────────────────────────────────────────────────────────────────

def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 of physical file bytes (no hardcoding)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _locate_paysim_csv() -> Path:
    """Find PaySim CSV in standard storage location. Raises if not found."""
    base = BACKEND_DIR / "storage" / "datasets" / "paysim"
    for name in PAYSIM_CSV_NAMES:
        candidate = base / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Real PaySim CSV not found in '{base}'. Expected one of: {PAYSIM_CSV_NAMES}. "
        "Run with --dataset-mode synthetic for a smoke test, or place the real dataset file."
    )


def load_real_paysim_sample(csv_path: Path) -> dict[str, Any]:
    """Load systematic every-10th-row sample from real PaySim CSV.

    Sampling design:
    - Row offset 0, 10, 20, ... preserves temporal ordering (steps are sorted).
    - Preserves natural fraud prevalence (~0.129%) without stratification bias.
    - 636k rows fit comfortably in ~32 MB as float32 feature matrices.
    - Reproducible: given same CSV, same result every run.
    """
    import pandas as pd

    logger.info("[PaySim] Loading systematic every-%dth-row sample from %s", SAMPLE_EVERY_NTH, csv_path)
    t0 = time.perf_counter()
    chunks = []
    for chunk in pd.read_csv(str(csv_path), chunksize=200_000):
        mask = np.zeros(len(chunk), dtype=bool)
        mask[::SAMPLE_EVERY_NTH] = True
        chunks.append(chunk[mask])
    df = pd.concat(chunks, ignore_index=True)
    elapsed = time.perf_counter() - t0
    logger.info("[PaySim] Loaded %d rows (%.1fs)", len(df), elapsed)

    # Feature engineering
    for t in ["TRANSFER", "CASH_OUT", "PAYMENT", "DEBIT", "CASH_IN"]:
        df[f"type_{t}"] = (df["type"] == t).astype("float32")
    df["errorBalanceOrig"] = df["newbalanceOrig"] + df["amount"] - df["oldbalanceOrg"]
    df["errorBalanceDest"] = df["oldbalanceDest"] + df["amount"] - df["newbalanceDest"]

    X = df[PAYSIM_FEATURE_COLS].fillna(0).values.astype("float32")
    y = df["isFraud"].values.astype(int)

    n_fraud = int(y.sum())
    return {
        "X": X,
        "y": y,
        "n_rows": len(X),
        "n_fraud": n_fraud,
        "n_legit": len(X) - n_fraud,
        "fraud_rate": float(y.mean()),
        "step_min": int(df["step"].min()),
        "step_max": int(df["step"].max()),
        "source": "real_csv_systematic_10th",
        "csv_path": str(csv_path),
        "sampling_strategy": f"systematic every {SAMPLE_EVERY_NTH}th row",
        "full_rows_approx": len(df) * SAMPLE_EVERY_NTH,
    }


def load_synthetic_paysim(n_samples: int = 50_000, seed: int = 42) -> dict[str, Any]:
    """Minimal synthetic PaySim data for smoke testing only.

    THIS IS NOT A REAL BENCHMARK. Results carry the 'dataset_type: synthetic' flag
    and must never be presented as canonical PaySim performance.
    """
    rng = np.random.default_rng(seed)
    fraud_rate = 0.00129
    n_fraud = max(50, int(n_samples * fraud_rate))
    n_legit = n_samples - n_fraud

    steps = rng.integers(1, 743, size=n_samples).astype("float32")
    amounts_l = rng.lognormal(9.5, 1.5, n_legit).astype("float32")
    amounts_f = rng.lognormal(13.0, 1.2, n_fraud).astype("float32")
    amounts = np.concatenate([amounts_l, amounts_f])

    old_o = np.abs(rng.lognormal(10.0, 2.0, n_samples)).astype("float32")
    new_o = np.maximum(0, old_o - amounts)
    new_o[n_legit:] = 0.0
    old_d = np.abs(rng.lognormal(9.0, 2.2, n_samples)).astype("float32")
    new_d = (old_d + amounts).astype("float32")

    types_l = rng.choice(["TRANSFER", "CASH_OUT", "PAYMENT", "DEBIT", "CASH_IN"],
                          n_legit, p=[0.084, 0.351, 0.338, 0.007, 0.220])
    types_f = rng.choice(["TRANSFER", "CASH_OUT"], n_fraud, p=[0.5, 0.5])
    types = np.concatenate([types_l, types_f])

    X = np.column_stack([
        steps,
        (types == "TRANSFER").astype("float32"),
        (types == "CASH_OUT").astype("float32"),
        (types == "PAYMENT").astype("float32"),
        (types == "DEBIT").astype("float32"),
        (types == "CASH_IN").astype("float32"),
        amounts, old_o, new_o, old_d, new_d,
        (new_o + amounts - old_o).astype("float32"),
        (old_d + amounts - new_d).astype("float32"),
    ])
    y = np.array([0] * n_legit + [1] * n_fraud, dtype=int)
    idx = rng.permutation(n_samples)
    X, y = X[idx], y[idx]
    return {
        "X": X, "y": y, "n_rows": n_samples,
        "n_fraud": int(y.sum()), "n_legit": int((y == 0).sum()),
        "fraud_rate": float(y.mean()), "step_min": 1, "step_max": 742,
        "source": "synthetic_smoke_test",
        "dataset_type": "synthetic",
        "sampling_strategy": f"synthetic generation, seed={seed}",
        "full_rows_approx": n_samples,
    }


# ─────────────────────────────────────────────────────────────────────────────
# §2  Stratified random split (fixed data seed)
# ─────────────────────────────────────────────────────────────────────────────

def stratified_random_split(
    X: np.ndarray,
    y: np.ndarray,
    test_fraction: float = 0.20,
    data_seed: int = DATA_SPLIT_SEED,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Stratified 80/20 split preserving fraud prevalence in both sets.

    Justification: PaySim is a Monte-Carlo simulation with fraud distributed
    uniformly across all 743 simulation steps. Temporal splitting introduces a
    4x fraud-rate imbalance (test rate 0.34% vs train 0.08%), making PR-AUC
    incomparable. Stratified random split produces matching ~0.13% prevalence
    in both train and test sets.
    """
    rng = np.random.default_rng(data_seed)
    classes, counts = np.unique(y, return_counts=True)
    test_idx, train_idx = [], []
    for cls, cnt in zip(classes, counts):
        cls_idx = np.where(y == cls)[0]
        rng.shuffle(cls_idx)
        n_test = max(1, int(cnt * test_fraction))
        test_idx.extend(cls_idx[:n_test].tolist())
        train_idx.extend(cls_idx[n_test:].tolist())
    rng.shuffle(test_idx := np.array(test_idx))
    rng.shuffle(train_idx := np.array(train_idx))
    return X[train_idx], y[train_idx], X[test_idx], y[test_idx]


# ─────────────────────────────────────────────────────────────────────────────
# §3  Dirichlet client partitioning
# ─────────────────────────────────────────────────────────────────────────────

def partition_dirichlet(
    X_train: np.ndarray,
    y_train: np.ndarray,
    n_clients: int = NUM_CLIENTS,
    alpha: float = DIRICHLET_ALPHA,
    seed: int = DATA_SPLIT_SEED,
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], dict[str, dict]]:
    """Dirichlet (alpha) label-skew partition across n_clients."""
    rng = np.random.default_rng(seed)
    idx_all = np.arange(len(y_train))
    client_idx: list[list[int]] = [[] for _ in range(n_clients)]
    for cls in np.unique(y_train):
        c_idx = idx_all[y_train == cls].copy()
        rng.shuffle(c_idx)
        props = rng.dirichlet(np.repeat(alpha, n_clients))
        counts = np.floor(props * len(c_idx)).astype(int)
        rem = len(c_idx) - counts.sum()
        if rem > 0:
            frac = (props * len(c_idx)) - counts
            for ti in np.argsort(frac)[::-1][:rem]:
                counts[ti] += 1
        si = 0
        for k, cnt in enumerate(counts):
            if cnt > 0:
                client_idx[k].extend(c_idx[si : si + cnt])
                si += cnt

    partitions: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    diagnostics: dict[str, dict] = {}
    for k, name in enumerate(CLIENT_NAMES[:n_clients]):
        kidx = np.array(sorted(client_idx[k]), dtype=int)
        X_k, y_k = X_train[kidx], y_train[kidx]
        partitions[name] = (X_k, y_k)
        n_k = len(y_k)
        f_k = int(y_k.sum())
        diagnostics[name] = {
            "n_samples": n_k,
            "n_fraud": f_k,
            "n_legit": n_k - f_k,
            "fraud_rate": float(f_k / n_k) if n_k > 0 else 0.0,
            "volume_share": float(n_k / len(y_train)),
        }
    return partitions, diagnostics


# ─────────────────────────────────────────────────────────────────────────────
# §4  Model + metric utilities (reuse PaySimNeuralClassifier from train_federated)
# ─────────────────────────────────────────────────────────────────────────────

class PaySimMLP(nn.Module):
    """Identical architecture to PaySimNeuralClassifier in train_federated.py."""
    def __init__(self, input_dim: int = 13, hidden_dim: int = 64, dropout: float = 0.2):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 32),
            nn.LayerNorm(32),
            nn.ReLU(),
            nn.Dropout(dropout / 2),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x).squeeze(-1)


def _recall_at_fpr(y_true: np.ndarray, y_proba: np.ndarray, target_fpr: float) -> float:
    if len(np.unique(y_true)) < 2:
        return 0.0
    fpr, tpr, _ = roc_curve(y_true, y_proba)
    idx = np.where(fpr <= target_fpr)[0]
    return float(tpr[idx[-1]]) if len(idx) > 0 else 0.0


def compute_metrics(y_true: np.ndarray, y_proba: np.ndarray) -> dict[str, float]:
    y_b = (y_true > 0).astype(int)
    if len(np.unique(y_b)) < 2:
        return {"pr_auc": 0.0, "roc_auc": 0.5, "recall_at_01pct_fpr": 0.0, "recall_at_1pct_fpr": 0.0}
    return {
        "pr_auc": float(average_precision_score(y_b, y_proba)),
        "roc_auc": float(roc_auc_score(y_b, y_proba)),
        "recall_at_01pct_fpr": _recall_at_fpr(y_b, y_proba, 0.001),
        "recall_at_1pct_fpr": _recall_at_fpr(y_b, y_proba, 0.010),
    }


def evaluate(
    model: nn.Module,
    X: np.ndarray,
    y: np.ndarray,
    batch_size: int = 1024,
    device: torch.device = torch.device("cpu"),
) -> tuple[np.ndarray, dict[str, float]]:
    model.eval()
    dataset = TensorDataset(torch.from_numpy(X), torch.from_numpy(y.astype("float32")))
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False)
    all_probs: list[float] = []
    with torch.no_grad():
        for bx, _ in loader:
            all_probs.extend(model(bx.to(device)).cpu().numpy().tolist())
    proba = np.array(all_probs, dtype="float32")
    return proba, compute_metrics(y, proba)


def _train_epoch(
    model: nn.Module,
    X: np.ndarray,
    y: np.ndarray,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    batch_size: int,
    device: torch.device,
) -> float:
    model.train()
    dataset = TensorDataset(
        torch.from_numpy(X),
        torch.from_numpy(y.astype("float32")),
    )
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    total_loss, n_steps = 0.0, 0
    for bx, by in loader:
        bx, by = bx.to(device), by.to(device)
        optimizer.zero_grad()
        loss = criterion(model(bx), by)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        n_steps += 1
    return total_loss / max(1, n_steps)


# ─────────────────────────────────────────────────────────────────────────────
# §5  Centralized trainer
# ─────────────────────────────────────────────────────────────────────────────

def train_centralized(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    num_epochs: int,
    lr: float = 0.001,
    batch_size: int = 256,
    seed: int = 42,
    device: torch.device = torch.device("cpu"),
) -> dict[str, Any]:
    """Train a single model on the full pooled training set.

    Budget: num_epochs full passes over X_train.
    """
    torch.manual_seed(seed)
    np.random.seed(seed)

    model = PaySimMLP(input_dim=X_train.shape[1]).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.BCELoss()

    history = []
    t0 = time.perf_counter()
    for ep in range(1, num_epochs + 1):
        loss = _train_epoch(model, X_train, y_train, optimizer, criterion, batch_size, device)
        _, metrics = evaluate(model, X_test, y_test, device=device)
        history.append({"epoch": ep, "train_loss": round(loss, 6), **{k: round(v, 6) for k, v in metrics.items()}})
        logger.info("  [Central] Epoch %2d/%d — loss=%.4f PR-AUC=%.4f ROC-AUC=%.4f",
                    ep, num_epochs, loss, metrics["pr_auc"], metrics["roc_auc"])

    _, final_metrics = evaluate(model, X_test, y_test, device=device)
    n_optimizer_steps = num_epochs * int(np.ceil(len(X_train) / batch_size))
    return {
        "final_metrics": final_metrics,
        "history": history,
        "duration_seconds": round(time.perf_counter() - t0, 3),
        "budget": {
            "epochs": num_epochs,
            "samples": len(X_train),
            "batch_size": batch_size,
            "optimizer_steps": n_optimizer_steps,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# §6  FedAvg trainer
# ─────────────────────────────────────────────────────────────────────────────

def _clone(model: nn.Module) -> dict[str, torch.Tensor]:
    return {k: v.detach().clone().cpu() for k, v in model.state_dict().items()}


def _load(model: nn.Module, w: dict[str, torch.Tensor], device: torch.device) -> None:
    model.load_state_dict({k: v.to(device) for k, v in w.items()})


def _fedavg_aggregate(updates: list[tuple[dict, int]]) -> dict[str, torch.Tensor]:
    total = sum(n for _, n in updates)
    agg: dict[str, torch.Tensor] = {}
    for key, val in updates[0][0].items():
        if val.dtype.is_floating_point:
            acc = torch.zeros_like(val)
            for w, n in updates:
                acc.add_(w[key] * (n / total))
            agg[key] = acc
        else:
            agg[key] = val.clone()
    return agg


def train_fedavg(
    client_partitions: dict[str, tuple[np.ndarray, np.ndarray]],
    X_test: np.ndarray,
    y_test: np.ndarray,
    num_rounds: int,
    local_epochs: int,
    lr: float = 0.001,
    batch_size: int = 256,
    seed: int = 42,
    device: torch.device = torch.device("cpu"),
) -> dict[str, Any]:
    """FedAvg training with full budget accounting.

    Budget per client: num_rounds × local_epochs epochs over client data.
    Comparable centralized budget: num_rounds × local_epochs epochs over pooled data.
    """
    torch.manual_seed(seed)
    np.random.seed(seed)

    input_dim = next(iter(client_partitions.values()))[0].shape[1]
    global_model = PaySimMLP(input_dim=input_dim).to(device)
    global_weights = _clone(global_model)
    criterion = nn.BCELoss()

    history = []
    t0 = time.perf_counter()
    total_optimizer_steps = 0

    for r in range(1, num_rounds + 1):
        client_updates: list[tuple[dict, int]] = []
        for name, (X_k, y_k) in client_partitions.items():
            local_model = PaySimMLP(input_dim=input_dim).to(device)
            _load(local_model, global_weights, device)
            opt = torch.optim.Adam(local_model.parameters(), lr=lr)
            for _ in range(local_epochs):
                _train_epoch(local_model, X_k, y_k, opt, criterion, batch_size, device)
                steps_k = int(np.ceil(len(X_k) / batch_size))
                total_optimizer_steps += steps_k
            client_updates.append((_clone(local_model), len(y_k)))

        global_weights = _fedavg_aggregate(client_updates)
        _load(global_model, global_weights, device)

        _, metrics = evaluate(global_model, X_test, y_test, device=device)
        history.append({"round": r, **{k: round(v, 6) for k, v in metrics.items()}})
        logger.info("  [FedAvg]  Round %2d/%d — PR-AUC=%.4f ROC-AUC=%.4f",
                    r, num_rounds, metrics["pr_auc"], metrics["roc_auc"])

    _, final_metrics = evaluate(global_model, X_test, y_test, device=device)
    total_samples_per_client = {n: len(y) for n, (_, y) in client_partitions.items()}
    return {
        "final_metrics": final_metrics,
        "history": history,
        "duration_seconds": round(time.perf_counter() - t0, 3),
        "budget": {
            "rounds": num_rounds,
            "local_epochs": local_epochs,
            "total_local_optimizer_steps": total_optimizer_steps,
            "client_samples": total_samples_per_client,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# §7  Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_canonical_benchmark(
    dataset_mode: str = "real",
    num_rounds: int = 10,
    local_epochs: int = 3,
    lr: float = 0.001,
    batch_size: int = 256,
    seeds: list[int] | None = None,
    save_artifact: bool = True,
) -> dict[str, Any]:
    """Run multi-seed centralized vs FedAvg benchmark on real PaySim data.

    Args:
        dataset_mode: 'real' (fail hard if CSV missing) or 'synthetic' (smoke test).
        num_rounds: FL communication rounds (centralized uses same × local_epochs total epochs).
        local_epochs: Local epochs per FL round. Budget: num_rounds × local_epochs per side.
        lr: Adam learning rate.
        batch_size: Mini-batch size.
        seeds: Model/training seeds. Data split always uses seed=42.
        save_artifact: Write canonical_results.json to experiments/paysim/.
    """
    if seeds is None:
        seeds = [42, 123, 456]

    if dataset_mode not in ("real", "synthetic"):
        raise ValueError(f"dataset_mode must be 'real' or 'synthetic', got '{dataset_mode}'")

    # ── Dataset loading ─────────────────────────────────────────────────────
    dataset_sha256: str | None = None
    if dataset_mode == "real":
        csv_path = _locate_paysim_csv()
        logger.info("[PaySim] Computing SHA-256 of %s ...", csv_path)
        dataset_sha256 = _compute_sha256(csv_path)
        logger.info("[PaySim] SHA-256: %s", dataset_sha256)
        raw = load_real_paysim_sample(csv_path)
    else:
        logger.warning("[PaySim] SYNTHETIC MODE -- results are smoke-test only, NOT canonical.")
        raw = load_synthetic_paysim(n_samples=50_000, seed=DATA_SPLIT_SEED)

    X_all, y_all = raw["X"], raw["y"]

    # ── Stratified random split (fixed data seed) ───────────────────────────
    X_train, y_train, X_test, y_test = stratified_random_split(
        X_all, y_all, test_fraction=0.20, data_seed=DATA_SPLIT_SEED
    )
    split_info = {
        "strategy": "stratified_random_80_20",
        "data_seed": DATA_SPLIT_SEED,
        "train_n": int(len(X_train)),
        "train_fraud": int(y_train.sum()),
        "train_legit": int((y_train == 0).sum()),
        "train_fraud_rate": float(y_train.mean()),
        "test_n": int(len(X_test)),
        "test_fraud": int(y_test.sum()),
        "test_legit": int((y_test == 0).sum()),
        "test_fraud_rate": float(y_test.mean()),
        "split_justification": (
            "PaySim is a Monte-Carlo simulation with fraud uniform across steps 1-742 "
            "(no concept drift). Temporal split introduces 4x fraud-rate imbalance "
            "between train and test sets, making PR-AUC incomparable. "
            "Stratified random split preserves natural ~0.13% prevalence in both sets."
        ),
    }
    logger.info("[PaySim] Train: %d samples, %d fraud (%.4f%%)",
                split_info["train_n"], split_info["train_fraud"], split_info["train_fraud_rate"] * 100)
    logger.info("[PaySim] Test:  %d samples, %d fraud (%.4f%%)",
                split_info["test_n"], split_info["test_fraud"], split_info["test_fraud_rate"] * 100)

    # ── Dirichlet client partition ───────────────────────────────────────────
    partitions, partition_diag = partition_dirichlet(
        X_train, y_train, n_clients=NUM_CLIENTS, alpha=DIRICHLET_ALPHA, seed=DATA_SPLIT_SEED
    )
    logger.info("[PaySim] Client partitions (Dirichlet alpha=%.1f):", DIRICHLET_ALPHA)
    for name, diag in partition_diag.items():
        logger.info("  %s: n=%d, fraud=%d (%.3f%%)",
                    name, diag["n_samples"], diag["n_fraud"], diag["fraud_rate"] * 100)

    # Budget definition: centralized gets num_rounds * local_epochs epochs
    central_epochs = num_rounds * local_epochs

    # ── Multi-seed runs ──────────────────────────────────────────────────────
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    seed_results: list[dict] = []

    for seed in seeds:
        logger.info("[PaySim] ─── Seed %d ───────────────────────────────", seed)

        # Centralized
        logger.info("[PaySim] Training Centralized (seed=%d, epochs=%d)...", seed, central_epochs)
        central_result = train_centralized(
            X_train, y_train, X_test, y_test,
            num_epochs=central_epochs, lr=lr, batch_size=batch_size,
            seed=seed, device=device,
        )

        # FedAvg
        logger.info("[PaySim] Training FedAvg (seed=%d, rounds=%d × local_epochs=%d)...",
                    seed, num_rounds, local_epochs)
        fedavg_result = train_fedavg(
            partitions, X_test, y_test,
            num_rounds=num_rounds, local_epochs=local_epochs,
            lr=lr, batch_size=batch_size, seed=seed, device=device,
        )

        seed_results.append({
            "seed": seed,
            "centralized": {
                "pr_auc": central_result["final_metrics"]["pr_auc"],
                "roc_auc": central_result["final_metrics"]["roc_auc"],
                "recall_at_01pct_fpr": central_result["final_metrics"]["recall_at_01pct_fpr"],
                "budget": central_result["budget"],
                "duration_seconds": central_result["duration_seconds"],
            },
            "fedavg": {
                "pr_auc": fedavg_result["final_metrics"]["pr_auc"],
                "roc_auc": fedavg_result["final_metrics"]["roc_auc"],
                "recall_at_01pct_fpr": fedavg_result["final_metrics"]["recall_at_01pct_fpr"],
                "budget": fedavg_result["budget"],
                "duration_seconds": fedavg_result["duration_seconds"],
            },
            "delta_pr_auc": round(
                fedavg_result["final_metrics"]["pr_auc"] - central_result["final_metrics"]["pr_auc"], 6
            ),
        })

    # ── Aggregate statistics ─────────────────────────────────────────────────
    central_aucs = [r["centralized"]["pr_auc"] for r in seed_results]
    fedavg_aucs  = [r["fedavg"]["pr_auc"] for r in seed_results]
    deltas        = [r["delta_pr_auc"] for r in seed_results]

    def _stats(vals: list[float]) -> dict[str, float]:
        a = np.array(vals)
        return {
            "mean": round(float(a.mean()), 6),
            "std": round(float(a.std()), 6),
            "min": round(float(a.min()), 6),
            "max": round(float(a.max()), 6),
        }

    aggregate = {
        "centralized_pr_auc": _stats(central_aucs),
        "fedavg_pr_auc": _stats(fedavg_aucs),
        "delta_pr_auc": _stats(deltas),
    }

    # Print summary
    logger.info("[PaySim] ========== CANONICAL RESULTS ==========")
    logger.info("[PaySim] Centralized PR-AUC: %.4f +/- %.4f (min=%.4f, max=%.4f)",
                aggregate["centralized_pr_auc"]["mean"], aggregate["centralized_pr_auc"]["std"],
                aggregate["centralized_pr_auc"]["min"],  aggregate["centralized_pr_auc"]["max"])
    logger.info("[PaySim] FedAvg PR-AUC:      %.4f +/- %.4f (min=%.4f, max=%.4f)",
                aggregate["fedavg_pr_auc"]["mean"], aggregate["fedavg_pr_auc"]["std"],
                aggregate["fedavg_pr_auc"]["min"],  aggregate["fedavg_pr_auc"]["max"])
    logger.info("[PaySim] delta PR-AUC (FL-Central): %.4f +/- %.4f",
                aggregate["delta_pr_auc"]["mean"], aggregate["delta_pr_auc"]["std"])

    # ── Assemble artifact ────────────────────────────────────────────────────
    artifact: dict[str, Any] = {
        "schema_version": "2.0.0",
        "benchmark_id": "paysim_canonical_real_data",
        "generated_at_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "dataset": {
            "name": "PaySim Mobile Money Fraud",
            "dataset_type": raw.get("dataset_type", "real"),
            "source": raw["source"],
            "csv_path": raw.get("csv_path"),
            "sha256": dataset_sha256,
            "sampling_strategy": raw["sampling_strategy"],
            "sample_every_nth": SAMPLE_EVERY_NTH,
            "n_rows_sampled": raw["n_rows"],
            "n_rows_full_approx": raw["full_rows_approx"],
            "n_fraud": raw["n_fraud"],
            "n_legit": raw["n_legit"],
            "fraud_rate": raw["fraud_rate"],
            "step_range": [raw["step_min"], raw["step_max"]],
            "feature_cols": PAYSIM_FEATURE_COLS,
        },
        "split": split_info,
        "partition": {
            "algorithm": "dirichlet",
            "alpha": DIRICHLET_ALPHA,
            "n_clients": NUM_CLIENTS,
            "data_seed": DATA_SPLIT_SEED,
            "client_diagnostics": partition_diag,
        },
        "budget": {
            "description": "Centralized: num_rounds × local_epochs epochs over pooled data. FedAvg: num_rounds rounds × local_epochs per client.",
            "num_rounds": num_rounds,
            "local_epochs": local_epochs,
            "centralized_epochs": central_epochs,
        },
        "per_seed_results": seed_results,
        "aggregate": aggregate,
        "legacy_results": {
            "note": "These are PRESERVED historical results. Do NOT use as canonical.",
            "synthetic_fallback": {
                "centralized_pr_auc": 0.4654,
                "fedavg_pr_auc": 0.1463,
                "source": "benchmarks/runners/run_fraud_benchmark.py",
                "dataset_type": "synthetic_10_feature_fallback",
                "rounds": 3,
                "clients": 5,
                "status": "ARCHIVED — produced when benchmarks/datasets/paysim/processed/ was missing",
            },
            "limited_30k_real": {
                "fedavg_pr_auc": 0.11838,
                "source": "experiments/paysim/train_federated.py",
                "dataset_type": "real_csv_first_30k_rows",
                "nrows": 30000,
                "test_fraud_count": 3,
                "status": "ARCHIVED — test set had only 3 fraud examples; PR-AUC statistically unreliable",
                "sha256_bug": "results.json recorded sha256('') (empty string hash) instead of real file hash",
            },
        },
    }

    if save_artifact:
        out_path = OUTPUT_DIR / "canonical_results.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2)
        logger.info("[PaySim] Canonical results saved to %s", out_path)

    return artifact


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="PaySim Canonical Real-Data Benchmark")
    parser.add_argument(
        "--dataset-mode",
        choices=["real", "synthetic"],
        default="real",
        help="'real': use physical PaySim CSV (fail hard if missing). "
             "'synthetic': smoke test only, NOT a canonical benchmark.",
    )
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--local-epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 123, 456])
    args = parser.parse_args()

    results = run_canonical_benchmark(
        dataset_mode=args.dataset_mode,
        num_rounds=args.rounds,
        local_epochs=args.local_epochs,
        lr=args.lr,
        batch_size=args.batch_size,
        seeds=args.seeds,
    )

    agg = results["aggregate"]
    print("\n" + "=" * 70)
    print("PAYSIM CANONICAL BENCHMARK RESULTS")
    print("=" * 70)
    print(f"Dataset type    : {results['dataset']['dataset_type']}")
    print(f"Sample size     : {results['dataset']['n_rows_sampled']:,} rows ({results['dataset']['sampling_strategy']})")
    print(f"SHA-256         : {results['dataset']['sha256']}")
    print(f"Test fraud count: {results['split']['test_fraud']}")
    print(f"Seeds           : {[r['seed'] for r in results['per_seed_results']]}")
    print()
    print(f"Centralized PR-AUC : {agg['centralized_pr_auc']['mean']:.4f} +/- {agg['centralized_pr_auc']['std']:.4f}")
    print(f"FedAvg PR-AUC      : {agg['fedavg_pr_auc']['mean']:.4f} +/- {agg['fedavg_pr_auc']['std']:.4f}")
    print(f"delta (FL - Central): {agg['delta_pr_auc']['mean']:.4f} +/- {agg['delta_pr_auc']['std']:.4f}")
    print()
    print("Per-seed breakdown:")
    for r in results["per_seed_results"]:
        print(f"  seed={r['seed']}: Central={r['centralized']['pr_auc']:.4f}  "
              f"FedAvg={r['fedavg']['pr_auc']:.4f}  delta={r['delta_pr_auc']:.4f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
