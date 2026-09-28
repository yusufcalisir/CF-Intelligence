"""Temporal Generalization & Out-of-Time Degradation Quantification Benchmark.

Evaluates financial fraud models across chronological regimes:
1. Past-Present-Future Split Protocol (Period 1 vs Period 2 vs Period 3)
2. Optimistic Random K-Fold Cross-Validation vs Strict Out-of-Time Degradation
3. Population Stability Index (PSI) & Kolmogorov-Smirnov Feature Drift Tracking
4. Probability Calibration (ECE, Brier Score) Decay Analysis
5. Automated Model Retraining Urgency Trigger Formulation
"""

from __future__ import annotations

import json
import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats  # type: ignore[import-untyped]
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import StratifiedKFold
from torch.utils.data import DataLoader, TensorDataset

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Pydantic Schemas
# ---------------------------------------------------------------------------

class TemporalSplitProtocol(StrEnum):
    """Chronological split phases in financial stream lifecycle."""

    PERIOD_1_PAST = "PERIOD_1_PAST"
    PERIOD_2_PRESENT = "PERIOD_2_PRESENT"
    PERIOD_3_FUTURE = "PERIOD_3_FUTURE"


class PeriodMetrics(BaseModel):
    """Evaluation metrics for a specific temporal evaluation window."""

    model_config = ConfigDict(frozen=True)

    period_name: str = Field(..., description="Human-readable period descriptor")
    protocol: TemporalSplitProtocol = Field(..., description="Chronological protocol phase")
    timestamp_range: tuple[float, float] = Field(..., description="(t_min, t_max) bounds")
    sample_count: int = Field(..., description="Total samples in partition")
    fraud_count: int = Field(..., description="Total positive fraud samples")
    fraud_rate: float = Field(..., description="Empirical fraud prevalence")
    pr_auc: float = Field(..., description="Area under Precision-Recall Curve")
    roc_auc: float = Field(..., description="Area under ROC Curve")
    f1_score: float = Field(..., description="F1-Score at 0.50 cutoff")
    recall_at_01_fpr: float = Field(..., description="Recall at 0.1% strict false positive rate")
    recall_at_05_fpr: float = Field(..., description="Recall at 0.5% false positive rate")
    recall_at_10_fpr: float = Field(..., description="Recall at 1.0% false positive rate")
    brier_score: float = Field(..., description="Brier score (mean squared calibration error)")
    ece: float = Field(..., description="Expected Calibration Error across 10 bins")


class KFoldMetrics(BaseModel):
    """Metrics achieved under optimistic (temporally-leaky) randomized K-fold CV."""

    model_config = ConfigDict(frozen=True)

    num_folds: int = Field(5, description="Number of cross-validation folds")
    pr_auc_mean: float = Field(..., description="Mean PR-AUC across folds")
    pr_auc_std: float = Field(..., description="Standard deviation of PR-AUC across folds")
    roc_auc_mean: float = Field(..., description="Mean ROC-AUC across folds")
    roc_auc_std: float = Field(..., description="Standard deviation of ROC-AUC across folds")
    f1_mean: float = Field(..., description="Mean F1 score across folds")
    recall_at_01_fpr_mean: float = Field(..., description="Mean Recall @ 0.1% FPR")
    recall_at_01_fpr_std: float = Field(..., description="Standard deviation of Recall @ 0.1% FPR")


class FeatureDriftProfile(BaseModel):
    """Drift quantification for a single feature between Period 1 and subsequent periods."""

    model_config = ConfigDict(frozen=True)

    feature_name: str = Field(..., description="Feature identifier")
    p1_to_p2_ks_stat: float = Field(..., description="Kolmogorov-Smirnov statistic (P1 vs P2)")
    p1_to_p2_psi: float = Field(..., description="Population Stability Index (P1 vs P2)")
    p1_to_p3_ks_stat: float = Field(..., description="Kolmogorov-Smirnov statistic (P1 vs P3)")
    p1_to_p3_psi: float = Field(..., description="Population Stability Index (P1 vs P3)")
    drift_status: str = Field(..., description="Drift classification: STABLE, MODERATE, SEVERE")


