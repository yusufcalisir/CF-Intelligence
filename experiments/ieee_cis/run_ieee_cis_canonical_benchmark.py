"""IEEE-CIS Canonical Real-Data Benchmark Execution Harness.

Methodology & Invariants:
    Dataset    : Real IEEE-CIS Fraud Detection (train_transaction.csv, train_identity.csv)
                 Source: Kaggle / Vesta Corporation (590,540 transactions, 434 merged features).
    Split      : Strict temporal split along 'TransactionDT' axis (zero future lookahead).
                 Chronological split: 80% train, 20% untouched out-of-time test set.
    Partition  : 3-bank federated consortium (Bank A, Bank B, Bank C) partitioned via
                 Dirichlet distribution (alpha=0.5) over temporal training partition.
    Paradigms  :
                 1. Centralized Pooled Baseline (Equalized computation budget: 10 epochs).
                 2. Federated FedAvg (3 clients, 5 communication rounds, 2 local epochs).
    Evaluation : Out-of-time holdout test evaluation: PR-AUC (Average Precision), ROC-AUC, Brier score,
                 Recall @ strict False Positive Rates (0.01%, 0.05%, 0.1%, 0.5%, 1.0% FPR).
    Seeds      : Multi-seed statistical robustness across seeds [42, 123, 456].
    Provenance : Strict real vs synthetic dataset isolation. Real mode strictly requires physical CSVs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from torch.utils.data import DataLoader, TensorDataset

# Ensure repository root and backend directory are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
for p in (REPO_ROOT, BACKEND_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from benchmarks.provenance_schema import (
    ArtifactStatus,
    CanonicalBenchmarkArtifact,
    CommunicationEligibility,
    DatasetProvenance,
    DatasetProvenanceType,
    EvidenceScope,
    ExecutionBudget,
)
from experiments.ieee_cis.temporal_split import IEEECISPartitioner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("experiments.ieee_cis.canonical_benchmark")

SEEDS = [42, 123, 456]
NUM_CLIENTS = 3
CLIENT_NAMES = ["bank_a", "bank_b", "bank_c"]
DIRICHLET_ALPHA = 0.5
TEST_RATIO = 0.20
DEFAULT_ROUNDS = 5
DEFAULT_LOCAL_EPOCHS = 2
BATCH_SIZE = 512
LEARNING_RATE = 1e-3


def compute_file_sha256(path: Path) -> str:
    """Compute physical SHA-256 digest of a file in 64KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_git_commit() -> str:
    """Retrieve current Git commit SHA-1."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "uncommitted_clean_tree"


class IEEECISNeuralClassifier(nn.Module):
    """Deep tabular neural classifier for IEEE-CIS transaction scoring.

    Uses LayerNorm to avoid running statistics drift during federated averaging
    across non-IID client institutions.
    """

    def __init__(self, input_dim: int, hidden_dim: int = 128, dropout: float = 0.2) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 64),
            nn.LayerNorm(64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).view(-1)


def compute_operational_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    target_fprs: list[float] | None = None,
) -> dict[str, Any]:
    """Compute fixed-FPR operating points and standard classification metrics."""
    if target_fprs is None:
        target_fprs = [0.0001, 0.0005, 0.001, 0.005, 0.010]

    n_pos = int(np.sum(y_true == 1))
    n_neg = int(np.sum(y_true == 0))

    if len(np.unique(y_true)) < 2:
        return {
            "pr_auc": 0.0,
            "roc_auc": 0.5,
            "brier_score": float(np.mean((y_prob - y_true) ** 2)),
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
            "operating_points": [],
            "positive_support": n_pos,
            "negative_support": n_neg,
        }

    pr_auc = float(average_precision_score(y_true, y_prob))
    roc_auc = float(roc_auc_score(y_true, y_prob))
    brier = float(brier_score_loss(y_true, y_prob))

    # Standard metrics at fixed 0.5 decision threshold
    y_pred_05 = (y_prob >= 0.5).astype(int)
    prec_05 = float(precision_score(y_true, y_pred_05, zero_division=0))
    rec_05 = float(recall_score(y_true, y_pred_05, zero_division=0))
    f1_05 = float(f1_score(y_true, y_pred_05, zero_division=0))

    fpr_curve, tpr_curve, thresholds = roc_curve(y_true, y_prob)

    op_points: list[dict[str, Any]] = []
    for tfpr in target_fprs:
        raw_idx = np.searchsorted(fpr_curve, tfpr, side="right") - 1
        idx_val = int(raw_idx)
        idx_clamped = max(0, min(idx_val, len(tpr_curve) - 1))
        thresh = float(thresholds[idx_clamped]) if idx_clamped < len(thresholds) else 0.5
        emp_fpr = float(fpr_curve[idx_clamped])
        rec = float(tpr_curve[idx_clamped])

        preds = (y_prob >= thresh).astype(int)
        tn, fp, fn, tp = confusion_matrix(y_true, preds).ravel()
        prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        alerts = int(tp + fp)

        op_points.append({
            "target_fpr": tfpr,
            "empirical_fpr": emp_fpr,
            "threshold": thresh,
            "recall": rec,
            "precision": prec,
            "tp": int(tp),
            "fp": int(fp),
            "tn": int(tn),
            "fn": int(fn),
            "alerts": alerts,
            "positive_support": n_pos,
            "negative_support": n_neg,
        })

    op_by_fpr = {f"recall_at_{int(p['target_fpr']*10000):04d}_fpr": p["recall"] for p in op_points}

    return {
        "pr_auc": pr_auc,
        "roc_auc": roc_auc,
        "brier_score": brier,
        "precision": prec_05,
        "recall": rec_05,
        "f1_score": f1_05,
        "recall_at_001_fpr": op_by_fpr.get("recall_at_0001_fpr", 0.0),
        "recall_at_005_fpr": op_by_fpr.get("recall_at_0005_fpr", 0.0),
        "recall_at_01_fpr": op_by_fpr.get("recall_at_0010_fpr", 0.0),
        "recall_at_05_fpr": op_by_fpr.get("recall_at_0050_fpr", 0.0),
        "recall_at_10_fpr": op_by_fpr.get("recall_at_0100_fpr", 0.0),
        "operating_points": op_points,
        "positive_support": n_pos,
        "negative_support": n_neg,
    }


def train_single_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    device: torch.device,
) -> tuple[float, int]:
    """Train model for a single epoch. Returns (avg_loss, steps_executed)."""
    model.train()
    total_loss = 0.0
    total_samples = 0
    steps = 0
    for x_b, y_b in loader:
        x_b, y_b = x_b.to(device), y_b.to(device)
        optimizer.zero_grad()
        logits = model(x_b)
        loss = criterion(logits, y_b)
        loss.backward()
        optimizer.step()
        steps += 1
        batch_size = x_b.size(0)
        total_loss += loss.item() * batch_size
        total_samples += batch_size
    return total_loss / max(1, total_samples), steps


def evaluate_model(
    model: nn.Module,
    loader: DataLoader,
    device: torch.device,
) -> dict[str, Any]:
    """Evaluate model on test partition and compute operational metrics."""
    model.eval()
    all_probs: list[np.ndarray] = []
    all_targets: list[np.ndarray] = []
    with torch.no_grad():
        for x_b, y_b in loader:
            x_b = x_b.to(device)
            logits = model(x_b)
            probs = torch.sigmoid(logits).cpu().numpy()
            all_probs.append(probs)
            all_targets.append(y_b.numpy())

    y_prob = np.concatenate(all_probs)
    y_true = np.concatenate(all_targets)

    return compute_operational_metrics(y_true, y_prob)


def run_canonical_ieee_cis_benchmark(
    dataset_mode: str = "real",
    rounds: int = DEFAULT_ROUNDS,
    local_epochs: int = DEFAULT_LOCAL_EPOCHS,
    seeds: list[int] | None = None,
    execute_full_590k: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute canonical multi-seed IEEE-CIS benchmark across centralized and federated settings."""
    if seeds is None:
        seeds = SEEDS

    storage_dir = BACKEND_DIR / "storage" / "datasets" / "ieee_cis"
    trans_path = storage_dir / "train_transaction.csv"
    id_path = storage_dir / "train_identity.csv"

    if not trans_path.is_file() or not id_path.is_file():
        raise FileNotFoundError(
            f"Canonical IEEE-CIS benchmark requires physical Kaggle dataset at '{trans_path}' and '{id_path}'."
        )

    # 1. Compute physical SHA-256 hashes
    logger.info("Computing physical SHA-256 digests...")
    trans_sha256 = compute_file_sha256(trans_path)
    id_sha256 = compute_file_sha256(id_path)
    git_sha = get_git_commit()
    timestamp_start = datetime.now(UTC).isoformat()

    logger.info("=" * 80)
    logger.info("  CANONICAL IEEE-CIS BENCHMARK PROTOCOL FROZEN")
    logger.info("=" * 80)
    logger.info("  train_transaction.csv : %s bytes | SHA-256: %s", trans_path.stat().st_size, trans_sha256)
    logger.info("  train_identity.csv    : %s bytes | SHA-256: %s", id_path.stat().st_size, id_sha256)
    logger.info("  Git Commit            : %s", git_sha)
    logger.info("  Seeds                 : %s", seeds)
    logger.info("  Clients               : %d (%s)", NUM_CLIENTS, CLIENT_NAMES)
    logger.info("  Dirichlet Alpha       : %.2f", DIRICHLET_ALPHA)
    logger.info("  Federated Budget      : %d rounds x %d local epochs", rounds, local_epochs)
    logger.info("  Centralized Budget    : %d epochs (budget-equalized)", rounds * local_epochs)
    logger.info("  Batch Size            : %d | LR: %e", BATCH_SIZE, LEARNING_RATE)
    logger.info("  Started At            : %s", timestamp_start)
    logger.info("=" * 80)

    # 2. Ingest real dataset and partition
    logger.info("Ingesting full 590k IEEE-CIS dataset via IEEECISPartitioner...")
    partitioner = IEEECISPartitioner(
        alpha=DIRICHLET_ALPHA,
        num_clients=NUM_CLIENTS,
        client_names=CLIENT_NAMES,
        seed=42,
        test_ratio=TEST_RATIO,
    )
    partitioner.load_data(all_rows=True, require_real=True)

    # Verification of TransactionDT leakage prevention
    assert "TransactionDT" not in partitioner.feature_names, "Leakage: TransactionDT in feature_names!"
    assert partitioner.temporal_cutoff_dt is not None

    if (
        partitioner.X_train is None
        or partitioner.X_test is None
        or partitioner.y_train is None
        or partitioner.y_test is None
        or partitioner.dt_train is None
        or partitioner.dt_test is None
    ):
        raise RuntimeError("Partitioner arrays must be loaded and non-None before proceeding.")

    X_train = partitioner.X_train
    X_test = partitioner.X_test
    y_train = partitioner.y_train
    y_test = partitioner.y_test
    dt_train = partitioner.dt_train
    dt_test = partitioner.dt_test

    train_n = len(X_train)
    test_n = len(X_test)
    train_fraud = int(np.sum(y_train))
    test_fraud = int(np.sum(y_test))
    train_prev = float(np.mean(y_train))
    test_prev = float(np.mean(y_test))
    train_min_dt = float(np.min(dt_train))
    train_max_dt = float(np.max(dt_train))
    test_min_dt = float(np.min(dt_test))
    test_max_dt = float(np.max(dt_test))
    assert train_max_dt <= test_min_dt, f"Temporal leakage: train max dt ({train_max_dt}) > test min dt ({test_min_dt})"

    logger.info("Temporal Split Verified: Train=%d (Fraud=%d, %.2f%%), Test=%d (Fraud=%d, %.2f%%)",
                train_n, train_fraud, train_prev * 100, test_n, test_fraud, test_prev * 100)
    logger.info("Temporal Cutoff: [%.1f, %.1f] <= [%.1f, %.1f] (zero future leakage)",
                train_min_dt, train_max_dt, test_min_dt, test_max_dt)

    # Fit standardizer strictly on training data
    logger.info("Fitting standardizer strictly on training partition...")
    partitioner.fit_standardizer()
    assert partitioner.X_train is not None and partitioner.X_test is not None
    input_dim = partitioner.X_train.shape[1]
    logger.info("Feature count: %d input features", input_dim)

    # Prepare untouched global test DataLoader
    test_ds = TensorDataset(
        torch.tensor(partitioner.X_test, dtype=torch.float32),
        torch.tensor(y_test, dtype=torch.float32),
    )
    test_loader = DataLoader(test_ds, batch_size=BATCH_SIZE, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Compute device: %s", device)

    # Multi-seed execution
    seed_results: list[dict[str, Any]] = []
    partition_diagnostics_by_seed: dict[str, Any] = {}

    for seed in seeds:
        logger.info("\n" + "#" * 60)
        logger.info("  EXECUTING PREDEFINED SEED: %d", seed)
        logger.info("#" * 60)

        # Partition data deterministically for this seed
        p_seed = IEEECISPartitioner(
            alpha=DIRICHLET_ALPHA,
            num_clients=NUM_CLIENTS,
            client_names=CLIENT_NAMES,
            seed=seed,
            test_ratio=TEST_RATIO,
        )
        p_seed.X_all = partitioner.X_all
        p_seed.y_all = partitioner.y_all
        p_seed.dt_all = partitioner.dt_all
        p_seed.feature_names = partitioner.feature_names
        p_seed.X_train = partitioner.X_train
        p_seed.y_train = partitioner.y_train
        p_seed.dt_train = partitioner.dt_train
        p_seed.X_test = partitioner.X_test
        p_seed.y_test = partitioner.y_test
        p_seed.dt_test = partitioner.dt_test

        client_dict = p_seed.partition_dirichlet()

        client_stats = {}
        for c in CLIENT_NAMES:
            c_y = client_dict[c][1]
            c_n = len(c_y)
            c_fraud = int(np.sum(c_y))
            client_stats[c] = {
                "n_samples": c_n,
                "fraud_count": c_fraud,
                "fraud_prevalence": c_fraud / c_n if c_n > 0 else 0.0,
                "pct_global_train": c_n / train_n * 100.0,
            }
        partition_diagnostics_by_seed[str(seed)] = client_stats
        logger.info("Client partition for seed %d: %s", seed, client_stats)

        # Prepare pooled training loader
        X_train_pooled = np.concatenate([client_dict[c][0] for c in CLIENT_NAMES])
        y_train_pooled = np.concatenate([client_dict[c][1] for c in CLIENT_NAMES])
        pooled_ds = TensorDataset(
            torch.tensor(X_train_pooled, dtype=torch.float32),
            torch.tensor(y_train_pooled, dtype=torch.float32),
        )
        pooled_loader = DataLoader(pooled_ds, batch_size=BATCH_SIZE, shuffle=True)

        # Prepare client loaders
        client_loaders = {}
        for c in CLIENT_NAMES:
            c_ds = TensorDataset(
                torch.tensor(client_dict[c][0], dtype=torch.float32),
                torch.tensor(client_dict[c][1], dtype=torch.float32),
            )
            client_loaders[c] = DataLoader(c_ds, batch_size=BATCH_SIZE, shuffle=True)

        # -----------------------------------------------------------------
        # A. Centralized Neural Baseline (Budget: rounds * local_epochs = 10)
        # -----------------------------------------------------------------
        total_central_epochs = rounds * local_epochs
        logger.info("[Seed %d] Training Centralized Baseline for %d epochs...", seed, total_central_epochs)
        torch.manual_seed(seed)
        np.random.seed(seed)

        central_model = IEEECISNeuralClassifier(input_dim).to(device)
        central_opt = torch.optim.Adam(central_model.parameters(), lr=LEARNING_RATE)
        criterion = nn.BCEWithLogitsLoss()

        t0_cent = time.perf_counter()
        central_steps = 0
        for ep in range(total_central_epochs):
            loss, steps = train_single_epoch(central_model, pooled_loader, central_opt, criterion, device)
            central_steps += steps
            logger.info("  [Centralized Seed %d] Epoch %02d/%02d - Loss: %.4f", seed, ep + 1, total_central_epochs, loss)
        t_cent_dur = time.perf_counter() - t0_cent

        central_metrics = evaluate_model(central_model, test_loader, device)
        logger.info("  [Centralized Seed %d] PR-AUC: %.4f | ROC-AUC: %.4f | Rec@0.1%%FPR: %.4f | Time: %.1fs",
                    seed, central_metrics["pr_auc"], central_metrics["roc_auc"], central_metrics["recall_at_01_fpr"], t_cent_dur)

        # -----------------------------------------------------------------
        # B. Federated FedAvg (3 clients, 5 rounds, 2 local epochs)
        # -----------------------------------------------------------------
        logger.info("[Seed %d] Training Federated FedAvg (%d rounds x %d local epochs)...", seed, rounds, local_epochs)
        torch.manual_seed(seed)
        np.random.seed(seed)

        global_model = IEEECISNeuralClassifier(input_dim).to(device)
        t0_fl = time.perf_counter()
        federated_steps = 0

        for rnd in range(rounds):
            local_weights = []
            sample_counts = []
            round_steps = 0
            for c in CLIENT_NAMES:
                local_m = IEEECISNeuralClassifier(input_dim).to(device)
                local_m.load_state_dict(global_model.state_dict())
                opt = torch.optim.Adam(local_m.parameters(), lr=LEARNING_RATE)
                for _ in range(local_epochs):
                    _, steps = train_single_epoch(local_m, client_loaders[c], opt, criterion, device)
                    round_steps += steps
                local_weights.append(local_m.state_dict())
                sample_counts.append(len(client_dict[c][1]))

            federated_steps += round_steps
            total_n = sum(sample_counts)
            avg_dict = {}
            for k in global_model.state_dict():
                avg_dict[k] = sum(
                    local_weights[i][k] * (sample_counts[i] / total_n)
                    for i in range(NUM_CLIENTS)
                )
            global_model.load_state_dict(avg_dict)
            logger.info("  [FedAvg Seed %d] Completed Round %02d/%02d (Round Steps: %d)", seed, rnd + 1, rounds, round_steps)

        t_fl_dur = time.perf_counter() - t0_fl
        fedavg_metrics = evaluate_model(global_model, test_loader, device)
        delta_pr_auc = fedavg_metrics["pr_auc"] - central_metrics["pr_auc"]

        logger.info("  [FedAvg Seed %d] PR-AUC: %.4f | ROC-AUC: %.4f | Rec@0.1%%FPR: %.4f | Delta: %+.4f | Time: %.1fs",
                    seed, fedavg_metrics["pr_auc"], fedavg_metrics["roc_auc"], fedavg_metrics["recall_at_01_fpr"], delta_pr_auc, t_fl_dur)

        seed_results.append({
            "seed": seed,
            "centralized": central_metrics,
            "fedavg": fedavg_metrics,
            "delta_pr_auc": round(delta_pr_auc, 6),
            "budget": {
                "centralized_steps": central_steps,
                "federated_total_local_steps": federated_steps,
                "step_difference": federated_steps - central_steps,
                "relative_difference_pct": round(abs(federated_steps - central_steps) / max(1, central_steps) * 100.0, 2),
                "centralized_duration_seconds": round(t_cent_dur, 2),
                "federated_duration_seconds": round(t_fl_dur, 2),
            },
        })

    # 3. Compute aggregate statistics (ddof=1 sample standard deviation)
    cent_praucs = [r["centralized"]["pr_auc"] for r in seed_results]
    fed_praucs = [r["fedavg"]["pr_auc"] for r in seed_results]
    delta_praucs = [r["delta_pr_auc"] for r in seed_results]
    cent_rocaucs = [r["centralized"]["roc_auc"] for r in seed_results]
    fed_rocaucs = [r["fedavg"]["roc_auc"] for r in seed_results]
    cent_rec01 = [r["centralized"]["recall_at_01_fpr"] for r in seed_results]
    fed_rec01 = [r["fedavg"]["recall_at_01_fpr"] for r in seed_results]

    def _stats(vals: list[float]) -> dict[str, float]:
        a = np.array(vals)
        return {
            "mean": round(float(a.mean()), 6),
            "std": round(float(np.std(a, ddof=1)), 6),
            "min": round(float(a.min()), 6),
            "max": round(float(a.max()), 6),
        }

    aggregate = {
        "centralized_pr_auc": _stats(cent_praucs),
        "fedavg_pr_auc": _stats(fed_praucs),
        "delta_pr_auc": _stats(delta_praucs),
        "centralized_roc_auc": _stats(cent_rocaucs),
        "fedavg_roc_auc": _stats(fed_rocaucs),
        "centralized_recall_at_01_fpr": _stats(cent_rec01),
        "fedavg_recall_at_01_fpr": _stats(fed_rec01),
        "std_definition": "sample_standard_deviation_ddof_1",
    }

    # Consolidated operational points (averaged across seeds)
    operational_points_agg = []
    for tfpr in [0.0001, 0.0005, 0.001, 0.005, 0.010]:
        c_recs = [next(p["recall"] for p in r["centralized"]["operating_points"] if abs(p["target_fpr"] - tfpr) < 1e-7) for r in seed_results]
        f_recs = [next(p["recall"] for p in r["fedavg"]["operating_points"] if abs(p["target_fpr"] - tfpr) < 1e-7) for r in seed_results]
        f_precs = [next(p["precision"] for p in r["fedavg"]["operating_points"] if abs(p["target_fpr"] - tfpr) < 1e-7) for r in seed_results]
        f_alerts = [next(p["alerts"] for p in r["fedavg"]["operating_points"] if abs(p["target_fpr"] - tfpr) < 1e-7) for r in seed_results]
        f_thresh = [next(p["threshold"] for p in r["fedavg"]["operating_points"] if abs(p["target_fpr"] - tfpr) < 1e-7) for r in seed_results]

        operational_points_agg.append({
            "target_fpr": tfpr,
            "fedavg_recall_mean": round(float(np.mean(f_recs)), 6),
            "fedavg_recall_std": round(float(np.std(f_recs, ddof=1)), 6),
            "centralized_recall_mean": round(float(np.mean(c_recs)), 6),
            "centralized_recall_std": round(float(np.std(c_recs, ddof=1)), 6),
            "fedavg_precision_mean": round(float(np.mean(f_precs)), 6),
            "fedavg_mean_alerts": round(float(np.mean(f_alerts)), 1),
            "mean_threshold": round(float(np.mean(f_thresh)), 6),
            "positive_denominator": test_fraud,
            "negative_denominator": test_n - test_fraud,
            "calibration_source": "test_set_diagnostic_operating_point",
        })

    # Assemble canonical Level 1 artifact
    total_central_steps = sum(r["budget"]["centralized_steps"] for r in seed_results) // len(seeds)
    total_fl_steps = sum(r["budget"]["federated_total_local_steps"] for r in seed_results) // len(seeds)

    artifact: dict[str, Any] = {
        "schema_version": "2.2.0",
        "benchmark_id": "ieee_cis_canonical",
        "status": "CANONICAL",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": git_sha,
        "dataset": {
            "name": "IEEE-CIS Fraud Detection",
            "dataset_type": "real",
            "provenance_type": "REAL_DATA",
            "evidence_scope": "REAL_WORLD_BENCHMARK",
            "communication_eligibility": "SAFE_WITH_CAVEAT",
            "transaction_csv_path": str(trans_path),
            "identity_csv_path": str(id_path),
            "transaction_sha256": trans_sha256,
            "identity_sha256": id_sha256,
            "sha256": trans_sha256,
            "total_transactions": 590540,
            "total_fraud": 20663,
            "fraud_prevalence": round(20663 / 590540, 6),
            "feature_dim": input_dim,
            "feature_names_digest": hashlib.sha256(",".join(partitioner.feature_names).encode()).hexdigest(),
        },
        "split": {
            "strategy": "strict_chronological_temporal_split",
            "time_axis": "TransactionDT",
            "test_ratio": TEST_RATIO,
            "train_samples": train_n,
            "test_samples": test_n,
            "train_fraud_count": train_fraud,
            "test_fraud_count": test_fraud,
            "train_prevalence": round(train_prev, 6),
            "test_prevalence": round(test_prev, 6),
            "train_dt_range": [train_min_dt, train_max_dt],
            "test_dt_range": [test_min_dt, test_max_dt],
            "temporal_cutoff_dt": partitioner.temporal_cutoff_dt,
            "temporal_leakage_prevented": True,
            "transaction_dt_excluded_from_x": True,
        },
        "preprocessing": {
            "strategy": "train_only_standard_scaling",
            "imputation": "zero_filled_numeric",
            "categorical_encoding": "one_hot_low_cardinality_and_binary_flags",
            "standardizer_fit_scope": "train_partition_only",
            "zero_test_lookahead": True,
        },
        "partition": {
            "algorithm": "dirichlet",
            "alpha": DIRICHLET_ALPHA,
            "n_clients": NUM_CLIENTS,
            "client_names": CLIENT_NAMES,
            "client_diagnostics_by_seed": partition_diagnostics_by_seed,
        },
        "budget": {
            "equalized": True,
            "num_rounds": rounds,
            "local_epochs": local_epochs,
            "centralized_epochs": rounds * local_epochs,
            "batch_size": BATCH_SIZE,
            "learning_rate": LEARNING_RATE,
            "optimizer": "Adam",
            "loss_function": "BCEWithLogitsLoss",
            "mean_centralized_steps": total_central_steps,
            "mean_federated_steps": total_fl_steps,
        },
        "seeds": seeds,
        "per_seed_results": seed_results,
        "aggregate": aggregate,
        "aggregate_metrics": aggregate,
        "operational_metrics": operational_points_agg,
        "limitations": [
            "Evaluates simulated 3-bank federation derived from a single public competition dataset (Kaggle/Vesta), not live multi-bank deployments.",
            "Evaluated across 3 predefined random seeds [42, 123, 456]; further seeds may reveal wider variance.",
            "Out-of-time temporal holdout reflects real-world concept drift but is subject to IEEE-CIS specific temporal discontinuities.",
            "Evaluates standard FedAvg optimization; adaptive aggregators or personalization may alter collaborative dynamics.",
        ],
        "historical_context": {
            "note": "Historical synthetic smoke test produced ~0.7811 / ~0.7554 metrics from an uncalibrated 10-feature generator. Those values are strictly superseded by this Level 1 real dataset execution.",
            "engineering_targets": {
                "centralized_target_pr_auc": 0.8120,
                "operational_target_recall_at_01_fpr": 0.5890,
            },
        },
    }

    # Save artifact
    out_dir = REPO_ROOT / "experiments" / "ieee_cis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "canonical_results.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)
    logger.info("Successfully wrote canonical benchmark artifact to %s", out_file)

    # Validate against Pydantic schema
    logger.info("Validating artifact through Pydantic CanonicalBenchmarkArtifact schema...")
    dataset_info = artifact["dataset"]
    prov_model = DatasetProvenance(
        dataset_name=str(dataset_info["name"]),
        dataset_type=DatasetProvenanceType.REAL_DATA,
        source_uri=str(dataset_info["transaction_csv_path"]),
        sha256=str(dataset_info["sha256"]),
        total_samples=int(dataset_info["total_transactions"]),
        fraud_samples=int(dataset_info["total_fraud"]),
        fraud_prevalence_pct=float(dataset_info["fraud_prevalence"] * 100),
    )
    budget_model = ExecutionBudget(
        budget_equalized=True,
        centralized_epochs=rounds * local_epochs,
        centralized_optimizer_steps=total_central_steps,
        fl_rounds=rounds,
        fl_local_epochs=local_epochs,
        fl_total_client_steps=total_fl_steps,
        batch_size=BATCH_SIZE,
        learning_rate=LEARNING_RATE,
    )
    limitations_list: list[str] = list(artifact["limitations"])
    pydantic_artifact = CanonicalBenchmarkArtifact(
        benchmark_id="ieee_cis_canonical",
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        provenance=prov_model,
        budget=budget_model,
        seeds=seeds,
        per_seed_results=seed_results,
        aggregate_metrics=aggregate,
        limitations=limitations_list,
        git_commit=git_sha,
    )
    logger.info("Pydantic schema validation PASSED for CanonicalBenchmarkArtifact (id=%s)!", pydantic_artifact.benchmark_id)

    # Print publication summary
    logger.info("=" * 80)
    logger.info("  CANONICAL IEEE-CIS BENCHMARK RESULTS (LEVEL 1 EMPIRICAL EVIDENCE)")
    logger.info("=" * 80)
    logger.info("  Centralized PR-AUC : %.4f +/- %.4f (min=%.4f, max=%.4f)",
                aggregate["centralized_pr_auc"]["mean"], aggregate["centralized_pr_auc"]["std"],
                aggregate["centralized_pr_auc"]["min"], aggregate["centralized_pr_auc"]["max"])
    logger.info("  FedAvg PR-AUC      : %.4f +/- %.4f (min=%.4f, max=%.4f)",
                aggregate["fedavg_pr_auc"]["mean"], aggregate["fedavg_pr_auc"]["std"],
                aggregate["fedavg_pr_auc"]["min"], aggregate["fedavg_pr_auc"]["max"])
    logger.info("  Delta PR-AUC       : %+.4f +/- %.4f",
                aggregate["delta_pr_auc"]["mean"], aggregate["delta_pr_auc"]["std"])
    logger.info("  Centralized ROC-AUC: %.4f +/- %.4f",
                aggregate["centralized_roc_auc"]["mean"], aggregate["centralized_roc_auc"]["std"])
    logger.info("  FedAvg ROC-AUC     : %.4f +/- %.4f",
                aggregate["fedavg_roc_auc"]["mean"], aggregate["fedavg_roc_auc"]["std"])
    logger.info("  FedAvg Rec@0.1%%FPR: %.4f +/- %.4f (N_fraud=%d, N_legit=%d)",
                aggregate["fedavg_recall_at_01_fpr"]["mean"], aggregate["fedavg_recall_at_01_fpr"]["std"],
                test_fraud, test_n - test_fraud)
    logger.info("=" * 80)

    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="IEEE-CIS Canonical Benchmark Runner")
    parser.add_argument("--dataset-mode", choices=["real", "synthetic"], default="real", help="Dataset mode (default: real)")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="Communication rounds")
    parser.add_argument("--local-epochs", type=int, default=DEFAULT_LOCAL_EPOCHS, help="Local epochs per round")
    parser.add_argument("--execute-full-590k", action="store_true", default=True, help="Authorize full 590k multi-seed training on real Kaggle data")
    args = parser.parse_args()

    run_canonical_ieee_cis_benchmark(
        dataset_mode=args.dataset_mode,
        rounds=args.rounds,
        local_epochs=args.local_epochs,
        execute_full_590k=args.execute_full_590k,
    )


if __name__ == "__main__":
    main()
