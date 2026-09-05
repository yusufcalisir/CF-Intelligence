"""Bank information endpoints."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd  # noqa: TC002
from fastapi import APIRouter, HTTPException
from scipy import stats

from app.application.services.data_generator import DataGenerator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/banks", tags=["banks"])

# Default bank configurations (static reference data)
BANK_CONFIGS = [
    {
        "id": "bank_a",
        "name": "Meridian National",
        "tier": "large",
        "description": "Large retail bank with broad domestic presence",
        "default_fraud_ratio": 0.008,
        "default_transactions": 50000,
        "fraud_pattern": "Velocity spikes during late-night hours with unusual merchant categories",
        "characteristics": [
            "High transaction volume",
            "Predominantly domestic transactions",
            "POS-heavy with growing mobile adoption",
            "Low baseline fraud rate",
        ],
    },
    {
        "id": "bank_b",
        "name": "Nexus Digital",
        "tier": "medium",
        "description": "Digital-only bank with international customer base",
        "default_fraud_ratio": 0.025,
        "default_transactions": 30000,
        "fraud_pattern": "New accounts from high-risk countries using crypto and wire transfers",
        "characteristics": [
            "Mobile-first platform",
            "High international transaction ratio",
            "Younger account age distribution",
            "Higher baseline fraud rate due to onboarding velocity",
        ],
    },
    {
        "id": "bank_c",
        "name": "Heritage Regional",
        "tier": "small",
        "description": "Traditional regional bank with concentrated geography",
        "default_fraud_ratio": 0.012,
        "default_transactions": 20000,
        "fraud_pattern": "Card testing - repeated small amounts followed by a large charge",
        "characteristics": [
            "Concentrated geographic footprint",
            "Longer average account age",
            "POS-dominant transaction mix",
            "Moderate fraud rate with distinct testing patterns",
        ],
    },
]

# Cached distributions data (computed once on first request)
_distributions_cache: dict[str, Any] | None = None


@router.get("")
async def list_banks() -> list[dict]:
    """List all bank configurations (reference data)."""
    return BANK_CONFIGS


def _compute_js_divergence(p: Any, q: Any) -> float:
    """Compute the Jensen-Shannon Divergence between two binned frequency distributions."""
    p_arr = np.array(p, dtype=float)
    q_arr = np.array(q, dtype=float)

    p_sum = p_arr.sum()
    q_sum = q_arr.sum()

    if p_sum == 0 or q_sum == 0:
        return 0.0

    p_norm = p_arr / p_sum
    q_norm = q_arr / q_sum

    m = 0.5 * (p_norm + q_norm)

    # Avoid zero division and log(0) warnings by computing element-wise with safeguards
    kl_pm = 0.0
    if np.any(p_norm > 0):
        ratio_p = np.zeros_like(p_norm)
        np.divide(p_norm, m, out=ratio_p, where=p_norm > 0)

        log_ratio_p = np.zeros_like(ratio_p)
        np.log2(ratio_p, out=log_ratio_p, where=ratio_p > 0)

        kl_pm = np.sum(p_norm * log_ratio_p)

    kl_qm = 0.0
    if np.any(q_norm > 0):
        ratio_q = np.zeros_like(q_norm)
        np.divide(q_norm, m, out=ratio_q, where=q_norm > 0)

        log_ratio_q = np.zeros_like(ratio_q)
        np.log2(ratio_q, out=log_ratio_q, where=ratio_q > 0)

        kl_qm = np.sum(q_norm * log_ratio_q)

    js = 0.5 * kl_pm + 0.5 * kl_qm
    return float(np.clip(js, 0.0, 1.0))


def _compute_psi(expected: Any, actual: Any, num_bins: int = 10) -> float:
    """Compute the Population Stability Index (PSI) between two continuous arrays."""
    exp_arr = np.array(expected, dtype=float)
    act_arr = np.array(actual, dtype=float)

    if len(exp_arr) == 0 or len(act_arr) == 0:
        return 0.0

    # Standard quantile binning based on expected
    percentiles = np.linspace(0, 100, num_bins + 1)
    bin_edges = np.percentile(exp_arr, percentiles)
    bin_edges = np.unique(bin_edges)

    if len(bin_edges) < 2:
        bin_edges = np.array([exp_arr.min() - 0.1, exp_arr.max() + 0.1])

    expected_counts, _ = np.histogram(exp_arr, bins=bin_edges)
    actual_counts, _ = np.histogram(act_arr, bins=bin_edges)

    expected_pct = expected_counts / len(exp_arr)
    actual_pct = actual_counts / len(act_arr)

    # Epsilon padding to prevent log(0)
    eps = 1e-4
    expected_pct = np.where(expected_pct == 0, eps, expected_pct)
    actual_pct = np.where(actual_pct == 0, eps, actual_pct)

    expected_pct /= expected_pct.sum()
    actual_pct /= actual_pct.sum()

    psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))
    return float(psi)


def _compute_categorical_js(expected: Any, actual: Any) -> float:
    """Compute JS Divergence for categorical arrays."""
    exp_arr = np.asarray(expected)
    act_arr = np.asarray(actual)
    if len(exp_arr) == 0 or len(act_arr) == 0:
        return 0.0
    all_cats = np.unique(np.concatenate([exp_arr, act_arr]))
    exp_counts = np.array([np.sum(exp_arr == cat) for cat in all_cats], dtype=float)
    act_counts = np.array([np.sum(act_arr == cat) for cat in all_cats], dtype=float)
    return _compute_js_divergence(exp_counts, act_counts)


def _compute_categorical_psi(expected: Any, actual: Any) -> float:
    """Compute PSI for categorical arrays."""
    exp_arr = np.asarray(expected)
    act_arr = np.asarray(actual)
    if len(exp_arr) == 0 or len(act_arr) == 0:
        return 0.0
    all_cats = np.unique(np.concatenate([exp_arr, act_arr]))
    exp_counts = np.array([np.sum(exp_arr == cat) for cat in all_cats], dtype=float)
    act_counts = np.array([np.sum(act_arr == cat) for cat in all_cats], dtype=float)

    expected_pct = exp_counts / len(exp_arr)
    actual_pct = act_counts / len(act_arr)

    eps = 1e-4
    expected_pct = np.where(expected_pct == 0, eps, expected_pct)
    actual_pct = np.where(actual_pct == 0, eps, actual_pct)

    expected_pct /= expected_pct.sum()
    actual_pct /= actual_pct.sum()

    psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))
    return float(psi)


def _compute_feature_drift(df_exp: pd.DataFrame, df_act: pd.DataFrame) -> dict[str, Any]:
    """Compute feature drift statistics between two DataFrames."""
    features_config = {
        "transaction_amount": "continuous",
        "velocity": "continuous",
        "hour_of_day": "continuous",
        "merchant_category": "categorical",
        "device_type": "categorical",
    }

    features_drift = {}
    psi_values = []
    js_values = []

    for feat, f_type in features_config.items():
        exp_vals = df_exp[feat].to_numpy()
        act_vals = df_act[feat].to_numpy()

        if f_type == "continuous":
            ks_stat, _ = stats.ks_2samp(exp_vals, act_vals)
            psi = _compute_psi(exp_vals, act_vals)
            bins = np.linspace(
                min(exp_vals.min(), act_vals.min()), max(exp_vals.max(), act_vals.max()) + 1e-5, 11
            )
            hist_exp, _ = np.histogram(exp_vals, bins=bins)
            hist_act, _ = np.histogram(act_vals, bins=bins)
            js = _compute_js_divergence(hist_exp, hist_act)
            ks_val = round(float(ks_stat), 4)
        else:
            psi = _compute_categorical_psi(exp_vals, act_vals)
            js = _compute_categorical_js(exp_vals, act_vals)
            ks_val = None

        psi_val = round(psi, 4)
        js_val = round(js, 4)

        psi_values.append(psi_val)
        js_values.append(js_val)

        if psi_val < 0.1:
            status = "stable"
        elif psi_val < 0.25:
            status = "moderate"
        else:
            status = "drifted"

        features_drift[feat] = {
            "psi": psi_val,
            "js_divergence": js_val,
            "ks": ks_val,
            "status": status,
        }

    return {
        "overall_psi": round(sum(psi_values) / len(psi_values), 4),
        "overall_js": round(sum(js_values) / len(js_values), 4),
        "features": features_drift,
    }


def _compute_concept_drift(
    df_exp: pd.DataFrame,
    y_exp: pd.Series,
    df_act: pd.DataFrame,
    y_act: pd.Series,
) -> dict[str, Any]:
    """Compute concept drift statistics between two bank datasets."""
    from sklearn.linear_model import LogisticRegression

    # 1) Model prediction probability shift
    X_exp: Any = DataGenerator.encode_features(df_exp)
    X_act: Any = DataGenerator.encode_features(df_act)

    if y_exp.nunique() < 2:
        pred_psi = 0.0
        pred_js = 0.0
    else:
        clf = LogisticRegression(solver="lbfgs", max_iter=100, random_state=42)
        clf.fit(X_exp, y_exp.to_numpy())

        p_exp: Any = clf.predict_proba(X_exp)[:, 1]
        p_act: Any = clf.predict_proba(X_act)[:, 1]

        pred_psi = _compute_psi(p_exp, p_act, num_bins=10)

        bins = np.linspace(0.0, 1.0, 11)
        h_exp, _ = np.histogram(p_exp, bins=bins)
        h_act, _ = np.histogram(p_act, bins=bins)
        pred_js = _compute_js_divergence(h_exp, h_act)

    pred_psi = round(pred_psi, 4)
    pred_js = round(pred_js, 4)

    if pred_psi < 0.1:
        pred_status = "stable"
    elif pred_psi < 0.25:
        pred_status = "moderate"
    else:
        pred_status = "drifted"

    model_prediction_drift = {
        "psi": pred_psi,
        "js_divergence": pred_js,
        "status": pred_status,
    }

    # 2) Conditional distributions (Y given X)
    is_fraud_exp = y_exp.to_numpy().astype(bool)
    is_fraud_act = y_act.to_numpy().astype(bool)

    # Hour of day fraud occurrences normalized
    hours_exp = df_exp["hour_of_day"].to_numpy()
    hours_act = df_act["hour_of_day"].to_numpy()
    h_fraud_exp = np.array(
        [np.sum((hours_exp == h) & is_fraud_exp) for h in range(24)], dtype=float
    )
    h_fraud_act = np.array(
        [np.sum((hours_act == h) & is_fraud_act) for h in range(24)], dtype=float
    )
    hour_cond_js = _compute_js_divergence(h_fraud_exp, h_fraud_act)

    # Merchant category fraud occurrences normalized
    cats_exp = df_exp["merchant_category"].to_numpy()
    cats_act = df_act["merchant_category"].to_numpy()
    all_cats = np.unique(np.concatenate([cats_exp, cats_act]))
    m_fraud_exp = np.array([np.sum((cats_exp == c) & is_fraud_exp) for c in all_cats], dtype=float)
    m_fraud_act = np.array([np.sum((cats_act == c) & is_fraud_act) for c in all_cats], dtype=float)
    merchant_cond_js = _compute_js_divergence(m_fraud_exp, m_fraud_act)

    conditional_drifts = {
        "hour_of_day": round(hour_cond_js, 4),
        "merchant_category": round(merchant_cond_js, 4),
    }

    overall_psi = pred_psi
    overall_js = round(0.5 * pred_js + 0.25 * hour_cond_js + 0.25 * merchant_cond_js, 4)

    return {
        "overall_psi": overall_psi,
        "overall_js": overall_js,
        "model_prediction_drift": model_prediction_drift,
        "conditional_drifts": conditional_drifts,
    }


@router.get("/distributions")
async def get_bank_distributions() -> dict[str, Any]:
    """Get distribution data for all banks to visualize Non-IID data drift.

    Generates a small sample (1000 txns/bank) and computes:
    - Transaction amount histograms (15 bins, log-scale)
    - Hourly fraud rate distributions (24h)
    - Merchant category risk profiles (top 8)
    - KS divergence statistics between bank pairs
    - PSI & JS Divergence for both Feature Drift and Concept Drift
    """
    global _distributions_cache  # noqa: PLW0603

    if _distributions_cache is not None:
        return _distributions_cache

    generator = DataGenerator(seed=42)
    datasets = generator.generate_bank_datasets(
        bank_a_size=1000,
        bank_b_size=1000,
        bank_c_size=1000,
    )

    banks_data: dict[str, Any] = {}
    amount_arrays: dict[str, Any] = {}

    for bank_id, (df, labels) in datasets.items():
        amounts = df["transaction_amount"].to_numpy()
        amount_arrays[bank_id] = amounts
        hours = df["hour_of_day"].to_numpy()
        merchants = df["merchant_category"].to_numpy()
        is_fraud = labels.to_numpy().astype(bool)

        # 1) Transaction amount histogram (log-scale bins)
        log_bins = np.logspace(
            np.log10(max(amounts.min(), 0.01)),
            np.log10(amounts.max() + 1),
            16,
        )
        total_counts, bin_edges = np.histogram(amounts, bins=log_bins)
        fraud_counts, _ = np.histogram(amounts[is_fraud], bins=log_bins)

        amount_histogram = {
            "bins": [round(float(b), 2) for b in bin_edges],
            "counts": [int(c) for c in total_counts],
            "fraud_counts": [int(c) for c in fraud_counts],
        }

        # 2) Hourly fraud rate
        hourly_total = np.zeros(24, dtype=int)
        hourly_fraud = np.zeros(24, dtype=int)
        for h in range(24):
            mask = hours == h
            hourly_total[h] = int(mask.sum())
            hourly_fraud[h] = int((mask & is_fraud).sum())

        hourly_fraud_rate = {
            "hours": list(range(24)),
            "total": [int(t) for t in hourly_total],
            "fraud": [int(f) for f in hourly_fraud],
        }

        # 3) Merchant category risk (top 8 by volume)
        unique_merchants, merchant_counts = np.unique(merchants, return_counts=True)
        top_idx = np.argsort(-merchant_counts)[:8]
        top_merchants = unique_merchants[top_idx]
        top_counts = merchant_counts[top_idx]

        merchant_fraud_rates = []
        for m in top_merchants:
            m_mask = merchants == m
            m_total = int(m_mask.sum())
            m_fraud = int((m_mask & is_fraud).sum())
            merchant_fraud_rates.append(round(m_fraud / m_total, 4) if m_total > 0 else 0.0)

        merchant_risk = {
            "categories": [str(m) for m in top_merchants],
            "fraud_rates": merchant_fraud_rates,
            "counts": [int(c) for c in top_counts],
        }

        banks_data[bank_id] = {
            "amount_histogram": amount_histogram,
            "hourly_fraud_rate": hourly_fraud_rate,
            "merchant_risk": merchant_risk,
        }

    # 4) Statistical Divergence calculations (KS, PSI, JS) between bank pairs
    pairs = [("bank_a", "bank_b"), ("bank_a", "bank_c"), ("bank_b", "bank_c")]
    ks_stats: dict[str, float] = {}
    feature_drifts: dict[str, Any] = {}
    concept_drifts: dict[str, Any] = {}

    for b1, b2 in pairs:
        key = f"{b1.split('_')[1]}_vs_{b2.split('_')[1]}"

        # Original amount KS statistic
        ks_stat, _ = stats.ks_2samp(amount_arrays[b1], amount_arrays[b2])
        ks_stats[key] = round(float(ks_stat), 4)

        # Continuous & categorical feature drift
        feature_drifts[key] = _compute_feature_drift(datasets[b1][0], datasets[b2][0])

        # Model-based and segment-based concept drift
        concept_drifts[key] = _compute_concept_drift(
            datasets[b1][0], datasets[b1][1], datasets[b2][0], datasets[b2][1]
        )

    overall_score = round(sum(ks_stats.values()) / len(ks_stats), 4)

    result: dict[str, Any] = {
        "banks": banks_data,
        "divergence_summary": {
            "amount_ks_statistic": ks_stats,
            "overall_non_iid_score": overall_score,
            "feature_drift": feature_drifts,
            "concept_drift": concept_drifts,
        },
    }

    _distributions_cache = result
    return result


@router.get("/scoring-volume")
async def get_scoring_volume() -> list[dict[str, Any]]:
    """Return 24-hour aggregated transaction scoring volume across consortium banks.

    Aggregates real empirical hourly transaction volume from bank datasets.
    """
    dist = await get_bank_distributions()
    banks_data = dist.get("banks", {})
    hourly_totals = np.zeros(24, dtype=int)
    for b_data in banks_data.values():
        h_data = b_data.get("hourly_fraud_rate", {})
        totals = h_data.get("total", [])
        for i, t in enumerate(totals[:24]):
            hourly_totals[i] += int(t)

    return [
        {"time": f"{h:02d}:00", "volume": int(hourly_totals[h])}
        for h in range(24)
    ]


@router.get("/{bank_id}")
async def get_bank(bank_id: str) -> dict:
    """Get details for a specific bank."""
    for bank in BANK_CONFIGS:
        if bank["id"] == bank_id:
            return bank
    raise HTTPException(status_code=404, detail=f"Bank {bank_id} not found")