class TemporalDegradationMetrics(BaseModel):
    """Comparative degradation deltas and bias quantification."""

    model_config = ConfigDict(frozen=True)

    # In-Period vs OOT Present (P1 -> P2)
    p1_to_p2_pr_auc_delta: float = Field(..., description="PR-AUC change from P1 test to P2")
    p1_to_p2_relative_decay_pct: float = Field(..., description="Percentage PR-AUC loss (P1 -> P2)")
    p1_to_p2_roc_auc_delta: float = Field(..., description="ROC-AUC change from P1 test to P2")

    # In-Period vs OOT Future (P1 -> P3)
    p1_to_p3_pr_auc_delta: float = Field(..., description="PR-AUC change from P1 test to P3")
    p1_to_p3_relative_decay_pct: float = Field(..., description="Percentage PR-AUC loss (P1 -> P3)")
    p1_to_p3_roc_auc_delta: float = Field(..., description="ROC-AUC change from P1 test to P3")
    p1_to_p3_recall_01_delta_pp: float = Field(..., description="Percentage points loss in Recall @ 0.1% FPR")

    # Optimistic K-Fold Bias Gap
    optimistic_kfold_bias_pr_auc: float = Field(..., description="K-Fold PR-AUC minus P3 OOT PR-AUC")
    optimistic_kfold_bias_roc_auc: float = Field(..., description="K-Fold ROC-AUC minus P3 OOT ROC-AUC")

    # Retraining Governance
    retraining_recommended: bool = Field(..., description="Whether degradation exceeds retraining threshold")
    retraining_urgency: str = Field(..., description="NONE, MODERATE, or CRITICAL")
    max_feature_psi: float = Field(..., description="Maximum feature PSI observed across P1 -> P3")


class TemporalGeneralizationConfig(BaseModel):
    """Configuration parameters for the temporal generalization experiment."""

    model_config = ConfigDict(frozen=True)

    dataset_name: str = Field(
        default="Synthetic Financial Transaction Stream",
        description="Dataset name or identifier",
    )
    num_samples_per_period: int = Field(
        default=2500,
        ge=200,
        description="Number of transactions generated per temporal period",
    )
    num_features: int = Field(
        default=12,
        ge=4,
        description="Number of transaction behavioral features",
    )
    base_fraud_rate: float = Field(
        default=0.04,
        gt=0.0,
        lt=0.5,
        description="Underlying fraud prevalence in Period 1",
    )
    drift_intensity: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Magnitude of concept drift in fraudster behavior across periods",
    )
    k_folds: int = Field(
        default=5,
        ge=2,
        le=10,
        description="Number of folds for randomized cross-validation baseline",
    )
    random_seed: int = Field(
        default=42,
        description="Deterministic PRNG seed for reproducibility",
    )
    hidden_dim: int = Field(
        default=64,
        ge=16,
        description="MLP hidden layer dimension",
    )
    epochs: int = Field(
        default=15,
        ge=1,
        description="Training epochs on Period 1",
    )
    learning_rate: float = Field(
        default=0.01,
        gt=0.0,
        description="Adam optimizer learning rate",
    )


class TemporalGeneralizationSuiteResult(BaseModel):
    """Consolidated serializable benchmark result schema."""

    model_config = ConfigDict(frozen=True)

    config: TemporalGeneralizationConfig
    in_period_p1: PeriodMetrics
    oot_period_2: PeriodMetrics
    oot_period_3: PeriodMetrics
    optimistic_kfold: KFoldMetrics
    degradation: TemporalDegradationMetrics
    feature_drift_profiles: list[FeatureDriftProfile]
    executive_summary: str


# ---------------------------------------------------------------------------
# Statistical & Metric Calculation Helpers
# ---------------------------------------------------------------------------

def calculate_recall_at_fixed_fpr(
    y_true: np.ndarray | Any,
    y_pred: np.ndarray | Any,
    target_fpr: float,
) -> float:
    """Calculate empirical Recall at a strict maximum False Positive Rate cutoff."""
    if len(np.unique(y_true)) < 2:
        return 0.0

    fpr, tpr, _ = roc_curve(y_true, y_pred)
    valid_idx = np.where(fpr <= target_fpr)[0]
    if len(valid_idx) == 0:
        return 0.0
    return float(tpr[valid_idx[-1]])


def calculate_ece(
    y_true: np.ndarray | Any,
    y_prob: np.ndarray | Any,
    num_bins: int = 10,
) -> float:
    """Calculate Expected Calibration Error (ECE) across equal-width probability bins."""
    bin_boundaries = np.linspace(0.0, 1.0, num_bins + 1)
    ece = 0.0
    total_samples = len(y_true)

    if total_samples == 0:
        return 0.0

    for i in range(num_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]
        if i == num_bins - 1:
            in_bin = (y_prob >= bin_lower) & (y_prob <= bin_upper)
        else:
            in_bin = (y_prob >= bin_lower) & (y_prob < bin_upper)

        bin_count = np.sum(in_bin)
        if bin_count > 0:
            bin_acc = np.mean(y_true[in_bin])
            bin_conf = np.mean(y_prob[in_bin])
            ece += (bin_count / total_samples) * abs(bin_acc - bin_conf)

    return float(ece)


