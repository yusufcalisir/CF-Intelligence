"""Classical Tabular Benchmark Baselines.

Implements standard machine learning baselines for fraud detection:
- Logistic Regression (L2 regularized, class-balanced)
- Random Forest (Ensemble bagging of decision trees, class-balanced)
- Gradient Boosted Decision Trees (HistGradientBoosting / LightGBM equivalent)

All models compute standard classification metrics, probability calibration
scores, Recall @ strict operational False Positive Rates (0.1%, 0.5%, 1.0%),
and inference latency benchmarks.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)

logger = logging.getLogger(__name__)


def compute_recall_at_fpr(
    y_true: np.ndarray | Any, y_pred_proba: np.ndarray | Any, target_fpr: float
) -> float:
    """Calculate true positive rate (recall) at a strict maximum false positive rate.

    Parameters
    ----------
    y_true : np.ndarray | Any
        Ground truth binary labels (0 or 1).
    y_pred_proba : np.ndarray | Any
        Predicted probabilities of positive class.
    target_fpr : float
        Maximum allowable false positive rate (e.g. 0.001 for 0.1% FPR).

    Returns
    -------
    float
        Recall achieved at the highest threshold yielding FPR <= target_fpr.
    """
    if len(np.unique(y_true)) < 2:
        return 0.0
    fpr, tpr, _ = roc_curve(y_true, y_pred_proba)
    idx = np.where(fpr <= target_fpr)[0]
    if len(idx) == 0:
        return 0.0
    return float(tpr[idx[-1]])


def compute_safe_metrics(
    y_true: np.ndarray | Any,
    y_pred_proba: np.ndarray | Any,
    inference_duration_s: float = 0.0,
    model_name: str = "Model",
) -> dict[str, Any]:
    """Compute comprehensive classification and operational metrics.

    Safely handles edge cases like single-class arrays or non-finite values.
    """
    n_samples = len(y_true)
    if n_samples == 0:
        return {
            "model_name": model_name,
            "roc_auc": 0.5,
            "pr_auc": 0.0,
            "f1_score": 0.0,
            "brier_score": 0.0,
            "recall_at_01_fpr": 0.0,
            "recall_at_05_fpr": 0.0,
            "recall_at_1_fpr": 0.0,
            "samples_evaluated": 0,
            "latency_ms_per_sample": 0.0,
            "latency_ms_batch": 0.0,
        }

    # Ensure probabilities are bounded in [0, 1]
    probs = np.clip(y_pred_proba, 0.0, 1.0)
    y_binary = (y_true > 0).astype(int)

    unique_classes = np.unique(y_binary)
    if len(unique_classes) < 2:
        roc_auc = 0.5
        pr_auc = float(np.mean(y_binary))
        f1 = 0.0
        recall_01 = 0.0
        recall_05 = 0.0
        recall_10 = 0.0
    else:
        try:
            roc_auc = float(roc_auc_score(y_binary, probs))
        except ValueError:
            roc_auc = 0.5

        try:
            pr_auc = float(average_precision_score(y_binary, probs))
        except ValueError:
            pr_auc = 0.0

        # Optimal F1 search along PR curve
        try:
            precisions, recalls, thresholds = precision_recall_curve(y_binary, probs)
            f1_scores = np.where(
                (precisions + recalls) > 0,
                2 * (precisions * recalls) / (precisions + recalls + 1e-12),
                0.0,
            )
            f1 = float(np.max(f1_scores)) if len(f1_scores) > 0 else 0.0
        except Exception:
            preds_default = (probs >= 0.5).astype(int)
            f1 = float(f1_score(y_binary, preds_default, zero_division=0))

        recall_01 = compute_recall_at_fpr(y_binary, probs, target_fpr=0.001)
        recall_05 = compute_recall_at_fpr(y_binary, probs, target_fpr=0.005)
        recall_10 = compute_recall_at_fpr(y_binary, probs, target_fpr=0.010)

    try:
        brier = float(brier_score_loss(y_binary, probs))
    except Exception:
        brier = float(np.mean((probs - y_binary) ** 2))

    batch_latency_ms = float(inference_duration_s * 1000.0)
    per_sample_latency_ms = float((batch_latency_ms / n_samples) if n_samples > 0 else 0.0)

    return {
        "model_name": model_name,
        "roc_auc": round(roc_auc, 4),
        "pr_auc": round(pr_auc, 4),
        "f1_score": round(f1, 4),
        "brier_score": round(brier, 4),
        "recall_at_01_fpr": round(recall_01, 4),
        "recall_at_05_fpr": round(recall_05, 4),
        "recall_at_1_fpr": round(recall_10, 4),
        "samples_evaluated": n_samples,
        "latency_ms_batch": round(batch_latency_ms, 3),
        "latency_ms_per_sample": round(per_sample_latency_ms, 5),
    }


class ClassicalBaselines:
    """Benchmark suite for classical tabular classification algorithms."""

    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.fitted_models: dict[str, Any] = {}

    def fit_logistic_regression(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        max_iter: int = 500,
        c_param: float = 1.0,
    ) -> LogisticRegression:
        """Fit class-balanced Logistic Regression with L2 regularization."""
        model = LogisticRegression(
            C=c_param,
            max_iter=max_iter,
            class_weight="balanced",
            random_state=self.random_state,
            solver="lbfgs",
        )
        model.fit(X_train, y_train)
        self.fitted_models["logistic_regression"] = model
        return model

    def fit_random_forest(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        n_estimators: int = 100,
        max_depth: int | None = 12,
        n_jobs: int = -1,
    ) -> RandomForestClassifier:
        """Fit class-balanced Random Forest ensemble."""
        model = RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            class_weight="balanced",
            random_state=self.random_state,
            n_jobs=n_jobs,
        )
        model.fit(X_train, y_train)
        self.fitted_models["random_forest"] = model
        return model

    def fit_gradient_boosting(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        max_iter: int = 100,
        max_leaf_nodes: int = 31,
        learning_rate: float = 0.1,
    ) -> HistGradientBoostingClassifier:
        """Fit Histogram-based Gradient Boosted Trees (LightGBM equivalent in scikit-learn)."""
        # Compute class balance weights
        y_binary = (y_train > 0).astype(int)
        pos_count = int(np.sum(y_binary))
        neg_count = len(y_binary) - pos_count
        pos_weight = float(neg_count / max(1, pos_count))

        sample_weights = np.where(y_binary == 1, pos_weight, 1.0)

        model = HistGradientBoostingClassifier(
            max_iter=max_iter,
            max_leaf_nodes=max_leaf_nodes,
            learning_rate=learning_rate,
            random_state=self.random_state,
        )
        model.fit(X_train, y_binary, sample_weight=sample_weights)
        self.fitted_models["gradient_boosting"] = model
        return model

    def predict_proba(self, model: Any, X: np.ndarray) -> tuple[np.ndarray, float]:
        """Predict probabilities and record inference execution duration."""
        start = time.perf_counter()
        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(X)
            duration = time.perf_counter() - start
            if probs.ndim == 2 and probs.shape[1] >= 2:
                return probs[:, 1], duration
            return probs.ravel(), duration
        elif hasattr(model, "decision_function"):
            raw_scores = model.decision_function(X)
            # Sigmoid normalization
            probs = 1.0 / (1.0 + np.exp(-raw_scores))
            duration = time.perf_counter() - start
            return probs, duration
        else:
            preds = model.predict(X).astype(float)
            duration = time.perf_counter() - start
            return preds, duration

    def evaluate_model(
        self,
        model: Any,
        X_test: np.ndarray,
        y_test: np.ndarray,
        model_name: str,
    ) -> dict[str, Any]:
        """Evaluate a single trained model on test data."""
        probs, duration = self.predict_proba(model, X_test)
        return compute_safe_metrics(
            y_true=y_test,
            y_pred_proba=probs,
            inference_duration_s=duration,
            model_name=model_name,
        )

    def run_all_baselines(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_test: np.ndarray,
        y_test: np.ndarray,
        dataset_name: str = "FinancialFraud",
    ) -> dict[str, dict[str, Any]]:
        """Train and evaluate all classical tabular baselines on given dataset splits.

        Returns
        -------
        dict[str, dict[str, Any]]
            Mapping of baseline model key to performance and latency metrics.
        """
        results: dict[str, dict[str, Any]] = {}

        # 1. Logistic Regression
        logger.info("[%s] Fitting Logistic Regression baseline...", dataset_name)
        lr = self.fit_logistic_regression(X_train, y_train)
        results["logistic_regression"] = self.evaluate_model(
            lr, X_test, y_test, model_name="Logistic Regression (L2 Balanced)"
        )

        # 2. Random Forest
        logger.info("[%s] Fitting Random Forest baseline...", dataset_name)
        rf = self.fit_random_forest(X_train, y_train)
        results["random_forest"] = self.evaluate_model(
            rf, X_test, y_test, model_name="Random Forest (100 Trees Balanced)"
        )

        # 3. Gradient Boosted Trees (HistGBDT / LightGBM)
        logger.info("[%s] Fitting Gradient Boosted Trees baseline...", dataset_name)
        gbdt = self.fit_gradient_boosting(X_train, y_train)
        results["gradient_boosting"] = self.evaluate_model(
            gbdt, X_test, y_test, model_name="Gradient Boosted Decision Trees (GBDT)"
        )

        return results
