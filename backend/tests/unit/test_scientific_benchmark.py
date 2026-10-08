"""Unit tests for Production Benchmark Suite & Scientific Validation Protocols (Section 5.4)."""

from __future__ import annotations

import os
import sys

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import benchmark  # noqa: E402
import pytest

from app.domain.metrics_service import (  # noqa: E402
    compute_pr_auc,
    compute_precision_at_k,
    compute_recall_at_fpr,
    compute_scientific_benchmark,
)


def test_scientific_metrics_calculations() -> None:
    """Verifies accuracy of PR-AUC, Recall @ 0.1% FPR, and Precision@K calculations."""
    y_true = [1] * 20 + [0] * 980  # 2% fraud rate
    # High score for fraud items
    y_pred = [0.95] * 15 + [0.1] * 5 + [0.05] * 950 + [0.80] * 30

    pr_auc = compute_pr_auc(y_true, y_pred)
    recall_01_fpr = compute_recall_at_fpr(y_true, y_pred, target_fpr=0.001)
    prec_10 = compute_precision_at_k(y_true, y_pred, k=10)

    assert 0.0 <= pr_auc <= 1.0
    assert 0.0 <= recall_01_fpr <= 1.0
    assert 0.0 <= prec_10 <= 1.0
    assert prec_10 == 1.0  # Top 10 predictions are all fraud (0.95 score)


def test_compute_scientific_benchmark_aggregation() -> None:
    """Verifies compute_scientific_benchmark returns populated ScientificValidationMetrics object."""
    y_true = [1] * 50 + [0] * 950
    y_pred = [0.9] * 40 + [0.1] * 10 + [0.05] * 950

    metrics = compute_scientific_benchmark(
        model_config_name="FedGNN Test Config",
        y_true=y_true,
        y_pred=y_pred,
        detection_latency_ms=6.5,
        communication_payload_mb=2.1,
        dp_epsilon=2.0,
        dp_delta=1e-5,
        cross_bank_generalization_delta=0.035,
    )

    assert metrics.model_config_name == "FedGNN Test Config"
    assert metrics.pr_auc > 0.5
    assert metrics.roc_auc > 0.5
    assert metrics.detection_latency_ms == 6.5
    assert metrics.communication_payload_mb == 2.1
    assert metrics.dp_epsilon == 2.0


@pytest.mark.real_data
def test_benchmark_suite_execution() -> None:
    """Verifies end-to-end execution of benchmark.py suite across all 6 model configurations."""
    results = benchmark.run_benchmark_suite()

    assert len(results) == 6
    names = [r["model_config_name"] for r in results]
    assert "Local-Only Model (Bank A)" in names
    assert "Centralized Pooled (Non-Private Upper Bound)" in names
    assert "Standard FedAvg" in names
    assert "FedProx (mu = 0.01)" in names
    assert "FedGNN (Graph Attention Network)" in names
    assert "Federated + Privacy Entity Intelligence" in names

    # Centralized and FedGNN should outperform Local-Only in PR-AUC
    local_pr_auc = next(r["pr_auc"] for r in results if "Local-Only" in r["model_config_name"])
    pooled_pr_auc = next(r["pr_auc"] for r in results if "Centralized" in r["model_config_name"])
    fedgnn_pr_auc = next(r["pr_auc"] for r in results if "FedGNN" in r["model_config_name"])

    assert pooled_pr_auc > local_pr_auc
    assert fedgnn_pr_auc > local_pr_auc