def compute_psi(
    expected: np.ndarray | Any,
    actual: np.ndarray | Any,
    num_bins: int = 10,
) -> float:
    """Compute Population Stability Index (PSI) with Laplace quantile smoothing."""
    expected_valid = expected[np.isfinite(expected)]
    actual_valid = actual[np.isfinite(actual)]

    if len(expected_valid) < 30 or len(actual_valid) < 30:
        return 0.0

    quantiles = np.linspace(0, 100, num_bins + 1)
    bins = np.percentile(expected_valid, quantiles)
    bins = np.unique(bins)
    if len(bins) < 2:
        min_v = float(min(expected_valid.min(), actual_valid.min()))
        max_v = float(max(expected_valid.max(), actual_valid.max()))
        if min_v == max_v:
            max_v += 1e-4
        bins = np.linspace(min_v, max_v, num_bins + 1)

    expected_counts, _ = np.histogram(expected_valid, bins=bins)
    actual_counts, _ = np.histogram(actual_valid, bins=bins)

    expected_pct = (expected_counts + 1e-4) / (len(expected_valid) + 1e-4 * len(expected_counts))
    actual_pct = (actual_counts + 1e-4) / (len(actual_valid) + 1e-4 * len(actual_counts))

    psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))
    return float(max(0.0, psi))


def calculate_feature_drift(
    reference_X: np.ndarray | Any,
    target_X: np.ndarray | Any,
    target_p3_X: np.ndarray | Any,
    feature_names: list[str],
) -> list[FeatureDriftProfile]:
    """Calculate Kolmogorov-Smirnov 2-sample tests and PSI for each feature."""
    profiles: list[FeatureDriftProfile] = []

    for idx, name in enumerate(feature_names):
        ref_feat = reference_X[:, idx]
        p2_feat = target_X[:, idx]
        p3_feat = target_p3_X[:, idx]

        # KS statistics
        ks_res_p2: Any = stats.ks_2samp(ref_feat, p2_feat)
        ks_res_p3: Any = stats.ks_2samp(ref_feat, p3_feat)
        ks_p2 = float(ks_res_p2.statistic)
        ks_p3 = float(ks_res_p3.statistic)

        # PSI
        psi_p2 = compute_psi(ref_feat, p2_feat)
        psi_p3 = compute_psi(ref_feat, p3_feat)

        # Status classification based on industry PSI conventions (<0.10 stable, 0.10-0.25 moderate, >0.25 severe)
        if psi_p3 >= 0.25:
            status = "SEVERE_DRIFT"
        elif psi_p3 >= 0.10 or psi_p2 >= 0.10:
            status = "MODERATE_DRIFT"
        else:
            status = "STABLE"

        profiles.append(
            FeatureDriftProfile(
                feature_name=name,
                p1_to_p2_ks_stat=round(ks_p2, 4),
                p1_to_p2_psi=round(psi_p2, 4),
                p1_to_p3_ks_stat=round(ks_p3, 4),
                p1_to_p3_psi=round(psi_p3, 4),
                drift_status=status,
            )
        )

    return profiles


# ---------------------------------------------------------------------------
# Synthetic Temporal Stream Generator
# ---------------------------------------------------------------------------

CANONICAL_FEATURE_NAMES = [
    "amount_normalized",
    "velocity_1h",
    "velocity_24h",
    "country_corridor_risk",
    "merchant_category_risk",
    "device_trust_score",
    "time_since_last_tx",
    "cross_border_flag",
    "channel_risk_index",
    "balance_depletion_ratio",
    "atm_burst_score",
    "ip_geolocation_distance",
]


