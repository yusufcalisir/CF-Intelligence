"""Domain-level Scientific Validation Metrics Service.

Computes precision-recall AUC, recall at fixed 0.1% false positive rate (Recall @ 0.1% FPR),
precision@K, detection latency, communication payload overhead, DP budget consumption,
and cross-bank generalization deltas for scientific benchmarking.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)


class MetricStatusCode(StrEnum):
    """Explicit lifecycle status for mathematical metric evaluations."""

    DEFINED = "defined"
    UNDEFINED_SINGLE_CLASS = "undefined_single_class"
    UNDEFINED_EMPTY_INPUT = "undefined_empty_input"
    UNAVAILABLE = "unavailable"
    COMPUTATION_ERROR = "computation_error"


@dataclass
class ScientificValidationMetrics:
    """Scientific evaluation metrics for imbalanced cross-bank fraud detection."""

    model_config_name: str
    pr_auc: float | None
    roc_auc: float | None
    recall_at_01_fpr: float | None
    precision_at_k: float | None
    detection_latency_ms: float
    communication_payload_mb: float
    dp_epsilon: float
    dp_delta: float
    cross_bank_generalization_delta: float

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return asdict(self)


def _subsample_for_curve(y_t: np.ndarray, y_p: np.ndarray, max_samples: int = 50_000) -> tuple[np.ndarray, np.ndarray]:
    """Subsamples large evaluation arrays preserving all positive/fraud cases to prevent OOM."""
    if len(y_t) <= max_samples:
        return y_t, y_p

    fraud_idx = np.where(y_t == 1)[0]
    legit_idx = np.where(y_t == 0)[0]
    rng = np.random.default_rng(42)

    n_fraud = min(len(fraud_idx), max_samples // 2)
    n_legit = min(len(legit_idx), max_samples - n_fraud)

    sampled_fraud = rng.choice(fraud_idx, size=n_fraud, replace=False) if len(fraud_idx) > n_fraud else fraud_idx
    sampled_legit = rng.choice(legit_idx, size=n_legit, replace=False) if len(legit_idx) > n_legit else legit_idx

    sampled_idx = np.concatenate([sampled_fraud, sampled_legit])
    return y_t[sampled_idx], y_p[sampled_idx]


def validate_metric_inputs(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Validate metric inputs for 1-D matching shape and absence of non-finite values."""
    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)
    if y_t.ndim > 1:
        y_t = y_t.squeeze()
    if y_p.ndim > 1:
        y_p = y_p.squeeze()
    if y_t.ndim > 1 or y_p.ndim > 1:
        raise ValueError(
            f"Metric inputs must be 1-dimensional, got y_true shape {y_t.shape} and y_pred shape {y_p.shape}"
        )
    if y_t.shape != y_p.shape:
        raise ValueError(f"Shape mismatch: y_true shape {y_t.shape} != y_pred shape {y_p.shape}")
    if y_t.size > 0 and (not np.all(np.isfinite(y_t)) or not np.all(np.isfinite(y_p))):
        raise ValueError("Non-finite values (NaN or Inf) detected in metric inputs")
    return y_t, y_p


def is_roc_auc_defined(y_true: list[int] | np.ndarray) -> bool:
    """Return True if ROC-AUC is mathematically defined (at least two classes present)."""
    y_t = np.asarray(y_true)
    return y_t.size > 0 and len(np.unique(y_t)) >= 2


def is_pr_auc_defined(y_true: list[int] | np.ndarray) -> bool:
    """Return True if PR-AUC is mathematically defined (at least two classes present)."""
    y_t = np.asarray(y_true)
    return y_t.size > 0 and len(np.unique(y_t)) >= 2


