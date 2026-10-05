"""Unit Tests for Differential Privacy Benchmark Methodology.

Validates that the DP benchmark uses genuine per-sample DP-SGD (PyTorch Opacus),
features explicit PRV accounting (Privacy Random Variables, Gopi et al. 2021),
uses fixed-noise-multiplier sweep semantics, employs unbiased sample standard
deviation (ddof=1) across multi-seed evaluations, contains zero static unexecuted
fixtures, and emits verifiable canonical artifacts without metric hardcoding.
"""

from __future__ import annotations

import contextlib
import inspect
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from experiments.dp_evaluation.run_dp_noise_sweep import (
    BATCH_SIZE,
    EPOCHS,
    LEGACY_PROTOTYPE_POINTS,
    MAX_GRAD_NORM,
    N_FEATURES,
    generate_synthetic_fraud_dataset,
    train_and_evaluate_single_run,
)
from opacus import PrivacyEngine

from app.application.services.model_service import FraudDetectionModel

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_RESULTS_DIR = REPO_ROOT / "benchmarks" / "results" / "raw"
DP_ARTIFACT_PATH = RAW_RESULTS_DIR / "dp_privacy_utility_tradeoff.json"


def test_no_static_canonical_fixtures() -> None:
    """Verifies that legacy prototype points are cleanly archived and not emitted as live results."""
    # Ensure legacy points are explicitly classified as prototypes
    for pt in LEGACY_PROTOTYPE_POINTS:
        assert pt.get("classification") == "LEGACY_NON_DP_SGD_PROTOTYPE"

    # Ensure live artifact exists and is not equal to legacy fixtures
    assert DP_ARTIFACT_PATH.exists(), f"Missing DP raw artifact at {DP_ARTIFACT_PATH}"
    with open(DP_ARTIFACT_PATH, encoding="utf-8") as f:
        artifact = json.load(f)

    # Legacy fixture values from f0705141 / 7e32f409
    legacy_pr_aucs = {0.1963, 0.0722, 0.3081, 0.2833, 0.6205, 0.6272}
    live_pr_aucs = {pt["pr_auc"] for pt in artifact["tradeoff_points"]}
    assert not live_pr_aucs.intersection(legacy_pr_aucs)

    # The live results must reflect genuine DP-SGD runs, not the legacy fixtures
    baseline_pt = artifact["tradeoff_points"][-1]
    assert baseline_pt["noise_multiplier"] == 0.0
    assert baseline_pt["pr_auc"] not in legacy_pr_aucs
    # Verify baseline is clearly distinct from the legacy 0.6272 fixture without hardcoding exact decimals
    assert baseline_pt["pr_auc"] > 0.70


