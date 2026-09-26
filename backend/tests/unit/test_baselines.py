"""Unit tests for Classical Tabular Baselines and Pooled Centralized Upper Bound.

Validates:
- Logistic Regression, Random Forest, and Gradient Boosted Decision Trees
- Probability calibration, Brier score, and Recall @ strict FPR (0.1%, 0.5%, 1.0%)
- Single-class and edge case evaluation safety
- Partition pooling and theoretical upper bound calculations
- Centralization gap and federated efficiency metrics
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.baselines.classical_baselines import (  # noqa: E402
    ClassicalBaselines,
    compute_recall_at_fpr,
    compute_safe_metrics,
)
from experiments.baselines.comparative_runner import ComparativeBenchmarkEngine  # noqa: E402
from experiments.baselines.pooled_upper_bound import (  # noqa: E402
    PooledCentralizedBenchmark,
)


@pytest.fixture
def synthetic_fraud_data():
    """Generates synthetic binary classification dataset with realistic fraud imbalance."""
    rng = np.random.default_rng(42)
    n_samples = 1000
    n_features = 8

    # 95% legit, 5% fraud
    n_fraud = 50
    n_legit = n_samples - n_fraud

    X_legit = rng.normal(loc=0.0, scale=1.0, size=(n_legit, n_features))
    X_fraud = rng.normal(loc=1.5, scale=1.2, size=(n_fraud, n_features))

    X = np.vstack([X_legit, X_fraud]).astype(np.float32)
    y = np.array([0] * n_legit + [1] * n_fraud, dtype=int)

    # Shuffle
    idx = rng.permutation(n_samples)
    X, y = X[idx], y[idx]

    # 70/30 split
    split_idx = int(0.7 * n_samples)
    return X[:split_idx], y[:split_idx], X[split_idx:], y[split_idx:]


class TestClassicalBaselines:
    """Test classical tabular algorithms and metric computation."""

    def test_safe_metrics_normal(self, synthetic_fraud_data):
        _, _, X_test, y_test = synthetic_fraud_data
        probs = np.linspace(0.01, 0.99, len(y_test))
        metrics = compute_safe_metrics(y_true=y_test, y_pred_proba=probs, inference_duration_s=0.05)

        assert 0.0 <= metrics["roc_auc"] <= 1.0
        assert 0.0 <= metrics["pr_auc"] <= 1.0
        assert 0.0 <= metrics["brier_score"] <= 1.0
        assert 0.0 <= metrics["recall_at_01_fpr"] <= 1.0
        assert metrics["samples_evaluated"] == len(y_test)
        assert metrics["latency_ms_batch"] > 0.0

    def test_safe_metrics_single_class(self):
        y_all_zero = np.zeros(100, dtype=int)
        probs = np.random.uniform(0.0, 1.0, 100)
        metrics = compute_safe_metrics(y_true=y_all_zero, y_pred_proba=probs)

        # Should not raise exception and should fallback gracefully
        assert metrics["roc_auc"] == 0.5
        assert metrics["f1_score"] == 0.0

    def test_compute_recall_at_fpr_boundary(self):
        y_true = np.array([0] * 90 + [1] * 10)
        # Perfect predictions
        y_pred = np.array([0.01] * 90 + [0.99] * 10)

        recall_01 = compute_recall_at_fpr(y_true, y_pred, target_fpr=0.01)
        assert recall_01 == 1.0

    def test_fit_and_eval_logistic_regression(self, synthetic_fraud_data):
        X_train, y_train, X_test, y_test = synthetic_fraud_data
        baselines = ClassicalBaselines(random_state=42)

        model = baselines.fit_logistic_regression(X_train, y_train)
        metrics = baselines.evaluate_model(model, X_test, y_test, model_name="LR")

        assert metrics["roc_auc"] > 0.65
        assert metrics["pr_auc"] > 0.10
        assert "latency_ms_per_sample" in metrics

    def test_fit_and_eval_random_forest(self, synthetic_fraud_data):
        X_train, y_train, X_test, y_test = synthetic_fraud_data
        baselines = ClassicalBaselines(random_state=42)

        model = baselines.fit_random_forest(X_train, y_train, n_estimators=50)
        metrics = baselines.evaluate_model(model, X_test, y_test, model_name="RF")

        assert metrics["roc_auc"] > 0.70
        assert metrics["pr_auc"] > 0.15

    def test_fit_and_eval_gradient_boosting(self, synthetic_fraud_data):
        X_train, y_train, X_test, y_test = synthetic_fraud_data
        baselines = ClassicalBaselines(random_state=42)

        model = baselines.fit_gradient_boosting(X_train, y_train, max_iter=30)
        metrics = baselines.evaluate_model(model, X_test, y_test, model_name="GBDT")

        assert metrics["roc_auc"] > 0.70
        assert metrics["pr_auc"] > 0.15

    def test_run_all_baselines(self, synthetic_fraud_data):
        X_train, y_train, X_test, y_test = synthetic_fraud_data
        baselines = ClassicalBaselines(random_state=42)

        results = baselines.run_all_baselines(X_train, y_train, X_test, y_test)
        assert "logistic_regression" in results
        assert "random_forest" in results
        assert "gradient_boosting" in results
        for k, v in results.items():
            assert "roc_auc" in v
            assert "pr_auc" in v


class TestPooledCentralizedBenchmark:
    """Test pooled upper bound and centralization gap analysis."""

    def test_pool_partitions(self):
        benchmark = PooledCentralizedBenchmark(random_state=42)
        bank_partitions = {
            "bank_a": (np.ones((50, 4)), np.zeros(50, dtype=int)),
            "bank_b": (np.ones((60, 4)) * 2, np.ones(60, dtype=int)),
            "bank_c": (np.ones((40, 4)) * 3, np.zeros(40, dtype=int)),
        }

        X_pooled, y_pooled = benchmark.pool_partitions(bank_partitions)
        assert X_pooled.shape == (150, 4)
        assert len(y_pooled) == 150
        assert np.sum(y_pooled) == 60

    def test_fit_and_evaluate_pooled_models(self, synthetic_fraud_data):
        X_train, y_train, X_test, y_test = synthetic_fraud_data
        benchmark = PooledCentralizedBenchmark(random_state=42)

        results = benchmark.fit_and_evaluate_all(
            X_pooled_train=X_train,
            y_pooled_train=y_train,
            X_global_test=X_test,
            y_global_test=y_test,
            train_neural_mlp=True,
            mlp_epochs=5,
        )

        assert "pooled_gradient_boosting" in results
        assert "pooled_random_forest" in results
        assert "pooled_logistic_regression" in results
        assert "pooled_neural_mlp" in results

    def test_compute_centralization_gap(self):
        benchmark = PooledCentralizedBenchmark(random_state=42)
        benchmark.evaluation_results = {
            "pooled_gradient_boosting": {
                "pr_auc": 0.8200,
                "roc_auc": 0.9650,
                "recall_at_01_fpr": 0.6500,
            }
        }

        gap = benchmark.compute_centralization_gap(
            federated_pr_auc=0.7950,
            federated_roc_auc=0.9580,
            champion_pooled_key="pooled_gradient_boosting",
        )

        assert gap["pooled_pr_auc"] == 0.8200
        assert gap["centralization_gap_pr_auc"] == pytest.approx(0.0250, abs=1e-4)
        assert gap["centralization_gap_roc_auc"] == pytest.approx(0.0070, abs=1e-4)
        assert gap["federated_efficiency_pct"] > 95.0


class TestComparativeBenchmarkEngine:
    """Test full multi-paradigm orchestrator execution and serialization."""

    def test_run_full_comparative_suite(self, synthetic_fraud_data):
        X_train, y_train, X_test, y_test = synthetic_fraud_data

        # Partition X_train across 3 banks
        n = len(y_train)
        s1, s2 = n // 3, 2 * (n // 3)
        bank_partitions = {
            "bank_a": (X_train[:s1], y_train[:s1]),
            "bank_b": (X_train[s1:s2], y_train[s1:s2]),
            "bank_c": (X_train[s2:], y_train[s2:]),
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = ComparativeBenchmarkEngine(random_state=42, output_dir=tmp_dir)
            report = engine.run_full_comparative_suite(
                bank_train_partitions=bank_partitions,
                X_global_test=X_test,
                y_global_test=y_test,
                dataset_name="TestSyntheticFraud",
                train_neural=True,
            )

            assert report["dataset_name"] == "TestSyntheticFraud"
            assert report["bank_count"] == 3
            assert len(report["comparison_matrix"]) == 6
            assert "centralization_gap_analysis" in report
            assert "silo_deficit_analysis" in report

            # Verify exported file
            exported_file = Path(tmp_dir) / "comparative_baselines.json"
            assert exported_file.exists()
            assert exported_file.stat().st_size > 500


class TestComparativeBaselinesEndpoint:
    """Test FastAPI endpoint for comparative baseline dashboard data."""

    def test_get_comparative_baselines_route(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        response = client.get("/api/v1/dashboard/comparative-baselines")
        assert response.status_code == 200
        payload = response.json()

        assert "comparison_matrix" in payload
        assert len(payload["comparison_matrix"]) >= 4
        assert "centralization_gap_analysis" in payload
        assert "silo_deficit_analysis" in payload
        assert payload["centralization_gap_analysis"]["federated_efficiency_pct"] > 80.0
        assert payload["silo_deficit_analysis"]["silo_count"] >= 1