def compute_roc_auc_with_status(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    default: float | None = None,
) -> tuple[float | None, bool, str]:
    """Compute ROC-AUC with explicit mathematical definedness status.

    Returns:
        tuple[float | None, bool, str]: (score, is_defined, status_code)
        where status_code is 'defined', 'undefined_single_class', 'undefined_empty_input', or 'computation_error'.
    """
    y_t, y_p = validate_metric_inputs(y_true, y_pred)
    if y_t.size == 0 or y_p.size == 0:
        return default, False, MetricStatusCode.UNDEFINED_EMPTY_INPUT.value
    if len(np.unique(y_t)) < 2:
        return default, False, MetricStatusCode.UNDEFINED_SINGLE_CLASS.value
    try:
        from sklearn.metrics import roc_auc_score

        val = float(roc_auc_score(y_t, y_p))
        if np.isnan(val):
            return default, False, MetricStatusCode.COMPUTATION_ERROR.value
        return round(val, 4), True, MetricStatusCode.DEFINED.value
    except Exception as exc:
        logger.error("Internal computation failure calculating ROC-AUC: %s", exc, exc_info=True)
        return default, False, MetricStatusCode.COMPUTATION_ERROR.value


def safe_roc_auc_score(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    default: float | None = None,
) -> float | None:
    """Safely compute ROC-AUC score.

    When y_true contains only one class or is empty, ROC-AUC is mathematically undefined.
    In that degenerate case, this function returns `default` (None by default).
    """
    score, is_def, _ = compute_roc_auc_with_status(y_true, y_pred, default=default)
    return score if is_def else default


def compute_pr_auc_with_status(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    default: float | None = None,
) -> tuple[float | None, bool, str]:
    """Compute PR-AUC with explicit mathematical definedness status.

    Returns:
        tuple[float | None, bool, str]: (score, is_defined, status_code)
        where status_code is 'defined', 'undefined_single_class', 'undefined_empty_input', or 'computation_error'.
    """
    y_t, y_p = validate_metric_inputs(y_true, y_pred)
    if y_t.size == 0 or y_p.size == 0:
        return default, False, MetricStatusCode.UNDEFINED_EMPTY_INPUT.value
    if len(np.unique(y_t)) < 2:
        return default, False, MetricStatusCode.UNDEFINED_SINGLE_CLASS.value
    try:
        from sklearn.metrics import auc, precision_recall_curve

        y_t_sub, y_p_sub = _subsample_for_curve(y_t, y_p)
        precision, recall, _ = precision_recall_curve(y_t_sub, y_p_sub)
        val = float(auc(recall, precision))
        if np.isnan(val):
            return default, False, MetricStatusCode.COMPUTATION_ERROR.value
        return round(val, 4), True, MetricStatusCode.DEFINED.value
    except Exception as exc:
        logger.error("Internal computation failure calculating PR-AUC: %s", exc, exc_info=True)
        return default, False, MetricStatusCode.COMPUTATION_ERROR.value


def safe_pr_auc_score(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    default: float | None = None,
) -> float | None:
    """Safely compute PR-AUC score, guarding against single-class, empty, or NaN inputs.

    Returns `default` (None by default) when undefined.
    """
    return compute_pr_auc(y_true, y_pred, default=default)


def compute_pr_auc(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    default: float | None = None,
) -> float | None:
    """Computes Precision-Recall Area Under Curve (PR-AUC) using sklearn.

    Returns `default` (None) when single-class, empty, or ill-defined, preventing
    fabrication of arbitrary positive scores (e.g. 0.5) or conflation with valid 0.0.
    """
    score, is_def, _ = compute_pr_auc_with_status(y_true, y_pred, default=default)
    return score if is_def else default


