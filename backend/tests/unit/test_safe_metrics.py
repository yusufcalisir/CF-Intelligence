"""Unit tests for universal safe metric evaluation functions across edge cases."""

import numpy as np

from app.domain.metrics_service import (
    compute_scientific_benchmark,
    safe_f1_score,
    safe_pr_auc_score,
    safe_precision_recall_curve,
    safe_roc_auc_score,
)


def test_safe_roc_auc_score_edge_cases():
    """Test safe_roc_auc_score on empty, single-class, and valid arrays."""
    # 1. Normal valid case
    y_true = [0, 0, 1, 1]
    y_pred = [0.1, 0.2, 0.8, 0.9]
    assert safe_roc_auc_score(y_true, y_pred) == 1.0

    # 2. Single-class all 0s
    assert safe_roc_auc_score([0, 0, 0, 0], [0.1, 0.2, 0.3, 0.4]) == 0.5

    # 3. Single-class all 1s
    assert safe_roc_auc_score([1, 1, 1, 1], [0.1, 0.2, 0.3, 0.4], default=0.7) == 0.7

    # 4. Empty arrays
    assert safe_roc_auc_score([], []) == 0.5

    # 5. Numpy arrays
    assert safe_roc_auc_score(np.array([0, 0, 0]), np.array([0.1, 0.2, 0.3])) == 0.5


def test_safe_pr_auc_score_edge_cases():
    """Test safe_pr_auc_score on empty, single-class, and valid arrays."""
    # 1. Normal valid case
    y_true = [0, 0, 1, 1]
    y_pred = [0.1, 0.2, 0.8, 0.9]
    pr_auc = safe_pr_auc_score(y_true, y_pred)
    assert 0.0 <= pr_auc <= 1.0

    # 2. Single-class all 0s
    assert safe_pr_auc_score([0, 0, 0], [0.1, 0.2, 0.3]) == 0.5

    # 3. Empty arrays
    assert safe_pr_auc_score([], []) == 0.5


def test_safe_precision_recall_curve_edge_cases():
    """Test safe_precision_recall_curve on single-class and empty inputs."""
    # 1. Single-class all 0s
    prec, rec, thresh = safe_precision_recall_curve([0, 0, 0], [0.1, 0.2, 0.3])
    assert len(prec) > 0
    assert len(rec) > 0

    # 2. Empty arrays
    prec, rec, thresh = safe_precision_recall_curve([], [])
    assert len(prec) > 0
    assert len(rec) > 0

    # 3. Normal valid case
    prec, rec, thresh = safe_precision_recall_curve([0, 1, 0, 1], [0.1, 0.8, 0.2, 0.9])
    assert len(prec) > 0
    assert len(rec) > 0


def test_safe_f1_score_edge_cases():
    """Test safe_f1_score on edge cases."""
    # 1. Normal case
    assert safe_f1_score([0, 1, 0, 1], [0.1, 0.8, 0.2, 0.9]) == 1.0

    # 2. All zeros with zero predictions (no true or false positives)
    assert safe_f1_score([0, 0, 0], [0.1, 0.2, 0.3]) == 0.0

    # 3. Empty arrays
    assert safe_f1_score([], []) == 0.0


def test_compute_scientific_benchmark_single_class():
    """Test compute_scientific_benchmark does not raise error on homogeneous labels."""
    metrics = compute_scientific_benchmark(
        model_config_name="Single Class Test",
        y_true=[0, 0, 0, 0, 0],
        y_pred=[0.1, 0.2, 0.3, 0.4, 0.5],
    )
    assert metrics.roc_auc == 0.5
    assert metrics.pr_auc == 0.5
    assert metrics.model_config_name == "Single Class Test"
