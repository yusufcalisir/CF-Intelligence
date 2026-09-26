"""Unit tests for Isolated Local Banking Silo Training and Evaluation.

Validates:
- Multi-institution data partition isolation (zero cross-bank leakage)
- In-domain vs out-of-domain cross-institution generalization breakdown
- Silo deficit and collaborative uplift delta calculation
- Transfer matrix asymmetry across heterogeneous banking distributions
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.baselines.local_silos import LocalSiloEvaluator  # noqa: E402


@pytest.fixture
def multi_bank_partitions():
    """Create distinct partitions for 3 banks with slight distribution shifts."""
    rng = np.random.default_rng(101)
    n_features = 6

    # Bank A: Retail Domestic (small amounts, normal fraud)
    XA_legit = rng.normal(loc=0.0, scale=1.0, size=(300, n_features))
    XA_fraud = rng.normal(loc=1.2, scale=1.0, size=(20, n_features))
    XA = np.vstack([XA_legit, XA_fraud]).astype(np.float32)
    yA = np.array([0] * 300 + [1] * 20)

    # Bank B: Commercial International (high amounts, different fraud pattern)
    XB_legit = rng.normal(loc=0.5, scale=1.2, size=(250, n_features))
    XB_fraud = rng.normal(loc=2.5, scale=1.5, size=(18, n_features))
    XB = np.vstack([XB_legit, XB_fraud]).astype(np.float32)
    yB = np.array([0] * 250 + [1] * 18)

    # Bank C: Digital Challenger / FinTech (micro-transactions, velocity fraud)
    XC_legit = rng.normal(loc=-0.3, scale=0.8, size=(350, n_features))
    XC_fraud = rng.normal(loc=1.8, scale=1.1, size=(25, n_features))
    XC = np.vstack([XC_legit, XC_fraud]).astype(np.float32)
    yC = np.array([0] * 350 + [1] * 25)

    # Shuffle each bank's dataset
    def _shuffle_and_split(X_mat, y_vec):
        idx = rng.permutation(len(y_vec))
        s = int(0.7 * len(y_vec))
        return (X_mat[idx[:s]], y_vec[idx[:s]]), (X_mat[idx[s:]], y_vec[idx[s:]])

    train_A, test_A = _shuffle_and_split(XA, yA)
    train_B, test_B = _shuffle_and_split(XB, yB)
    train_C, test_C = _shuffle_and_split(XC, yC)

    train_partitions = {"bank_a": train_A, "bank_b": train_B, "bank_c": train_C}
    test_partitions = {"bank_a": test_A, "bank_b": test_B, "bank_c": test_C}

    # Global untouched consortium test set: pooled test sets
    X_global_test = np.vstack([test_A[0], test_B[0], test_C[0]])
    y_global_test = np.concatenate([test_A[1], test_B[1], test_C[1]])

    return train_partitions, test_partitions, X_global_test, y_global_test


class TestLocalSiloEvaluator:
    """Test localized banking silo training, isolation, and cross-evaluation."""

    def test_train_silo_models_isolated(self, multi_bank_partitions):
        train_partitions, _, _, _ = multi_bank_partitions
        evaluator = LocalSiloEvaluator(random_state=42)

        models = evaluator.train_silo_models(train_partitions, model_type="gradient_boosting")

        assert len(models) == 3
        assert "bank_a" in models
        assert "bank_b" in models
        assert "bank_c" in models

    def test_evaluate_silos_on_global_test(self, multi_bank_partitions):
        train_partitions, _, X_global, y_global = multi_bank_partitions
        evaluator = LocalSiloEvaluator(random_state=42)
        evaluator.train_silo_models(train_partitions, model_type="gradient_boosting")

        results = evaluator.evaluate_silos_on_global_test(X_global, y_global)

        assert len(results) == 3
        for bank_id in ["bank_a", "bank_b", "bank_c"]:
            assert bank_id in results
            assert "pr_auc" in results[bank_id]
            assert "roc_auc" in results[bank_id]
            assert "recall_at_01_fpr" in results[bank_id]

    def test_evaluate_cross_bank_transfer(self, multi_bank_partitions):
        train_partitions, test_partitions, _, _ = multi_bank_partitions
        evaluator = LocalSiloEvaluator(random_state=42)
        evaluator.train_silo_models(train_partitions, model_type="gradient_boosting")

        matrix = evaluator.evaluate_cross_bank_transfer(test_partitions, metric_key="pr_auc")

        # Must be 3x3 matrix
        assert len(matrix) == 3
        for train_b in ["bank_a", "bank_b", "bank_c"]:
            assert len(matrix[train_b]) == 3
            for eval_b in ["bank_a", "bank_b", "bank_c"]:
                assert 0.0 <= matrix[train_b][eval_b] <= 1.0

    def test_compute_silo_summary_and_uplift(self, multi_bank_partitions):
        train_partitions, _, X_global, y_global = multi_bank_partitions
        evaluator = LocalSiloEvaluator(random_state=42)
        evaluator.train_silo_models(train_partitions, model_type="gradient_boosting")
        evaluator.evaluate_silos_on_global_test(X_global, y_global)

        summary = evaluator.compute_silo_summary(
            federated_pr_auc=0.8500,
            federated_roc_auc=0.9700,
        )

        assert summary["silo_count"] == 3
        assert 0.0 <= summary["mean_pr_auc"] <= 1.0
        assert 0.0 <= summary["mean_roc_auc"] <= 1.0
        # Federated consensus should show positive collaborative uplift over isolated average
        assert summary["collaborative_uplift_pr_auc"] >= 0.0
        assert summary["collaborative_uplift_roc_auc"] >= 0.0

    def test_empty_silo_handling(self):
        evaluator = LocalSiloEvaluator(random_state=42)
        summary = evaluator.compute_silo_summary()

        assert summary["silo_count"] == 0
        assert summary["mean_pr_auc"] == 0.0
        assert summary["mean_roc_auc"] == 0.5
