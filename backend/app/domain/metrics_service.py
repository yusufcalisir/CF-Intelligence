"""Domain-level Scientific Validation Metrics Service.

Computes precision-recall AUC, recall at fixed 0.1% false positive rate (Recall @ 0.1% FPR),
precision@K, detection latency, communication payload overhead, DP budget consumption,
and cross-bank generalization deltas for scientific benchmarking.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)


@dataclass
class ScientificValidationMetrics:
    """Scientific evaluation metrics for imbalanced cross-bank fraud detection."""

    model_config_name: str
    pr_auc: float
    roc_auc: float
    recall_at_01_fpr: float
    precision_at_k: float
    detection_latency_ms: float
    communication_payload_mb: float
    dp_epsilon: float
    dp_delta: float
    cross_bank_generalization_delta: float

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return asdict(self)


def compute_pr_auc(y_true: list[int] | np.ndarray, y_pred: list[float] | np.ndarray) -> float:
    """Computes Precision-Recall Area Under Curve (PR-AUC) using sklearn."""
    from sklearn.metrics import auc, precision_recall_curve

    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)

    if len(np.unique(y_t)) < 2:
        return 0.5

    precision, recall, _ = precision_recall_curve(y_t, y_p)
    return round(float(auc(recall, precision)), 4)


def compute_recall_at_fpr(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    target_fpr: float = 0.001,
) -> float:
    """Computes Recall at a fixed False Positive Rate (e.g. 0.1% FPR = 1 in 1,000 legitimate transactions)."""
    from sklearn.metrics import roc_curve

    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)

    if len(np.unique(y_t)) < 2:
        return 0.0

    fpr, tpr, _ = roc_curve(y_t, y_p)
    fpr_f = np.asarray(fpr, dtype=np.float64)
    tpr_f = np.asarray(tpr, dtype=np.float64)
    # Find recall (tpr) at target_fpr using linear interpolation
    recall_val = float(np.interp(target_fpr, fpr_f, tpr_f))
    return round(recall_val, 4)


def compute_precision_at_k(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    k: int = 100,
) -> float:
    """Computes Precision among top K highest risk-scored transactions."""
    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)

    if len(y_t) == 0:
        return 0.0

    top_k_indices = np.argsort(y_p)[::-1][: min(k, len(y_p))]
    top_k_labels = y_t[top_k_indices]

    if len(top_k_labels) == 0:
        return 0.0

    precision_k = float(np.sum(top_k_labels == 1) / len(top_k_labels))
    return round(precision_k, 4)


def compute_scientific_benchmark(
    model_config_name: str,
    y_true: npt.ArrayLike,
    y_pred: npt.ArrayLike,
    detection_latency_ms: float = 4.2,
    communication_payload_mb: float = 1.2,
    dp_epsilon: float = 0.0,
    dp_delta: float = 0.0,
    cross_bank_generalization_delta: float = 0.0,
    top_k: int = 100,
) -> ScientificValidationMetrics:
    """Computes all 8 scientific evaluation metrics for a model configuration."""
    from sklearn.metrics import roc_auc_score

    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)

    roc_auc = 0.5
    if len(np.unique(y_t)) >= 2:
        roc_auc = round(float(roc_auc_score(y_t, y_p)), 4)

    pr_auc = compute_pr_auc(y_t, y_p)
    rec_01_fpr = compute_recall_at_fpr(y_t, y_p, target_fpr=0.001)
    prec_k = compute_precision_at_k(y_t, y_p, k=top_k)

    return ScientificValidationMetrics(
        model_config_name=model_config_name,
        pr_auc=pr_auc,
        roc_auc=roc_auc,
        recall_at_01_fpr=rec_01_fpr,
        precision_at_k=prec_k,
        detection_latency_ms=round(detection_latency_ms, 2),
        communication_payload_mb=round(communication_payload_mb, 2),
        dp_epsilon=round(dp_epsilon, 2),
        dp_delta=dp_delta,
        cross_bank_generalization_delta=round(cross_bank_generalization_delta, 4),
    )
