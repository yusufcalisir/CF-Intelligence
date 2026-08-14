"""Unit tests for Multi-threshold Confusion Matrix, Precision-Recall, Alert Fatigue and Cost Utility modeling."""

import numpy as np

from app.domain.metrics_service import (
    compute_financial_cost_utility,
    compute_multi_threshold_confusion_matrix,
    compute_scientific_benchmark,
)


def test_multi_threshold_confusion_matrix():
    y_true = np.array([1, 1, 0, 0, 0, 0, 1, 0])
    y_pred = np.array([0.9, 0.8, 0.1, 0.2, 0.4, 0.3, 0.7, 0.6])

    matrices = compute_multi_threshold_confusion_matrix(y_true, y_pred, thresholds=[0.5])
    assert len(matrices) == 1
    cm = matrices[0]
    assert cm.threshold == 0.5
    assert cm.true_positives == 3
    assert cm.false_positives == 1
    assert cm.true_negatives == 4
    assert cm.false_negatives == 0
    assert cm.recall == 1.0
    assert cm.precision == 0.75


def test_financial_cost_utility_report():
    y_true = np.array([1] * 20 + [0] * 980)
    y_pred = np.random.default_rng(42).random(1000)

    report = compute_financial_cost_utility(
        y_true=y_true,
        y_pred=y_pred,
        operational_threshold=0.5,
        daily_volume=50_000,
    )
    assert report.daily_transactions == 50_000
    assert report.total_daily_cost_dollars > 0.0
    assert 0.0 <= report.optimal_cost_threshold <= 1.0
    assert report.minimized_total_cost_dollars <= report.total_daily_cost_dollars + 1e-4


def test_scientific_benchmark_summary():
    y_true = np.array([1] * 10 + [0] * 90)
    y_pred = np.array([0.9] * 10 + [0.1] * 90)

    metrics = compute_scientific_benchmark(
        model_config_name="Test Model",
        y_true=y_true,
        y_pred=y_pred,
        top_k=10,
    )
    assert metrics.model_config_name == "Test Model"
    assert metrics.roc_auc == 1.0
    assert metrics.pr_auc == 1.0
    assert metrics.precision_at_k == 1.0
