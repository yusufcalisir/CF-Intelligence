"""Metrics computation and comparison service.

Converts raw evaluation dicts from ModelService into domain value objects,
and computes aggregate comparisons between local and federated models.
"""

from __future__ import annotations

import logging
from typing import Any

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
        import math

        raw_auc = eval_dict.get("auc_roc") if eval_dict.get("auc_roc") is not None else eval_dict.get("auc")
        auc_roc: float | None = None
        if raw_auc is not None and not isinstance(raw_auc, (bool, str, bytes)):
            try:
                val = float(raw_auc)
                if math.isfinite(val) and 0.0 <= val <= 1.0:
                    auc_roc = val
            except (ValueError, TypeError):
                auc_roc = None

        raw_pr = eval_dict.get("pr_auc")
        pr_auc: float | None = None
        if raw_pr is not None and not isinstance(raw_pr, (bool, str, bytes)):
            try:
                val_pr = float(raw_pr)
                if math.isfinite(val_pr) and 0.0 <= val_pr <= 1.0:
                    pr_auc = val_pr
            except (ValueError, TypeError):
                pr_auc = None

        def _parse_opt_float(raw_val: Any) -> float | None:
            if raw_val is None or isinstance(raw_val, (bool, str, bytes)):
                return None
            try:
                v = float(raw_val)
                return v if math.isfinite(v) else None
            except (ValueError, TypeError):
                return None

        # Build EvaluationMetrics domain value object with validated metrics.
        metrics_kwargs: dict[str, Any] = {
            "accuracy": _parse_opt_float(eval_dict.get("accuracy")),
            "precision": _parse_opt_float(eval_dict.get("precision", eval_dict.get("prec"))),
            "recall": _parse_opt_float(eval_dict.get("recall", eval_dict.get("rec"))),
            "f1_score": _parse_opt_float(eval_dict.get("f1_score", eval_dict.get("f1"))),
            "auc_roc": auc_roc,
            "loss": _parse_opt_float(eval_dict.get("loss")),
            "confusion_matrix": eval_dict.get("confusion_matrix"),
            "roc_fpr": eval_dict.get("roc_fpr", []),
            "roc_tpr": eval_dict.get("roc_tpr", []),
            "roc_thresholds": eval_dict.get("roc_thresholds", []),
            "feature_importance": feature_importance or {},
            "disparate_impact": _parse_opt_float(eval_dict.get("disparate_impact")),
            "equal_opportunity_diff": _parse_opt_float(eval_dict.get("equal_opportunity_diff")),
            "protected_selection_rate": _parse_opt_float(eval_dict.get("protected_selection_rate")),
            "reference_selection_rate": _parse_opt_float(eval_dict.get("reference_selection_rate")),
            "adversarial_robustness_score": _parse_opt_float(eval_dict.get("adversarial_robustness_score")),
            "clean_accuracy": _parse_opt_float(eval_dict.get("clean_accuracy")),
            "robust_accuracy": _parse_opt_float(eval_dict.get("robust_accuracy")),
            "fgsm_evasion_rate": _parse_opt_float(eval_dict.get("fgsm_evasion_rate")),
            "pgd_evasion_rate": _parse_opt_float(eval_dict.get("pgd_evasion_rate")),
            "threshold": float(eval_dict.get("threshold", 0.5)),
            "pr_auc": pr_auc,
            "predicted_positives": int(eval_dict.get("predicted_positives", 0)),
            "threshold_provenance": str(eval_dict.get("threshold_provenance", "default_fixed_0.5")),
            "dp_provenance": eval_dict.get("dp_provenance"),
            "auc_roc_defined": eval_dict.get("auc_roc_defined", auc_roc is not None),
            "auc_roc_status": str(eval_dict.get("auc_roc_status", "defined" if auc_roc is not None else "undefined")),
        }
        return EvaluationMetrics(**metrics_kwargs)

    @staticmethod
    def compute_aggregate_improvement(
        local_metrics: list[EvaluationMetrics],
        federated_metrics: list[EvaluationMetrics],
    ) -> dict[str, float | None]:
        """Compute average improvement across all banks.

        Returns the mean delta for each metric (federated - local).
        Positive values indicate federated model outperforms local.
        """
        if not local_metrics or not federated_metrics:
            return {}

        deltas: dict[str, list[float]] = {
            "accuracy": [],
            "precision": [],
            "recall": [],
            "f1_score": [],
            "auc_roc": [],
        }

        for local, federated in zip(local_metrics, federated_metrics, strict=False):
            if federated.accuracy is not None and local.accuracy is not None:
                deltas["accuracy"].append(federated.accuracy - local.accuracy)
            if federated.precision is not None and local.precision is not None:
                deltas["precision"].append(federated.precision - local.precision)
            if federated.recall is not None and local.recall is not None:
                deltas["recall"].append(federated.recall - local.recall)
            if federated.f1_score is not None and local.f1_score is not None:
                deltas["f1_score"].append(federated.f1_score - local.f1_score)
            if federated.auc_roc is not None and local.auc_roc is not None:
                deltas["auc_roc"].append(federated.auc_roc - local.auc_roc)

        res: dict[str, float | None] = {}
        for k, v in deltas.items():
            res[k] = round(sum(v) / len(v), 4) if v else None
        return res

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
            "adversarial_robustness_score": metrics.adversarial_robustness_score,
            "clean_accuracy": metrics.clean_accuracy,
            "robust_accuracy": metrics.robust_accuracy,
            "fgsm_evasion_rate": metrics.fgsm_evasion_rate,
            "pgd_evasion_rate": metrics.pgd_evasion_rate,
            "threshold": metrics.threshold,
            "pr_auc": metrics.pr_auc,
            "predicted_positives": metrics.predicted_positives,
            "threshold_provenance": metrics.threshold_provenance,
            "dp_provenance": metrics.dp_provenance,
        }