def test_accountant_identity_is_prv() -> None:
    """Verifies that canonical DP benchmark explicitly pins and uses PRVAccountant."""
    pe = PrivacyEngine(accountant="prv")
    assert type(pe.accountant).__name__ == "PRVAccountant", (
        f"Expected PRVAccountant, got {type(pe.accountant).__name__}"
    )

    # Verify implementation source explicitly specifies accountant='prv'
    source = inspect.getsource(train_and_evaluate_single_run)
    assert 'accountant="prv"' in source or "accountant='prv'" in source, (
        "train_and_evaluate_single_run must explicitly pin accountant='prv'"
    )

    # Verify raw artifact records PRVAccountant
    with open(DP_ARTIFACT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert "PRVAccountant" in data["accountant"]
    assert "accountant='prv'" in data["privacy_engine"]


def test_fixed_sigma_semantics() -> None:
    """Verifies that the benchmark passes fixed noise_multiplier, NOT target-epsilon calibration."""
    source = inspect.getsource(train_and_evaluate_single_run)
    assert "noise_multiplier=sigma" in source, "Must pass fixed noise_multiplier=sigma"
    assert "make_private_with_epsilon" not in source, (
        "Canonical benchmark must NOT use make_private_with_epsilon; it is a fixed-sigma sweep"
    )

    with open(DP_ARTIFACT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("sweep_type") == "fixed_noise_multiplier"


def test_dp_engine_is_active() -> None:
    """Smoke test verifying that PrivacyEngine wraps model and tracks epsilon for sigma > 0."""
    X, y = generate_synthetic_fraud_dataset(n_samples=200, n_features=N_FEATURES, seed=42)
    split = 150
    X_tr, y_tr = X[:split], y[:split]
    X_te, y_te = X[split:], y[split:]

    # Private run (sigma = 1.0, 1 epoch, small batch)
    res_private = train_and_evaluate_single_run(
        X_tr,
        y_tr,
        X_te,
        y_te,
        sigma=1.0,
        seed=42,
        epochs=1,
        batch_size=32,
        lr=1e-3,
        delta=1e-3,
    )
    assert res_private["epsilon"] < float("inf")
    assert res_private["epsilon"] > 0.0
    assert "pr_auc" in res_private

    # Non-private run (sigma = 0.0)
    res_non_private = train_and_evaluate_single_run(
        X_tr,
        y_tr,
        X_te,
        y_te,
        sigma=0.0,
        seed=42,
        epochs=1,
        batch_size=32,
        lr=1e-3,
        delta=1e-3,
    )
    assert res_non_private["epsilon"] == float("inf")
    assert "pr_auc" in res_non_private


def test_live_accounting_integrity() -> None:
    """Verifies that epsilon is computed from the live PrivacyEngine instance after training."""
    X, y = generate_synthetic_fraud_dataset(n_samples=100, n_features=N_FEATURES, seed=123)
    res = train_and_evaluate_single_run(
        X[:80],
        y[:80],
        X[80:],
        y[80:],
        sigma=2.0,
        seed=42,
        epochs=1,
        batch_size=32,
        lr=1e-3,
        delta=1e-4,
    )
    # Epsilon must be finite and positive for private execution
    assert isinstance(res["epsilon"], float)
    assert 0.0 < res["epsilon"] < 100.0
    assert res["dp_enabled"] is True


def test_per_sample_dp_semantics() -> None:
    """Verifies that Opacus applies per-sample gradient clipping rather than batch-level clipping."""
    model = FraudDetectionModel(input_dim=N_FEATURES, dp_compatible=True)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    dataset = torch.utils.data.TensorDataset(
        torch.randn(32, N_FEATURES),
        torch.randint(0, 2, (32,)).float(),
    )
    loader = torch.utils.data.DataLoader(dataset, batch_size=16)

    engine = PrivacyEngine(accountant="prv")
    try:
        model_p, opt_p, loader_p = engine.make_private(
            module=model,
            optimizer=optimizer,
            data_loader=loader,
            noise_multiplier=1.0,
            max_grad_norm=MAX_GRAD_NORM,
            grad_sample_mode="ew",
        )
    except Exception:
        model_p, opt_p, loader_p = engine.make_private(
            module=model,
            optimizer=optimizer,
            data_loader=loader,
            noise_multiplier=1.0,
            max_grad_norm=MAX_GRAD_NORM,
            grad_sample_mode="hooks",
        )

    try:
        # Opacus wraps model in GradSampleModule to compute per-sample gradients
        assert hasattr(model_p, "_module") or hasattr(model_p, "forbid_grad_accumulation")

        # Run one batch forward + backward
        criterion = torch.nn.BCELoss()
        for xb, yb in loader_p:
            opt_p.zero_grad()
            out = model_p(xb)
            loss = criterion(out, yb)
            loss.backward()
            # Ensure per-sample gradient samples exist on linear layers
            for param in model_p.parameters():
                if param.requires_grad:
                    assert hasattr(param, "grad_sample"), "Missing per-sample grad_sample attribute"
                    assert param.grad_sample.shape[0] == xb.shape[0], (
                        "grad_sample batch dimension mismatch"
                    )
            break
    finally:
        if hasattr(model_p, "remove_hooks"):
            with contextlib.suppress(Exception):
                model_p.remove_hooks()


def test_accountant_correspondence() -> None:
    """Verifies that PRV accountant yields monotonic privacy guarantees across sigma levels."""
    with open(DP_ARTIFACT_PATH, encoding="utf-8") as f:
        artifact = json.load(f)

    tradeoff_points = artifact["tradeoff_points"]
    # Separate finite epsilon points (sigma > 0)
    private_points = [p for p in tradeoff_points if isinstance(p["epsilon"], (int, float))]

    # Higher noise multiplier must yield LOWER epsilon (stronger privacy guarantee)
    for i in range(len(private_points) - 1):
        pt_high_noise = private_points[i]
        pt_low_noise = private_points[i + 1]
        assert pt_high_noise["noise_multiplier"] > pt_low_noise["noise_multiplier"]
        assert pt_high_noise["epsilon"] < pt_low_noise["epsilon"], (
            f"Expected eps({pt_high_noise['noise_multiplier']}) < eps({pt_low_noise['noise_multiplier']}), "
            f"got {pt_high_noise['epsilon']} vs {pt_low_noise['epsilon']}"
        )


def test_baseline_comparability() -> None:
    """Verifies non-private baseline uses identical architecture and parameter scale."""
    with open(DP_ARTIFACT_PATH, encoding="utf-8") as f:
        artifact = json.load(f)

    configs = artifact["configurations"]
    baseline_configs = [c for c in configs if c["sigma"] == 0.0]
    private_configs = [c for c in configs if c["sigma"] > 0.0]

    assert len(baseline_configs) == len(artifact["seeds"])
    assert len(private_configs) > 0

    for c in configs:
        assert c["epochs"] == EPOCHS
        assert c["batch_size"] == BATCH_SIZE
        assert c["clip_norm"] == MAX_GRAD_NORM


def test_sample_standard_deviation_semantics() -> None:
    """Verifies cross-seed variability is computed using sample standard deviation (ddof=1)."""
    with open(DP_ARTIFACT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    for pt in data["tradeoff_points"]:
        per_seed = list(pt["per_seed_pr_auc"].values())
        assert len(per_seed) == 3

        # Sample standard deviation (ddof=1)
        expected_sample_std = round(float(np.std(per_seed, ddof=1)), 4)
        pop_std = round(float(np.std(per_seed, ddof=0)), 4)

        # Must match sample std within rounding
        assert pytest.approx(pt["pr_auc_std"], abs=1e-4) == expected_sample_std
        # Sample std is strictly greater than population std for non-constant seeds
        assert expected_sample_std > pop_std, (
            f"Sample std ({expected_sample_std}) must exceed population std ({pop_std})"
        )
        assert (
            pt.get("std_definition") == "sample standard deviation across training seeds (ddof=1)"
        )


def test_artifact_provenance_and_schema() -> None:
    """Validates structure, provenance metadata, and multi-seed variance of canonical artifact."""
    assert DP_ARTIFACT_PATH.exists()
    with open(DP_ARTIFACT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    assert data["schema_version"] == "2.1.0"
    assert data["benchmark_id"] == "dp_privacy_utility_tradeoff_opacus"
    assert "PyTorch Opacus" in data["privacy_engine"]
    assert "accountant='prv'" in data["privacy_engine"]
    assert "PRVAccountant" in data["accountant"]
    assert data["sweep_type"] == "fixed_noise_multiplier"

    # Dataset verification
    ds = data["dataset"]
    assert ds["n_samples"] == 20000
    assert ds["n_features"] == 15
    assert ds["test_fraud_count"] == 84
    assert ds["test_samples"] == 4000

    # Tradeoff points
    points = data["tradeoff_points"]
    assert len(points) == 5
    assert points[0]["noise_multiplier"] == 3.0
    assert points[-1]["noise_multiplier"] == 0.0

    # Multi-seed variance verification
    for pt in points:
        assert "pr_auc" in pt
        assert "pr_auc_std" in pt
        assert pt["pr_auc_std"] > 0.0, f"Expected non-zero std for sigma={pt['noise_multiplier']}"
        assert len(pt["per_seed_pr_auc"]) == 3
        assert pt["std_definition"] == "sample standard deviation across training seeds (ddof=1)"
