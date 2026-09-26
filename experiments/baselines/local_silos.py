"""Isolated Local Banking Silo Evaluator.

Simulates $K$ isolated financial institutions where each bank trains strictly
on its local data partition with zero parameter or intelligence exchange.

Evaluates in-domain performance, cross-bank generalization degradation, and
the 'silo deficit' against the untouched global consortium test set.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import numpy as np

from experiments.baselines.classical_baselines import (
    ClassicalBaselines,
    compute_safe_metrics,
)

logger = logging.getLogger(__name__)


class LocalSiloEvaluator:
    """Evaluates the statistical and fraud detection penalty of isolated bank silos."""

    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.silo_models: dict[str, Any] = {}
        self.silo_metrics: dict[str, dict[str, Any]] = {}
        self.cross_bank_matrix: dict[str, dict[str, float]] = {}

    def train_silo_models(
        self,
        bank_partitions: Mapping[str, tuple[Any, Any]] | dict[str, Any],
        model_type: str = "gradient_boosting",  # "gradient_boosting", "random_forest", "logistic_regression"
    ) -> dict[str, Any]:
        """Train an isolated model for each bank using only that bank's local partition.

        Parameters
        ----------
        bank_partitions : dict[str, tuple[np.ndarray, np.ndarray]]
            Mapping of bank_id -> (X_train_k, y_train_k).
        model_type : str
            Choice of tabular model to train locally.

        Returns
        -------
        dict[str, Any]
            Mapping of bank_id to fitted local model.
        """
        self.silo_models.clear()
        baselines = ClassicalBaselines(random_state=self.random_state)

        for bank_id, (X_k, y_k) in bank_partitions.items():
            logger.info("Training isolated silo model for %s (%d records)...", bank_id, len(y_k))
            if model_type == "random_forest":
                model = baselines.fit_random_forest(X_k, y_k, n_estimators=100)
            elif model_type == "logistic_regression":
                model = baselines.fit_logistic_regression(X_k, y_k)
            else:
                model = baselines.fit_gradient_boosting(X_k, y_k)

            self.silo_models[bank_id] = model

        return self.silo_models

    def evaluate_silos_on_global_test(
        self,
        X_global_test: np.ndarray | Any,
        y_global_test: np.ndarray | Any,
    ) -> dict[str, dict[str, Any]]:
        """Evaluate each bank's isolated model against the untouched global consortium test set.

        This quantifies how much fraud a single bank misses because it lacks
        cross-institutional pattern visibility.
        """
        baselines = ClassicalBaselines(random_state=self.random_state)
        results: dict[str, dict[str, Any]] = {}

        for bank_id, model in self.silo_models.items():
            metrics = baselines.evaluate_model(
                model=model,
                X_test=X_global_test,
                y_test=y_global_test,
                model_name=f"Silo Model ({bank_id})",
            )
            results[bank_id] = metrics

        self.silo_metrics = results
        return results

    def evaluate_cross_bank_transfer(
        self,
        bank_test_partitions: Mapping[str, tuple[Any, Any]] | dict[str, Any],
        metric_key: str = "pr_auc",
    ) -> dict[str, dict[str, float]]:
        """Compute the transfer matrix T[i][j]: model trained on Bank i evaluated on Bank j.

        Reveals domain shift and blind spots when fraud topologies migrate across institutions.
        """
        baselines = ClassicalBaselines(random_state=self.random_state)
        matrix: dict[str, dict[str, float]] = {}

        for train_bank, model in self.silo_models.items():
            matrix[train_bank] = {}
            for eval_bank, (X_test_j, y_test_j) in bank_test_partitions.items():
                probs, duration = baselines.predict_proba(model, X_test_j)
                m = compute_safe_metrics(y_true=y_test_j, y_pred_proba=probs, inference_duration_s=duration)
                matrix[train_bank][eval_bank] = round(float(m.get(metric_key, 0.0)), 4)

        self.cross_bank_matrix = matrix
        return matrix

    def compute_silo_summary(
        self,
        federated_pr_auc: float | None = None,
        federated_roc_auc: float | None = None,
    ) -> dict[str, Any]:
        """Compute aggregate silo metrics, mean silo performance, and collaborative delta."""
        if not self.silo_metrics:
            return {
                "mean_pr_auc": 0.0,
                "mean_roc_auc": 0.5,
                "mean_recall_at_01_fpr": 0.0,
                "silo_count": 0,
                "collaborative_uplift_pr_auc": 0.0,
                "collaborative_uplift_roc_auc": 0.0,
            }

        pr_aucs = [m["pr_auc"] for m in self.silo_metrics.values()]
        roc_aucs = [m["roc_auc"] for m in self.silo_metrics.values()]
        recalls_01 = [m["recall_at_01_fpr"] for m in self.silo_metrics.values()]

        mean_pr = float(np.mean(pr_aucs))
        mean_roc = float(np.mean(roc_aucs))
        mean_rec = float(np.mean(recalls_01))

        uplift_pr = (federated_pr_auc - mean_pr) if federated_pr_auc is not None else 0.0
        uplift_roc = (federated_roc_auc - mean_roc) if federated_roc_auc is not None else 0.0

        return {
            "mean_pr_auc": round(mean_pr, 4),
            "mean_roc_auc": round(mean_roc, 4),
            "mean_recall_at_01_fpr": round(mean_rec, 4),
            "silo_count": len(self.silo_metrics),
            "individual_banks": self.silo_metrics,
            "collaborative_uplift_pr_auc": round(uplift_pr, 4),
            "collaborative_uplift_roc_auc": round(uplift_roc, 4),
            "cross_bank_transfer_matrix": self.cross_bank_matrix,
        }