def safe_precision_recall_curve(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Safely compute precision-recall curve, providing fallback arrays on single-class inputs."""
    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)
    if y_t.size == 0 or y_p.size == 0 or len(np.unique(y_t)) < 2:
        return np.array([0.0, 1.0]), np.array([1.0, 0.0]), np.array([0.5])
    try:
        from sklearn.metrics import precision_recall_curve

        prec, rec, thresh = precision_recall_curve(y_t, y_p)
        return np.asarray(prec), np.asarray(rec), np.asarray(thresh)
    except Exception:
        return np.array([0.0, 1.0]), np.array([1.0, 0.0]), np.array([0.5])


def safe_f1_score(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    threshold: float = 0.5,
    default: float | None = None,
) -> float | None:
    """Safely compute binary F1-score with zero-division handling."""
    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)
    if y_t.size == 0 or y_p.size == 0 or len(np.unique(y_t)) < 2:
        return default
    try:
        from sklearn.metrics import f1_score

        preds = (y_p >= threshold).astype(int)
        zero_div: Any = 0
        val = float(f1_score(y_t, preds, zero_division=zero_div))
        return default if np.isnan(val) else val
    except Exception as exc:
        logger.error("Internal computation failure calculating F1 score: %s", exc, exc_info=True)
        return default


def compute_recall_at_fpr(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    target_fpr: float = 0.001,
) -> float | None:
    """Computes Recall at a fixed False Positive Rate (e.g. 0.1% FPR = 1 in 1,000 legitimate transactions)."""
    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)

    if y_t.size == 0 or len(np.unique(y_t)) < 2:
        return None

    y_t, y_p = _subsample_for_curve(y_t, y_p)
    from sklearn.metrics import roc_curve

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
) -> float | None:
    """Computes Precision among top K highest risk-scored transactions."""
    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)

    if len(y_t) == 0:
        return None

    top_k_indices = np.argsort(y_p)[::-1][: min(k, len(y_p))]
    top_k_labels = y_t[top_k_indices]

    if len(top_k_labels) == 0:
        return None

    precision_k = float(np.sum(top_k_labels == 1) / len(top_k_labels))
    return round(precision_k, 4)


@dataclass
class ConfusionMatrixAtThreshold:
    """Confusion matrix and derived rates at a specific decision threshold."""

    threshold: float
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float
    recall: float
    fpr: float  # False Positive Rate (FP / (FP + TN))
    fnr: float  # False Negative Rate (FN / (FN + TP))
    specificity: float  # TN / (TN + FP)
    f1_score: float


@dataclass
class AlertFatigueAndCostReport:
    """Operational alert fatigue and financial cost-utility audit."""

    threshold: float
    daily_transactions: int
    daily_alerts_generated: int
    false_positive_alerts_daily: int
    legitimate_customers_impacted_per_10k: float
    estimated_daily_fraud_loss_dollars: float
    estimated_daily_investigation_cost_dollars: float
    total_daily_cost_dollars: float
    optimal_cost_threshold: float
    minimized_total_cost_dollars: float


def compute_multi_threshold_confusion_matrix(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    thresholds: list[float] | None = None,
) -> list[ConfusionMatrixAtThreshold]:
    """Compute confusion matrices across multiple operational decision thresholds."""
    y_t = np.asarray(y_true).astype(int)
    y_p = np.asarray(y_pred)
    thresh_list = thresholds or [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

    results: list[ConfusionMatrixAtThreshold] = []
    for th in thresh_list:
        pred_binary = (y_p >= th).astype(int)
        tp = int(np.sum((pred_binary == 1) & (y_t == 1)))
        fp = int(np.sum((pred_binary == 1) & (y_t == 0)))
        tn = int(np.sum((pred_binary == 0) & (y_t == 0)))
        fn = int(np.sum((pred_binary == 0) & (y_t == 1)))

        precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        fpr = (fp / (fp + tn)) if (fp + tn) > 0 else 0.0
        fnr = (fn / (fn + tp)) if (fn + tp) > 0 else 0.0
        specificity = (tn / (tn + fp)) if (tn + fp) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        results.append(
            ConfusionMatrixAtThreshold(
                threshold=round(th, 2),
                true_positives=tp,
                false_positives=fp,
                true_negatives=tn,
                false_negatives=fn,
                precision=round(precision, 4),
                recall=round(recall, 4),
                fpr=round(fpr, 6),
                fnr=round(fnr, 6),
                specificity=round(specificity, 4),
                f1_score=round(f1, 4),
            )
        )
    return results


def compute_financial_cost_utility(
    y_true: list[int] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    operational_threshold: float = 0.5,
    daily_volume: int = 100_000,
    cost_missed_fraud_fn: float = 850.0,
    cost_false_alarm_fp: float = 18.0,
    cost_investigation_tp: float = 6.0,
) -> AlertFatigueAndCostReport:
    """Calculate operational alert fatigue and financial cost-utility matrix.

    Parameters
    ----------
    cost_missed_fraud_fn : float
        Average unrecovered dollar loss per missed fraudulent transaction (FN).
    cost_false_alarm_fp : float
        Cost of false alert (customer friction, SMS OTP verification, ops triage).
    cost_investigation_tp : float
        Analyst triage cost for confirmed true positive SAR filing.
    """
    y_t = np.asarray(y_true).astype(int)
    y_p = np.asarray(y_pred)
    n = max(1, len(y_t))
    scale_factor = daily_volume / n

    # Scan 50 thresholds to find the cost-optimal decision threshold
    test_thresholds = np.linspace(0.05, 0.95, 50)
    min_cost = float("inf")
    best_th = operational_threshold

    for th in test_thresholds:
        pred_b = (y_p >= th).astype(int)
        tp = float(np.sum((pred_b == 1) & (y_t == 1))) * scale_factor
        fp = float(np.sum((pred_b == 1) & (y_t == 0))) * scale_factor
        fn = float(np.sum((pred_b == 0) & (y_t == 1))) * scale_factor
        c = (fn * cost_missed_fraud_fn) + (fp * cost_false_alarm_fp) + (tp * cost_investigation_tp)
        if c < min_cost:
            min_cost = c
            best_th = float(th)

    # Current operational threshold values
    pred_op = (y_p >= operational_threshold).astype(int)
    tp_op = float(np.sum((pred_op == 1) & (y_t == 1))) * scale_factor
    fp_op = float(np.sum((pred_op == 1) & (y_t == 0))) * scale_factor
    fn_op = float(np.sum((pred_op == 0) & (y_t == 1))) * scale_factor

    fraud_loss = fn_op * cost_missed_fraud_fn
    inv_cost = (fp_op * cost_false_alarm_fp) + (tp_op * cost_investigation_tp)
    total_cost = fraud_loss + inv_cost

    tn_op = float(np.sum((pred_op == 0) & (y_t == 0))) * scale_factor
    fpr_op = (fp_op / (fp_op + tn_op)) if (fp_op + tn_op) > 0 else 0.0
    impacted_per_10k = round(fpr_op * 10_000, 2)

    return AlertFatigueAndCostReport(
        threshold=round(operational_threshold, 2),
        daily_transactions=daily_volume,
        daily_alerts_generated=int(tp_op + fp_op),
        false_positive_alerts_daily=int(fp_op),
        legitimate_customers_impacted_per_10k=impacted_per_10k,
        estimated_daily_fraud_loss_dollars=round(fraud_loss, 2),
        estimated_daily_investigation_cost_dollars=round(inv_cost, 2),
        total_daily_cost_dollars=round(total_cost, 2),
        optimal_cost_threshold=round(best_th, 2),
        minimized_total_cost_dollars=round(min_cost, 2),
    )


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
    y_t = np.asarray(y_true)
    y_p = np.asarray(y_pred)

    roc_score = safe_roc_auc_score(y_t, y_p)
    roc_auc = round(roc_score, 4) if roc_score is not None else None
    pr_score = compute_pr_auc(y_t, y_p)
    pr_auc = round(pr_score, 4) if pr_score is not None else None
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
