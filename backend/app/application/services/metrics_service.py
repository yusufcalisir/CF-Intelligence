"""Metrics computation and comparison service.

Converts raw evaluation dicts from ModelService into domain value objects,
and computes aggregate comparisons between local and federated models.
"""

from __future__ import annotations

import logging

from app.domain.value_objects import EvaluationMetrics

logger = logging.getLogger(__name__)


class MetricsService:
    """Transforms and aggregates evaluation metrics."""

    @staticmethod
    def from_eval_dict(
        eval_dict: dict,
        feature_importance: dict[str, float] | None = None,
    ) -> EvaluationMetrics:
        """Convert ModelService evaluation output to a domain value object."""
        return EvaluationMetrics(
            accuracy=float(eval_dict.get("accuracy", 0.0)),
            precision=float(eval_dict.get("precision", eval_dict.get("prec", 0.0))),
            recall=float(eval_dict.get("recall", eval_dict.get("rec", 0.0))),
            f1_score=float(eval_dict.get("f1_score", eval_dict.get("f1", 0.0))),
            auc_roc=float(eval_dict.get("auc_roc", eval_dict.get("auc", 0.0))),
            loss=float(eval_dict.get("loss", 0.0)),
            confusion_matrix=eval_dict.get("confusion_matrix", [[0, 0], [0, 0]]),
            roc_fpr=eval_dict.get("roc_fpr", []),
            roc_tpr=eval_dict.get("roc_tpr", []),
            roc_thresholds=eval_dict.get("roc_thresholds", []),
            feature_importance=feature_importance or {},
            disparate_impact=eval_dict.get("disparate_impact", 1.0),
            equal_opportunity_diff=eval_dict.get("equal_opportunity_diff", 0.0),
            protected_selection_rate=eval_dict.get("protected_selection_rate", 1.0),
            reference_selection_rate=eval_dict.get("reference_selection_rate", 1.0),
        )

    @staticmethod
    def compute_aggregate_improvement(
        local_metrics: list[EvaluationMetrics],
        federated_metrics: list[EvaluationMetrics],
    ) -> dict[str, float]:
        """Compute average improvement across all banks.

        Returns the mean delta for each metric (federated - local).
        Positive values indicate federated model outperforms local.
        """
        if not local_metrics or not federated_metrics:
            return {}

        n = len(local_metrics)
        improvements: dict[str, float] = {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
            "auc_roc": 0.0,
        }

        for local, federated in zip(local_metrics, federated_metrics, strict=False):
            improvements["accuracy"] += federated.accuracy - local.accuracy
            improvements["precision"] += federated.precision - local.precision
            improvements["recall"] += federated.recall - local.recall
            improvements["f1_score"] += federated.f1_score - local.f1_score
            improvements["auc_roc"] += federated.auc_roc - local.auc_roc

        return {k: round(v / n, 4) for k, v in improvements.items()}

    @staticmethod
    def metrics_to_dict(metrics: EvaluationMetrics) -> dict:
        """Serialize metrics to a plain dict for storage/API response."""
        return {
            "accuracy": metrics.accuracy,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1_score": metrics.f1_score,
            "auc_roc": metrics.auc_roc,
            "loss": metrics.loss,
            "confusion_matrix": metrics.confusion_matrix,
            "roc_fpr": metrics.roc_fpr,
            "roc_tpr": metrics.roc_tpr,
            "roc_thresholds": metrics.roc_thresholds,
            "feature_importance": metrics.feature_importance,
            "disparate_impact": metrics.disparate_impact,
            "equal_opportunity_diff": metrics.equal_opportunity_diff,
            "protected_selection_rate": metrics.protected_selection_rate,
            "reference_selection_rate": metrics.reference_selection_rate,
        }