def generate_temporal_stream(
    config: TemporalGeneralizationConfig,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Generate 3 consecutive chronological periods of financial transactions.

    - Legitimate transactions maintain stable behavioral distributions.
    - Fraudulent transactions undergo structured concept drift across periods:
      * Period 1: Standard high-amount and burst-velocity attacks.
      * Period 2: Structuring drift (sub-threshold micro-amounts to evade alerts).
      * Period 3: Channel-shifting and geolocation spoofing evasion attacks.
    """
    rng = np.random.default_rng(config.random_seed)
    N = config.num_samples_per_period
    d = config.num_features
    drift = config.drift_intensity

    # Feature names
    feature_names = CANONICAL_FEATURE_NAMES[:d]

    periods_data = []
    for period_idx, t_offset in [(1, 0.0), (2, 100.0), (3, 200.0)]:
        # Assign continuous timestamps
        timestamps = rng.uniform(t_offset, t_offset + 100.0, size=N)
        timestamps = np.sort(timestamps)

        # Baseline fraud labels
        y = (rng.uniform(0.0, 1.0, size=N) < config.base_fraud_rate).astype(np.int64)

        # Ensure at least 5 positive samples exist for statistical safety
        if np.sum(y == 1) < 5:
            force_idx = rng.choice(N, size=5, replace=False)
            y[force_idx] = 1

        # Legitimate features: stable Gaussian with mild macro trend (e.g. seasonal drift)
        macro_shift = 0.08 * (period_idx - 1)
        X_legit = rng.normal(loc=macro_shift, scale=1.0, size=(N, d))

        # Fraud features: realistic overlap with legitimate distribution + concept drift
        # Base fraud features in Period 1: moderate separation with realistic overlap
        base_fraud_loc = np.array([
            1.20,  # amount_normalized (higher initially)
            1.10,  # velocity_1h (higher initially)
            0.80,  # velocity_24h
            0.90,  # country_corridor_risk
            0.75,  # merchant_category_risk
            -0.80,  # device_trust_score (lower trust)
            -0.60,  # time_since_last_tx (burst)
            1.00,  # cross_border_flag
            0.85,  # channel_risk_index
            0.70,  # balance_depletion_ratio
            0.60,  # atm_burst_score
            0.50,  # ip_geolocation_distance
        ][:d], dtype=np.float32)

        # Concept drift perturbations across periods
        drift_loc = base_fraud_loc.copy()
        if period_idx == 2:
            # Period 2: Structuring drift (fraudsters reduce amounts to evade static rules, increase velocity)
            drift_loc[0] -= 1.40 * drift  # lower amounts (closer to legit)
            drift_loc[1] += 1.00 * drift  # increased rapid attempts
            if d > 7:
                drift_loc[7] += 1.20 * drift  # cross border burst
            if d > 8:
                drift_loc[8] -= 0.50 * drift  # mimic standard channels
        elif period_idx == 3:
            # Period 3: Advanced evasion (micro-amounts, channel hopping, device spoofing)
            drift_loc[0] -= 1.90 * drift  # sub-threshold micro transfers
            drift_loc[1] -= 0.80 * drift  # slower paced to evade velocity rules
            if d > 5:
                drift_loc[5] += 0.90 * drift  # spoofed trusted device
            if d > 8:
                drift_loc[8] -= 1.10 * drift  # legitimate looking channels
            if d > 11:
                drift_loc[11] += 2.20 * drift  # proxy/VPN geolocation jumps

        X_fraud = rng.normal(loc=drift_loc, scale=1.15, size=(N, d))

        # Combine legit and fraud based on label y
        X = np.where(y[:, None] == 1, X_fraud, X_legit)

        # Standard min-max clipping to avoid mathematical infinities
        X = np.clip(X, -5.0, 10.0)

        periods_data.append({
            "X": X.astype(np.float32),
            "y": y,
            "timestamps": timestamps,
            "feature_names": feature_names,
            "period_idx": period_idx,
            "t_range": (float(timestamps[0]), float(timestamps[-1])),
        })

    return periods_data[0], periods_data[1], periods_data[2]


# ---------------------------------------------------------------------------
# Fast Feedforward Classifier
# ---------------------------------------------------------------------------

class _TemporalClassifierMLP(nn.Module):
    """Feedforward neural risk model for temporal evaluation."""

    def __init__(self, in_features: int, hidden_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_features, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.20),
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def _train_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    config: TemporalGeneralizationConfig,
) -> _TemporalClassifierMLP:
    """Train neural risk model strictly on designated training split."""
    torch.manual_seed(config.random_seed)
    in_dim = X_train.shape[1]
    model = _TemporalClassifierMLP(in_dim, config.hidden_dim)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, weight_decay=1e-4)

    # Calculate positive weight to balance class imbalance
    num_neg = np.sum(y_train == 0)
    num_pos = max(1, np.sum(y_train == 1))
    pos_weight = torch.tensor([float(num_neg / num_pos)], dtype=torch.float32)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    dataset = TensorDataset(
        torch.tensor(X_train, dtype=torch.float32),
        torch.tensor(y_train, dtype=torch.float32),
    )
    loader = DataLoader(dataset, batch_size=128, shuffle=True)

    model.train()
    for _ in range(config.epochs):
        for bx, by in loader:
            optimizer.zero_grad()
            logits = model(bx)
            loss = criterion(logits, by)
            loss.backward()
            optimizer.step()

    return model


def _predict_proba(model: _TemporalClassifierMLP, X: np.ndarray) -> np.ndarray:
    """Generate risk probabilities using calibrated sigmoid activation."""
    model.eval()
    with torch.no_grad():
        bx = torch.tensor(X, dtype=torch.float32)
        logits = model(bx)
        probs = torch.sigmoid(logits).cpu().numpy()
    return np.asarray(probs, dtype=float)


def _evaluate_partition(
    model: _TemporalClassifierMLP,
    X: np.ndarray,
    y: np.ndarray,
    period_name: str,
    protocol: TemporalSplitProtocol,
    t_range: tuple[float, float],
) -> PeriodMetrics:
    """Evaluate discrimination, precision-recall, and calibration metrics for a partition."""
    probs = _predict_proba(model, X)
    preds = (probs >= 0.50).astype(int)

    # Precision-Recall AUC & ROC-AUC
    if len(np.unique(y)) > 1:
        pr_auc = float(average_precision_score(y, probs))
        roc_auc = float(roc_auc_score(y, probs))
    else:
        pr_auc = float(np.mean(y == 1))
        roc_auc = 0.50

    zd: Any = 0
    f1 = float(f1_score(y, preds, zero_division=zd))
    rec_01 = calculate_recall_at_fixed_fpr(y, probs, target_fpr=0.001)
    rec_05 = calculate_recall_at_fixed_fpr(y, probs, target_fpr=0.005)
    rec_10 = calculate_recall_at_fixed_fpr(y, probs, target_fpr=0.010)
    brier = float(brier_score_loss(y, probs))
    ece = calculate_ece(y, probs, num_bins=10)

    sample_count = len(y)
    fraud_count = int(np.sum(y == 1))
    fraud_rate = float(fraud_count / max(1, sample_count))

    return PeriodMetrics(
        period_name=period_name,
        protocol=protocol,
        timestamp_range=(round(t_range[0], 2), round(t_range[1], 2)),
        sample_count=sample_count,
        fraud_count=fraud_count,
        fraud_rate=round(fraud_rate, 4),
        pr_auc=round(pr_auc, 4),
        roc_auc=round(roc_auc, 4),
        f1_score=round(f1, 4),
        recall_at_01_fpr=round(rec_01, 4),
        recall_at_05_fpr=round(rec_05, 4),
        recall_at_10_fpr=round(rec_10, 4),
        brier_score=round(brier, 4),
        ece=round(ece, 4),
    )


def _evaluate_kfold_cv(
    X_pooled: np.ndarray | Any,
    y_pooled: np.ndarray | Any,
    config: TemporalGeneralizationConfig,
) -> KFoldMetrics:
    """Execute standard randomized K-fold cross-validation (the optimistic baseline)."""
    skf = StratifiedKFold(n_splits=config.k_folds, shuffle=True, random_state=config.random_seed)

    pr_aucs: list[float] = []
    roc_aucs: list[float] = []
    f1s: list[float] = []
    rec_01s: list[float] = []

    for train_idx, test_idx in skf.split(X_pooled, y_pooled):
        X_tr, y_tr = X_pooled[train_idx], y_pooled[train_idx]
        X_te, y_te = X_pooled[test_idx], y_pooled[test_idx]

        model = _train_model(X_tr, y_tr, config)
        probs = _predict_proba(model, X_te)
        preds = (probs >= 0.50).astype(int)

        pr_aucs.append(float(average_precision_score(y_te, probs)))
        roc_aucs.append(float(roc_auc_score(y_te, probs)))
        zd: Any = 0
        f1s.append(float(f1_score(y_te, preds, zero_division=zd)))
        rec_01s.append(calculate_recall_at_fixed_fpr(y_te, probs, target_fpr=0.001))

    return KFoldMetrics(
        num_folds=config.k_folds,
        pr_auc_mean=round(float(np.mean(pr_aucs)), 4),
        pr_auc_std=round(float(np.std(pr_aucs)), 4),
        roc_auc_mean=round(float(np.mean(roc_aucs)), 4),
        roc_auc_std=round(float(np.std(roc_aucs)), 4),
        f1_mean=round(float(np.mean(f1s)), 4),
        recall_at_01_fpr_mean=round(float(np.mean(rec_01s)), 4),
        recall_at_01_fpr_std=round(float(np.std(rec_01s)), 4),
    )


# ---------------------------------------------------------------------------
# Main Benchmark Runner
# ---------------------------------------------------------------------------

def run_temporal_generalization_benchmark(
    config: TemporalGeneralizationConfig | None = None,
    output_dir: str | Path | None = None,
) -> TemporalGeneralizationSuiteResult:
    """Execute complete temporal generalization and degradation evaluation.

    1. Generates 3 chronological periods (Period 1 Past, Period 2 Present, Period 3 Future).
    2. Trains strictly on Period 1 Training Split (75% train / 25% test).
    3. Evaluates on Period 1 Test Split (In-Period baseline).
    4. Evaluates on Period 2 (Immediate Out-of-Time).
    5. Evaluates on Period 3 (Distant Out-of-Time).
    6. Runs 5-Fold Randomized Cross-Validation on pooled stream to quantify optimistic bias gap.
    7. Profiles feature-level drift (KS & PSI).
    8. Quantifies degradation deltas and automated retraining trigger urgency.
    """
    if config is None:
        config = TemporalGeneralizationConfig()

    logger.info(
        "Starting Temporal Generalization Benchmark: dataset=%s, N_per_period=%d, drift=%.2f",
        config.dataset_name,
        config.num_samples_per_period,
        config.drift_intensity,
    )

    # 1. Generate chronological periods
    p1_data, p2_data, p3_data = generate_temporal_stream(config)

    # 2. Split Period 1 chronologically / stratified (75% train / 25% test)
    rng = np.random.default_rng(config.random_seed)
    p1_N = len(p1_data["y"])
    p1_indices = np.arange(p1_N)

    # Stratified partition of Period 1
    pos_idx = p1_indices[p1_data["y"] == 1]
    neg_idx = p1_indices[p1_data["y"] == 0]
    rng.shuffle(pos_idx)
    rng.shuffle(neg_idx)

    split_pos = int(0.75 * len(pos_idx))
    split_neg = int(0.75 * len(neg_idx))

    p1_train_idx = np.concatenate([pos_idx[:split_pos], neg_idx[:split_neg]])
    p1_test_idx = np.concatenate([pos_idx[split_pos:], neg_idx[split_neg:]])
    rng.shuffle(p1_train_idx)
    rng.shuffle(p1_test_idx)

    X_p1_train, y_p1_train = p1_data["X"][p1_train_idx], p1_data["y"][p1_train_idx]
    X_p1_test, y_p1_test = p1_data["X"][p1_test_idx], p1_data["y"][p1_test_idx]

    # 3. Train model strictly on Period 1 Train Split
    model = _train_model(X_p1_train, y_p1_train, config)

    # 4. Evaluate In-Period (Period 1 Test Split)
    in_period_p1 = _evaluate_partition(
        model,
        X_p1_test,
        y_p1_test,
        period_name="Period 1 (In-Period Test / Past)",
        protocol=TemporalSplitProtocol.PERIOD_1_PAST,
        t_range=p1_data["t_range"],
    )

    # 5. Evaluate Immediate OOT (Period 2)
    oot_p2 = _evaluate_partition(
        model,
        p2_data["X"],
        p2_data["y"],
        period_name="Period 2 (Intermediate OOT / Present)",
        protocol=TemporalSplitProtocol.PERIOD_2_PRESENT,
        t_range=p2_data["t_range"],
    )

    # 6. Evaluate Distant OOT (Period 3)
    oot_p3 = _evaluate_partition(
        model,
        p3_data["X"],
        p3_data["y"],
        period_name="Period 3 (Distant OOT / Future)",
        protocol=TemporalSplitProtocol.PERIOD_3_FUTURE,
        t_range=p3_data["t_range"],
    )

    # 7. Evaluate Optimistic Randomized K-Fold CV
    X_pooled = np.concatenate([p1_data["X"], p2_data["X"], p3_data["X"]], axis=0)
    y_pooled = np.concatenate([p1_data["y"], p2_data["y"], p3_data["y"]], axis=0)
    optimistic_kfold = _evaluate_kfold_cv(X_pooled, y_pooled, config)

    # 8. Feature Drift Profiling
    feature_drift = calculate_feature_drift(
        reference_X=p1_data["X"],
        target_X=p2_data["X"],
        target_p3_X=p3_data["X"],
        feature_names=p1_data["feature_names"],
    )
    max_psi = float(max(p.p1_to_p3_psi for p in feature_drift)) if feature_drift else 0.0

    # 9. Degradation Deltas & Retraining Trigger
    p1_p2_pr_delta = round(oot_p2.pr_auc - in_period_p1.pr_auc, 4)
    p1_p2_decay_pct = round(
        ((oot_p2.pr_auc - in_period_p1.pr_auc) / max(1e-4, in_period_p1.pr_auc)) * 100.0,
        2,
    )
    p1_p2_roc_delta = round(oot_p2.roc_auc - in_period_p1.roc_auc, 4)

    p1_p3_pr_delta = round(oot_p3.pr_auc - in_period_p1.pr_auc, 4)
    p1_p3_decay_pct = round(
        ((oot_p3.pr_auc - in_period_p1.pr_auc) / max(1e-4, in_period_p1.pr_auc)) * 100.0,
        2,
    )
    p1_p3_roc_delta = round(oot_p3.roc_auc - in_period_p1.roc_auc, 4)
    p1_p3_rec01_delta = round((oot_p3.recall_at_01_fpr - in_period_p1.recall_at_01_fpr) * 100.0, 2)

    kfold_bias_pr = round(optimistic_kfold.pr_auc_mean - oot_p3.pr_auc, 4)
    kfold_bias_roc = round(optimistic_kfold.roc_auc_mean - oot_p3.roc_auc, 4)

    # Retraining Governance: triggers when PR-AUC drops by >15% relative or max PSI >= 0.25
    if p1_p3_decay_pct <= -15.0 or max_psi >= 0.25:
        urgency = "CRITICAL"
        recommends_retraining = True
    elif p1_p2_decay_pct <= -5.0 or max_psi >= 0.10:
        urgency = "MODERATE"
        recommends_retraining = True
    else:
        urgency = "NONE"
        recommends_retraining = False

    degradation = TemporalDegradationMetrics(
        p1_to_p2_pr_auc_delta=p1_p2_pr_delta,
        p1_to_p2_relative_decay_pct=p1_p2_decay_pct,
        p1_to_p2_roc_auc_delta=p1_p2_roc_delta,
        p1_to_p3_pr_auc_delta=p1_p3_pr_delta,
        p1_to_p3_relative_decay_pct=p1_p3_decay_pct,
        p1_to_p3_roc_auc_delta=p1_p3_roc_delta,
        p1_to_p3_recall_01_delta_pp=p1_p3_rec01_delta,
        optimistic_kfold_bias_pr_auc=kfold_bias_pr,
        optimistic_kfold_bias_roc_auc=kfold_bias_roc,
        retraining_recommended=recommends_retraining,
        retraining_urgency=urgency,
        max_feature_psi=round(max_psi, 4),
    )

    summary = (
        f"Temporal Out-of-Time Degradation Evaluation: In-Period P1 PR-AUC={in_period_p1.pr_auc:.4f} "
        f"degrades to P2={oot_p2.pr_auc:.4f} ({p1_p2_decay_pct:+.1f}%) and P3={oot_p3.pr_auc:.4f} "
        f"({p1_p3_decay_pct:+.1f}%). Optimistic K-Fold Cross-Validation yields {optimistic_kfold.pr_auc_mean:.4f}, "
        f"exhibiting an optimistic bias gap of +{kfold_bias_pr:.4f} PR-AUC due to forward temporal leakage. "
        f"Retraining recommendation: {urgency} (Max Feature PSI={max_psi:.4f})."
    )

    suite_result = TemporalGeneralizationSuiteResult(
        config=config,
        in_period_p1=in_period_p1,
        oot_period_2=oot_p2,
        oot_period_3=oot_p3,
        optimistic_kfold=optimistic_kfold,
        degradation=degradation,
        feature_drift_profiles=feature_drift,
        executive_summary=summary,
    )

    # 10. Serialize JSON Artifacts
    target_path = Path(output_dir) if output_dir else Path(__file__).parent / "temporal_generalization_results.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(suite_result.model_dump(), f, indent=2)

    logger.info("Temporal generalization results successfully serialized to %s", target_path)
    return suite_result


# ---------------------------------------------------------------------------
# Markdown Formatting Helpers
# ---------------------------------------------------------------------------

def format_temporal_benchmark_markdown(result: TemporalGeneralizationSuiteResult) -> str:
    """Format benchmark results into publication-grade GitHub Flavored Markdown tables."""
    p1 = result.in_period_p1
    p2 = result.oot_period_2
    p3 = result.oot_period_3
    kf = result.optimistic_kfold
    deg = result.degradation

    lines = [
        "### Chronological Out-of-Time Degradation vs Optimistic K-Fold Benchmark",
        "",
        "| Evaluation Regime | Temporal Window | PR-AUC | ROC-AUC | F1-Score | Recall @ 0.1% FPR | Brier Score | ECE | Temporal Delta vs P1 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        f"| **Optimistic Randomized 5-Fold CV** | Pooled (Leakage) | {kf.pr_auc_mean:.4f} +/- {kf.pr_auc_std:.4f} | {kf.roc_auc_mean:.4f} +/- {kf.roc_auc_std:.4f} | {kf.f1_mean:.4f} | {kf.recall_at_01_fpr_mean * 100:.2f}% | -- | -- | +{deg.optimistic_kfold_bias_pr_auc:.4f} (Bias Gap) |",
        f"| **Period 1: In-Period Test (Past)** | t in [{p1.timestamp_range[0]}, {p1.timestamp_range[1]}] | **{p1.pr_auc:.4f}** | **{p1.roc_auc:.4f}** | **{p1.f1_score:.4f}** | **{p1.recall_at_01_fpr * 100:.2f}%** | {p1.brier_score:.4f} | {p1.ece:.4f} | Baseline (0.0000) |",
        f"| **Period 2: Intermediate OOT (Present)** | t in [{p2.timestamp_range[0]}, {p2.timestamp_range[1]}] | {p2.pr_auc:.4f} | {p2.roc_auc:.4f} | {p2.f1_score:.4f} | {p2.recall_at_01_fpr * 100:.2f}% | {p2.brier_score:.4f} | {p2.ece:.4f} | {deg.p1_to_p2_pr_auc_delta:+.4f} ({deg.p1_to_p2_relative_decay_pct:+.1f}%) |",
        f"| **Period 3: Distant OOT (Future)** | t in [{p3.timestamp_range[0]}, {p3.timestamp_range[1]}] | {p3.pr_auc:.4f} | {p3.roc_auc:.4f} | {p3.f1_score:.4f} | {p3.recall_at_01_fpr * 100:.2f}% | {p3.brier_score:.4f} | {p3.ece:.4f} | {deg.p1_to_p3_pr_auc_delta:+.4f} ({deg.p1_to_p3_relative_decay_pct:+.1f}%) |",
        "",
        "### Feature Drift & Population Stability Index (PSI) Summary",
        "",
        "| Feature Identifier | KS Statistic (P1->P2) | PSI (P1->P2) | KS Statistic (P1->P3) | PSI (P1->P3) | Drift Status | Retraining Impact |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for feat in result.feature_drift_profiles:
        impact = "Primary Retraining Driver" if feat.drift_status == "SEVERE_DRIFT" else "Monitored"
        lines.append(
            f"| `{feat.feature_name}` | {feat.p1_to_p2_ks_stat:.4f} | {feat.p1_to_p2_psi:.4f} | {feat.p1_to_p3_ks_stat:.4f} | {feat.p1_to_p3_psi:.4f} | `{feat.drift_status}` | {impact} |"
        )

    lines.extend([
        "",
        "### Key Empirical Findings & Regulatory Attestation",
        "",
        f"1. **Optimistic Evaluation Bias (+{deg.optimistic_kfold_bias_pr_auc:.4f} PR-AUC Inflation)**: Standard randomized K-fold CV projects an artificial {kf.pr_auc_mean:.4f} PR-AUC by mixing future fraud concepts into training folds. In production, chronological OOT testing reveals true distant performance of {p3.pr_auc:.4f}.",
        f"2. **Out-of-Time Degradation Velocity ({deg.p1_to_p3_relative_decay_pct:+.1f}% Loss)**: Unmaintained risk scoring degrades by {deg.p1_to_p3_pr_auc_delta:+.4f} PR-AUC and {deg.p1_to_p3_recall_01_delta_pp:+.2f} percentage points in strict Recall @ 0.1% FPR across periods.",
        f"3. **Automated Retraining Trigger Recommendation**: Trigger urgency status is `{deg.retraining_urgency}` (Max Feature PSI={deg.max_feature_psi:.4f}). Retraining is recommended when feature PSI exceeds 0.25 or relative PR-AUC degradation exceeds -15.0%.",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    reconfigure_fn = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure_fn):
        reconfigure_fn(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    suite = run_temporal_generalization_benchmark()
    md = format_temporal_benchmark_markdown(suite)
    print("\n" + md)
