"""Stateful experiment execution tracker and context manager.

Handles timing, hardware probe, git provenance discovery, step metrics logging,
evaluation curve synthesis, and automated artifact export.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    auc,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

from experiments.harness.exporter import ExperimentExporter
from experiments.harness.schema import (
    CalibrationData,
    ConfusionMatrixData,
    CurvePoint,
    DatasetMetadata,
    ExperimentConfig,
    ExperimentResult,
    HardwareMetadata,
    StepMetric,
)


def _discover_git_info() -> tuple[str, str]:
    """Discover current git commit SHA-1 and branch name."""
    commit = os.getenv("GIT_COMMIT", "").strip()
    branch = os.getenv("GIT_BRANCH", "main").strip()

    if not commit:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                commit = res.stdout.strip()
        except Exception:
            pass

    if not commit:
        commit = "uncommitted_development_snapshot"

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            branch = res.stdout.strip()
    except Exception:
        pass

    return commit, branch


def _decimate_curve(x: Sequence[float], y: Sequence[float], max_points: int = 100) -> tuple[list[float], list[float]]:
    """Decimate curve coordinates to max_points while preserving endpoints."""
    if len(x) <= max_points:
        return [round(float(v), 6) for v in x], [round(float(v), 6) for v in y]
    indices = np.linspace(0, len(x) - 1, max_points, dtype=int)
    dec_x = [round(float(x[i]), 6) for i in indices]
    dec_y = [round(float(y[i]), 6) for i in indices]
    return dec_x, dec_y


class ExperimentTracker:
    """Context manager for tracking an experiment run from configuration to artifacts."""

    def __init__(
        self,
        config: ExperimentConfig,
        dataset: DatasetMetadata,
        auto_export: bool = True,
    ):
        self.config = config
        self.dataset = dataset
        self.auto_export = auto_export
        self.exporter = ExperimentExporter(output_root=config.output_dir)

        self.hardware: HardwareMetadata = HardwareMetadata.capture()
        self.git_commit, self.git_branch = _discover_git_info()

        self.start_time_utc: str = ""
        self.end_time_utc: str = ""
        self._start_perf: float = 0.0
        self._end_perf: float = 0.0

        self.history: list[StepMetric] = []
        self.final_metrics: dict[str, float] = {}
        self.curves: CurvePoint | None = None
        self.confusion_matrix: ConfusionMatrixData | None = None
        self.calibration: CalibrationData | None = None
        self.status: str = "PENDING"
        self._step_start_perf: float = 0.0

    def start(self) -> ExperimentTracker:
        """Mark start of execution."""
        self.status = "RUNNING"
        self.start_time_utc = datetime.now(UTC).isoformat()
        self._start_perf = time.perf_counter()
        self._step_start_perf = self._start_perf
        return self

    def __enter__(self) -> ExperimentTracker:
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.status = "FAILED"
            self.finalize()
            return
        self.status = "COMPLETED"
        self.finalize()

    def log_step(
        self,
        step: int,
        train_loss: float | None = None,
        val_loss: float | None = None,
        pr_auc: float | None = None,
        roc_auc: float | None = None,
        accuracy: float | None = None,
        f1_score: float | None = None,
        precision: float | None = None,
        recall: float | None = None,
        **extra: Any,
    ) -> StepMetric:
        """Record step-by-step training and validation performance metrics."""
        now_perf = time.perf_counter()
        duration = now_perf - self._step_start_perf if self._step_start_perf else 0.0
        self._step_start_perf = now_perf

        metric = StepMetric(
            step=step,
            train_loss=round(train_loss, 6) if train_loss is not None else None,
            val_loss=round(val_loss, 6) if val_loss is not None else None,
            pr_auc=round(pr_auc, 6) if pr_auc is not None else None,
            roc_auc=round(roc_auc, 6) if roc_auc is not None else None,
            accuracy=round(accuracy, 6) if accuracy is not None else None,
            f1_score=round(f1_score, 6) if f1_score is not None else None,
            precision=round(precision, 6) if precision is not None else None,
            recall=round(recall, 6) if recall is not None else None,
            duration_seconds=round(duration, 4),
            extra=extra,
        )
        self.history.append(metric)

        # Update latest observed evaluation metrics as running final metrics
        if pr_auc is not None:
            self.final_metrics["pr_auc"] = metric.pr_auc
        if roc_auc is not None:
            self.final_metrics["roc_auc"] = metric.roc_auc
        if f1_score is not None:
            self.final_metrics["f1_score"] = metric.f1_score

        return metric

    def set_evaluation_predictions(
        self,
        y_true: Sequence[int] | np.ndarray,
        y_prob: Sequence[float] | np.ndarray,
        y_pred: Sequence[int] | np.ndarray | None = None,
        threshold: float = 0.5,
    ) -> dict[str, float]:
        """Compute ROC, PR, calibration, and confusion matrix curves from evaluation arrays."""
        y_t = np.asarray(y_true, dtype=int)
        y_p = np.asarray(y_prob, dtype=float)
        y_pred = (y_p >= threshold).astype(int) if y_pred is None else np.asarray(y_pred, dtype=int)

        # 1. Scalar summary metrics
        roc = float(roc_auc_score(y_t, y_p)) if len(np.unique(y_t)) > 1 else 0.0
        prec_arr, rec_arr, _ = precision_recall_curve(y_t, y_p)
        # Compute Area Under Precision-Recall Curve via Trapezoidal rule
        pr = float(auc(rec_arr, prec_arr))
        f1 = float(f1_score(y_t, y_pred, zero_division=0))
        prec = float(precision_score(y_t, y_pred, zero_division=0))
        rec = float(recall_score(y_t, y_pred, zero_division=0))
        brier = float(brier_score_loss(y_t, y_p))

        self.final_metrics.update({
            "roc_auc": round(roc, 6),
            "pr_auc": round(pr, 6),
            "f1_score": round(f1, 6),
            "precision": round(prec, 6),
            "recall": round(rec, 6),
            "brier_score": round(brier, 6),
        })

        # 2. ROC curve points
        fpr_arr, tpr_arr, _ = roc_curve(y_t, y_p)
        dec_fpr, dec_tpr = _decimate_curve(fpr_arr, tpr_arr, max_points=100)
        dec_rec, dec_prec = _decimate_curve(rec_arr, prec_arr, max_points=100)

        self.curves = CurvePoint(
            fpr=dec_fpr,
            tpr=dec_tpr,
            precision=dec_prec,
            recall=dec_rec,
        )

        # 3. Confusion Matrix
        cm = confusion_matrix(y_t, y_pred, labels=[0, 1])
        if cm.shape == (2, 2):
            self.confusion_matrix = ConfusionMatrixData(
                tn=int(cm[0, 0]),
                fp=int(cm[0, 1]),
                fn=int(cm[1, 0]),
                tp=int(cm[1, 1]),
            )

        # 4. Calibration Curve
        try:
            prob_true, prob_pred = calibration_curve(y_t, y_p, n_bins=10, strategy="uniform")
            self.calibration = CalibrationData(
                prob_true=[round(float(v), 6) for v in prob_true],
                prob_pred=[round(float(v), 6) for v in prob_pred],
                brier_score=round(brier, 6),
            )
        except Exception:
            pass

        return self.final_metrics

    def finalize(self) -> ExperimentResult:
        """Wrap up execution, compute duration, and optionally export artifacts."""
        if not self.end_time_utc:
            self.end_time_utc = datetime.now(UTC).isoformat()
            self._end_perf = time.perf_counter()

        duration = max(0.001, self._end_perf - self._start_perf)

        result = ExperimentResult(
            experiment_id=self.config.experiment_id,
            config=self.config,
            hardware=self.hardware,
            dataset=self.dataset,
            git_commit=self.git_commit,
            git_branch=self.git_branch,
            status=self.status,
            start_time_utc=self.start_time_utc,
            end_time_utc=self.end_time_utc,
            total_duration_seconds=round(duration, 4),
            final_metrics=self.final_metrics,
            history=self.history,
            curves=self.curves,
            confusion_matrix=self.confusion_matrix,
            calibration=self.calibration,
        )

        if self.auto_export:
            self.exporter.export_all(result)

        return result
