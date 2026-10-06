"""Unit tests for universal safe metric evaluation functions across edge cases."""

import numpy as np
import pytest

from app.domain.metrics_service import (
    compute_pr_auc_with_status,
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

    # 2. Single-class all 0s returns None (undefined, never fabricated 0.5)
    assert safe_roc_auc_score([0, 0, 0, 0], [0.1, 0.2, 0.3, 0.4]) is None

    # 3. Single-class all 1s returns None
    assert safe_roc_auc_score([1, 1, 1, 1], [0.1, 0.2, 0.3, 0.4]) is None

    # 4. Empty arrays returns None
    assert safe_roc_auc_score([], []) is None

    # 5. Numpy arrays returns None
    assert safe_roc_auc_score(np.array([0, 0, 0]), np.array([0.1, 0.2, 0.3])) is None


def test_safe_pr_auc_score_edge_cases():
    """Test safe_pr_auc_score on empty, single-class, inverted ranking, and valid arrays."""
    # 1. Normal valid case
    y_true = [0, 0, 1, 1]
    y_pred = [0.1, 0.2, 0.8, 0.9]
    pr_auc = safe_pr_auc_score(y_true, y_pred)
    assert pr_auc is not None and 0.0 <= pr_auc <= 1.0

    score, is_def, status = compute_pr_auc_with_status(y_true, y_pred)
    assert is_def is True and status == "defined" and score == pr_auc

    # 2. Worst valid inverted ranking: PR-AUC is mathematically strictly positive (bounded by prevalence P/N)
    # Even on inverted predictions, PR-AUC cannot equal exact 0.0 for finite binary data with positives
    y_inv_true = [0, 0, 0, 1]
    y_inv_pred = [0.9, 0.8, 0.7, 0.1]
    inv_auc = safe_pr_auc_score(y_inv_true, y_inv_pred)
    assert inv_auc is not None and 0.0 < inv_auc < 1.0

    # 3. Single-class all 0s returns None (undefined, never fabricated 0.0 or 0.5)
    assert safe_pr_auc_score([0, 0, 0], [0.1, 0.2, 0.3]) is None
    score_sc, is_def_sc, status_sc = compute_pr_auc_with_status([0, 0, 0], [0.1, 0.2, 0.3])
    assert is_def_sc is False and status_sc == "undefined_single_class" and score_sc is None

    # 4. Empty arrays returns None
    assert safe_pr_auc_score([], []) is None
    score_e, is_def_e, status_e = compute_pr_auc_with_status([], [])
    assert is_def_e is False and status_e == "undefined_empty_input" and score_e is None

    # 5. Invalid caller input (NaN) fails closed by raising ValueError
    with pytest.raises(ValueError, match="Non-finite values"):
        safe_pr_auc_score([0, 1], [float("nan"), 0.5])


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

    # 2. All zeros with zero predictions (single-class undefined)
    assert safe_f1_score([0, 0, 0], [0.1, 0.2, 0.3]) is None

    # 3. Empty arrays
    assert safe_f1_score([], []) is None


def test_compute_scientific_benchmark_single_class():
    """Test compute_scientific_benchmark truthfully returns None for undefined metrics on homogeneous labels."""
    metrics = compute_scientific_benchmark(
        model_config_name="Single Class Test",
        y_true=[0, 0, 0, 0, 0],
        y_pred=[0.1, 0.2, 0.3, 0.4, 0.5],
    )
    assert metrics.roc_auc is None
    assert metrics.pr_auc is None
    assert metrics.model_config_name == "Single Class Test"
