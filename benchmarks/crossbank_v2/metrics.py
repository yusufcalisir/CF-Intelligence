"""Statistically Defensible Metrics and Validation-Threshold Protocol for CrossBank v2.

Enforces:
1. Threshold selection strictly on VALIDATION, never on TEST.
2. Scenario metrics computed exclusively on scenario members (zero global metric copy).
3. Explicit separation of transactions, incidents, and synthetic exposure.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from benchmarks.crossbank_v2.config import ScenarioConfig
from benchmarks.crossbank_v2.schema import (
    ConfusionMatrixCounts,
    EvaluationMetrics,
    LowFPRResolution,
    ScenarioMetricResult,
    ThresholdProvenance,
)


def compute_low_fpr_resolution(
    y_test: Any,
    target_fpr: float = 0.001,
) -> LowFPRResolution:
    """Compute mathematical resolution of requested target FPR operating point."""
    y_arr = np.asarray(y_test)
    n_neg = int(np.sum(y_arr == 0))
    min_fpr = float(1.0 / max(1, n_neg))
    achievable = n_neg >= int(1.0 / target_fpr)
    allowed_fp = int(target_fpr * n_neg)

    return LowFPRResolution(
        target_fpr=target_fpr,
        test_negative_count=n_neg,
        minimum_nonzero_fpr=round(min_fpr, 6),
        is_target_fpr_achievable=achievable,
        allowed_fp_at_target=allowed_fp,
    )


def select_threshold_on_validation(
    y_val: Any,
    val_scores: Any,
    target_fpr: float = 0.001,
) -> tuple[float, float]:
    """Derive decision threshold exclusively from VALIDATION split.

    Returns:
        (chosen_threshold, achieved_validation_fpr)
    """
    y_val_arr = np.asarray(y_val, dtype=int)
    scores_arr = np.asarray(val_scores, dtype=float)

    n_neg = int(np.sum(y_val_arr == 0))
    if n_neg == 0 or len(scores_arr) == 0:
        return 0.50, 0.0

    order = np.argsort(scores_arr)[::-1]
    sorted_labels = y_val_arr[order]
    sorted_scores = scores_arr[order]

    cum_fp = np.cumsum(sorted_labels == 0)
    fpr_curve = cum_fp / n_neg

    valid_indices = np.where(fpr_curve <= target_fpr)[0]
    if len(valid_indices) == 0:
        # If even the highest scoring sample produces FPR > target, set threshold above max score
        return float(sorted_scores[0] + 1e-4), 0.0

    best_idx = valid_indices[-1]
    chosen_threshold = float(sorted_scores[best_idx])
    achieved_val_fpr = float(fpr_curve[best_idx])
    return chosen_threshold, achieved_val_fpr


def compute_comprehensive_metrics(
    y_val: Any,
    val_scores: Any,
    y_test: Any,
    test_scores: Any,
    target_fpr: float = 0.001,
) -> EvaluationMetrics:
    """Evaluate performance strictly using validation-selected threshold."""
    y_test_arr = np.asarray(y_test, dtype=int)
    test_scores_arr = np.asarray(test_scores, dtype=float)

    # 1. Select threshold on validation
    threshold, val_fpr = select_threshold_on_validation(
        y_val, val_scores, target_fpr=target_fpr
    )

    # 2. Compute continuous curve metrics on test
    try:
        if len(np.unique(y_test_arr)) > 1 and np.sum(y_test_arr == 1) > 0:
            ap = float(average_precision_score(y_test_arr, test_scores_arr))
            if np.isnan(ap):
                ap = 0.0
        else:
            ap = 0.0
    except Exception:
        ap = 0.0

    try:
        if len(np.unique(y_test_arr)) > 1:
            auc = float(roc_auc_score(y_test_arr, test_scores_arr))
            if np.isnan(auc):
                auc = 0.5
        else:
            auc = 0.5
    except Exception:
        auc = 0.5

    # 3. Apply frozen threshold to test
    y_pred = (test_scores_arr >= threshold).astype(int)

    tp = int(np.sum((y_pred == 1) & (y_test_arr == 1)))
    fp = int(np.sum((y_pred == 1) & (y_test_arr == 0)))
    tn = int(np.sum((y_pred == 0) & (y_test_arr == 0)))
    fn = int(np.sum((y_pred == 0) & (y_test_arr == 1)))

    n_pos = tp + fn
    n_neg = tn + fp

    test_rec = float(tp / n_pos) if n_pos > 0 else 0.0
    test_prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    test_fpr = float(fp / n_neg) if n_neg > 0 else 0.0
    f1 = float(2 * test_prec * test_rec / (test_prec + test_rec)) if (test_prec + test_rec) > 0 else 0.0

    cm = ConfusionMatrixCounts(tp=tp, fp=fp, tn=tn, fn=fn)
    prov = ThresholdProvenance(
        threshold_value=round(threshold, 6),
        threshold_source_split="VALIDATION",
        target_validation_fpr=target_fpr,
        achieved_validation_fpr=round(val_fpr, 6),
        achieved_test_fpr=round(test_fpr, 6),
        achieved_test_precision=round(test_prec, 4),
        achieved_test_recall=round(test_rec, 4),
    )

    return EvaluationMetrics(
        average_precision=round(ap, 4),
        roc_auc=round(auc, 4),
        recall_at_validation_fpr=round(test_rec, 4),
        precision_at_validation_fpr=round(test_prec, 4),
        f1_at_validation_fpr=round(f1, 4),
        threshold_provenance=prov,
        confusion_matrix=cm,
    )


def compute_scenario_specific_metrics(
    df_test: pd.DataFrame,
    test_scores: Any,
    threshold: float,
    scenarios: tuple[ScenarioConfig, ...],
) -> dict[str, ScenarioMetricResult]:
    """Calculate metrics strictly for observations belonging to each scenario.

    Fixes the historical defect where global AP was copied across all scenario records.
    """
    scores_arr = np.asarray(test_scores, dtype=float)
    df_eval = df_test.copy()
    df_eval["score"] = scores_arr
    df_eval["pred"] = (df_eval["score"] >= threshold).astype(int)

    results: dict[str, ScenarioMetricResult] = {}

    for sc in scenarios:
        sc_id = sc.scenario_id
        sc_txns = df_eval[df_eval["scenario_id"] == sc_id]
        n_tx = len(sc_txns)

        if n_tx == 0:
            results[sc_id] = ScenarioMetricResult(
                scenario_id=sc_id,
                scenario_title=sc.title,
                typology=sc.typology,
                test_transaction_count=0,
                test_incident_count=0,
                test_positive_count=0,
                detected_transaction_count=0,
                detected_incident_count=0,
                transaction_detection_rate=0.0,
                incident_detection_rate=0.0,
                synthetic_total_exposure_usd=0.0,
                synthetic_detected_exposure_usd=0.0,
                synthetic_missed_exposure_usd=0.0,
            )
            continue

        n_pos = int(sc_txns["is_laundering"].sum())
        det_tx = ((sc_txns["pred"] == 1) & (sc_txns["is_laundering"] == 1)).sum()
        tx_det_rate = float(det_tx / n_pos) if n_pos > 0 else 0.0

        # Incident-level evaluation:
        # An incident is detected iff AT LEAST ONE of its transactions exceeds threshold
        pos_sc = sc_txns[sc_txns["is_laundering"] == 1]
        incidents = [x for x in np.unique(pos_sc["incident_id"]) if pd.notna(x)]
        n_incidents = len(incidents)
        det_incidents = 0
        for inc_id in incidents:
            inc_subset = sc_txns[sc_txns["incident_id"] == inc_id]
            if (inc_subset["pred"] == 1).any():
                det_incidents += 1

        inc_det_rate = float(det_incidents / n_incidents) if n_incidents > 0 else 0.0

        # Synthetic exposure accounting (fraud transactions in scenario)
        pos_txns = sc_txns[sc_txns["is_laundering"] == 1]
        total_vol = float(pos_txns["amount"].sum())
        det_vol = float(pos_txns[pos_txns["pred"] == 1]["amount"].sum())
        missed_vol = float(pos_txns[pos_txns["pred"] == 0]["amount"].sum())

        results[sc_id] = ScenarioMetricResult(
            scenario_id=sc_id,
            scenario_title=sc.title,
            typology=sc.typology,
            test_transaction_count=n_tx,
            test_incident_count=n_incidents,
            test_positive_count=n_pos,
            detected_transaction_count=det_tx,
            detected_incident_count=det_incidents,
            transaction_detection_rate=round(tx_det_rate, 4),
            incident_detection_rate=round(inc_det_rate, 4),
            synthetic_total_exposure_usd=round(total_vol, 2),
            synthetic_detected_exposure_usd=round(det_vol, 2),
            synthetic_missed_exposure_usd=round(missed_vol, 2),
        )

    return results
