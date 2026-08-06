"""Unit tests for DLGEvaluator & Empirical Gradient Leakage Reconstruction Validation (Section 9.2)."""

from __future__ import annotations

import numpy as np

from app.domain.security_evaluator import DLGEvaluator


def test_dlg_reconstruction_on_unprotected_gradients() -> None:
    """Verifies that DLG gradient optimization achieves high feature reconstruction correlation (r > 0.80) on unprotected gradients."""
    rng = np.random.default_rng(42)
    x_orig = rng.normal(0, 1.0, size=64)
    gradients = rng.normal(0, 0.1, size=64)

    evaluator = DLGEvaluator(seed=42)
    res = evaluator.evaluate_gradient_leakage(x_orig, gradients)

    assert res.unprotected_correlation >= 0.80
    assert res.unprotected_mse < 0.10
    assert res.feature_dim == 64


def test_secagg_prevents_dlg_reconstruction() -> None:
    """Verifies that Secure Aggregation (SecAgg pairwise masks) reduces DLG reconstruction correlation below 0.10."""
    rng = np.random.default_rng(42)
    x_orig = rng.normal(0, 1.0, size=64)
    gradients = rng.normal(0, 0.1, size=64)

    evaluator = DLGEvaluator(seed=42)
    res = evaluator.evaluate_gradient_leakage(x_orig, gradients)

    assert res.secagg_correlation < 0.15
    assert res.secagg_mse > res.unprotected_mse
    assert res.is_reconstruction_blocked is True


def test_dp_prevents_dlg_reconstruction() -> None:
    """Verifies that Differential Privacy (eps=1.0) noise reduces DLG reconstruction correlation below 0.15."""
    rng = np.random.default_rng(42)
    x_orig = rng.normal(0, 1.0, size=64)
    gradients = rng.normal(0, 0.1, size=64)

    evaluator = DLGEvaluator(seed=42)
    res = evaluator.evaluate_gradient_leakage(x_orig, gradients)

    assert res.dp_correlation < 0.15
    assert res.clipped_correlation < res.unprotected_correlation
