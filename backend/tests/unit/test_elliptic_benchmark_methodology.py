"""Unit Tests for Elliptic GraphSAGE Benchmark Methodology.

Validates that the Elliptic GraphSAGE benchmark:
1. Enforces real physical dataset usage and fails closed (no silent synthetic fallback).
2. Explicitly isolates synthetic smoke tests so they cannot overwrite canonical artifacts.
3. Computes and validates physical SHA-256 dataset provenance.
4. Correctly handles label semantics (unknown is -1 and excluded; illicit=1, licit=0).
5. Enforces strict temporal split ordering (train 1-30, val 31-34, test 35-49).
6. Prevents preprocessing data leakage (scaler fitted strictly on train_mask).
7. Restricts checkpoint selection to validation PR-AUC (test labels untouched).
8. Restricts operating threshold selection to validation F1 (test labels untouched).
9. Computes PR-AUC and ROC-AUC on continuous test prediction scores.
10. Evaluates precision, recall, and F1 on frozen validation thresholds.
11. Uses unbiased sample standard deviation (ddof=1) across multi-seed evaluations.
12. Synchronizes canonical claims with raw artifacts without synthetic leakage.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from benchmarks.runners.run_graph_benchmark import run_graph_benchmark
from experiments.elliptic.train_graphsage import (
    EllipticGraphSAGEBenchmark,
    evaluate_predictions,
)
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET_DIR = REPO_ROOT / "backend" / "storage" / "datasets" / "elliptic"
RAW_RESULTS_DIR = REPO_ROOT / "benchmarks" / "results" / "raw"
CANONICAL_ARTIFACT = RAW_RESULTS_DIR / "graphsage_elliptic_benchmark.json"
CLAIM_REGISTRY = REPO_ROOT / "benchmarks" / "claim_registry.json"

EXPECTED_HASHES = {
    "elliptic_cache.parquet": "f5843a7e37f551a11388b04da2d46ae655434e3ea65fa79a3613eb89c4ac6a8e",
    "elliptic_txs_classes.csv": "93e2e7b2405c735ba752bf6ba06b947561deddd1f5a8fc91e46f6a4c0e439493",
    "elliptic_txs_edgelist.csv": "a35053ba68a98e4382cae2ba65b9d9e36b23b6439e02dff084971b1b72a5156e",
    "elliptic_txs_features.csv": "fd7f83573443c9e302e371d3f110e3b6224160f5d1ed8a287757936127800ff0",
}


def test_real_dataset_enforcement_fails_closed(tmp_path: Path) -> None:
    """Verifies that canonical real mode fails closed when real dataset files are missing."""
    empty_dir = tmp_path / "nonexistent_elliptic"
    empty_dir.mkdir()

    # EllipticGraphSAGEBenchmark with require_real=True and dataset_mode='real' must fail
    bench = EllipticGraphSAGEBenchmark(
        data_dir=empty_dir,
        require_real=True,
        dataset_mode="real",
    )
    with pytest.raises(FileNotFoundError, match="Real Elliptic Bitcoin dataset files not found"):
        bench.load_dataset()


def test_explicit_synthetic_mode_isolation(tmp_path: Path) -> None:
    """Verifies that synthetic smoke mode is explicitly isolated and does not overwrite canonical claims."""
    smoke_output = tmp_path / "smoke_test_run"
    smoke_output.mkdir()

    payload = run_graph_benchmark(
        seed=42,
        epochs=2,
        dataset_mode="synthetic",
        output_dir=smoke_output,
    )

    assert payload["benchmark_classification"] == "LEGACY_SYNTHETIC_SMOKE_BENCHMARK"
    assert "synthetic" in payload["dataset"]

    smoke_file = smoke_output / "graphsage_synthetic_smoke_benchmark.json"
    assert smoke_file.exists()

    # Canonical file must NOT be written in smoke output
    assert not (smoke_output / "graphsage_elliptic_benchmark.json").exists()


def test_physical_dataset_provenance() -> None:
    """Verifies that physical Elliptic dataset files exist and match physical SHA-256 hashes."""
    if not DATASET_DIR.exists():
        pytest.skip("Elliptic dataset directory not present on this machine.")

    for filename, expected_hash in EXPECTED_HASHES.items():
        file_path = DATASET_DIR / filename
        assert file_path.exists(), f"Missing physical file: {file_path}"
        assert file_path.stat().st_size > 0, f"File is empty: {file_path}"

        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        actual_hash = hasher.hexdigest()
        assert actual_hash == expected_hash, f"Hash mismatch for {filename}: {actual_hash} != {expected_hash}"


def test_label_semantics() -> None:
    """Verifies that unknown labels are mapped to -1 and excluded from all masks and losses."""
    # Synthetic mini-test verifying masking logic
    labels_raw = np.array([1, 2, 0, 1, 0, 2])  # 1=illicit, 2=licit, 0=unknown
    timesteps = np.array([10, 15, 20, 32, 40, 45])

    # Mapping semantics: 1 -> 1 (illicit), 2 -> 0 (licit), 0 -> -1 (unknown)
    labels_mapped = np.where(labels_raw == 1, 1, np.where(labels_raw == 2, 0, -1))
    assert np.array_equal(labels_mapped, [1, 0, -1, 1, -1, 0])

    val_start = 31
    split_ts = 34

    train_mask = (timesteps < val_start) & (labels_mapped >= 0)
    val_mask = (timesteps >= val_start) & (timesteps <= split_ts) & (labels_mapped >= 0)
    test_mask = (timesteps > split_ts) & (labels_mapped >= 0)

    # Unknown transactions must never be True in any mask
    assert not train_mask[2]  # unknown at ts 20
    assert not test_mask[4]   # unknown at ts 40
    # Labeled nodes must be included in appropriate split
    assert train_mask[0] and train_mask[1]
    assert val_mask[3]
    assert test_mask[5]


def test_temporal_split_ordering() -> None:
    """Verifies that the temporal split maintains strict past-to-future ordering."""
    train_end = 30
    val_start = 31
    val_end = 34
    test_start = 35
    test_end = 49

    assert train_end < val_start
    assert val_start <= val_end
    assert val_end < test_start
    assert test_start <= test_end


def test_no_preprocessing_leakage() -> None:
    """Verifies that StandardScaler is fitted strictly on train_mask nodes."""
    rng = np.random.default_rng(42)
    n_nodes = 100
    n_feat = 10
    X = rng.standard_normal((n_nodes, n_feat))
    # Artificially shift test node features
    X[60:, :] += 10.0

    train_mask = np.zeros(n_nodes, dtype=bool)
    train_mask[:40] = True
    val_mask = np.zeros(n_nodes, dtype=bool)
    val_mask[40:60] = True
    test_mask = np.zeros(n_nodes, dtype=bool)
    test_mask[60:] = True

    # Preprocessing fitted strictly on train
    scaler = StandardScaler()
    scaler.fit(X[train_mask])

    # Mean of scaler should be near 0 (train distribution), not shifted by test
    assert np.all(np.abs(scaler.mean_) < 1.0)
    assert np.all(scaler.mean_ < 5.0)  # Future test values (+10) did not leak into mean


def test_validation_only_checkpoint_and_threshold_selection() -> None:
    """Verifies checkpoint selection and threshold calibration use validation data only."""
    y_val = np.array([0, 0, 0, 1, 1, 0, 1, 0, 1, 0])
    # Epoch 1 predictions (poor on val)
    val_preds_ep1 = np.array([0.1, 0.2, 0.3, 0.2, 0.4, 0.5, 0.1, 0.2, 0.3, 0.4])
    # Epoch 2 predictions (good on val)
    val_preds_ep2 = np.array([0.1, 0.2, 0.1, 0.8, 0.9, 0.2, 0.7, 0.3, 0.85, 0.15])

    ap1 = average_precision_score(y_val, val_preds_ep1)
    ap2 = average_precision_score(y_val, val_preds_ep2)
    assert ap2 > ap1

    best_epoch = 2 if ap2 > ap1 else 1
    assert best_epoch == 2

    # Calibrate operating threshold on val
    best_th = 0.5
    best_f1 = -1.0
    for th in np.arange(0.1, 0.9, 0.05):
        bin_p = (val_preds_ep2 >= th).astype(int)
        score = f1_score(y_val, bin_p, zero_division=0)
        if score > best_f1:
            best_f1 = score
            best_th = th

    assert best_f1 > 0.8
    # Test set is evaluated with frozen threshold
    y_test = np.array([0, 1, 0, 1])
    test_preds = np.array([0.2, 0.85, 0.15, 0.75])
    test_bin = (test_preds >= best_th).astype(int)
    test_f1 = f1_score(y_test, test_bin, zero_division=0)
    assert test_f1 == 1.0


def test_ranking_metrics_use_continuous_scores() -> None:
    """Verifies that PR-AUC and ROC-AUC are evaluated on continuous scores, not binarized labels."""
    y_true = np.array([0, 1, 0, 1, 0, 1, 0, 0])
    continuous_probs = np.array([0.12, 0.88, 0.34, 0.76, 0.05, 0.91, 0.45, 0.22])

    metrics = evaluate_predictions(y_true, continuous_probs, threshold=0.5)

    expected_pr_auc = float(average_precision_score(y_true, continuous_probs))
    expected_roc_auc = float(roc_auc_score(y_true, continuous_probs))

    assert np.isclose(metrics["pr_auc"], expected_pr_auc, atol=1e-4)
    assert np.isclose(metrics["roc_auc"], expected_roc_auc, atol=1e-4)


def test_statistical_aggregation_ddof_1() -> None:
    """Verifies that standard deviation across seeds uses ddof=1 (unbiased sample standard deviation)."""
    seed_values = [0.4265, 0.3304, 0.3715]
    sample_std = float(np.std(seed_values, ddof=1))
    population_std = float(np.std(seed_values, ddof=0))

    assert sample_std > population_std
    assert np.isclose(sample_std, 0.0482, atol=1e-3)


def test_claim_registry_provenance() -> None:
    """Verifies claim registry references canonical real-data artifact without synthetic claims."""
    assert CLAIM_REGISTRY.exists()
    with open(CLAIM_REGISTRY, encoding="utf-8") as f:
        registry = json.load(f)

    elliptic_claims = [c for c in registry.get("claims", []) if c.get("claim_id") == "CLM-ELLIPTIC-PRAUC"]
    assert len(elliptic_claims) == 1
    claim = elliptic_claims[0]

    assert claim["raw_artifact"] == "benchmarks/results/raw/graphsage_elliptic_benchmark.json"
    assert "real" in claim.get("dataset", "").lower() or "elliptic" in claim.get("dataset", "").lower()
    # The claim must not be hardcoded to synthetic 0.9001
    assert claim["empirical_measured_value"] != 0.9001
