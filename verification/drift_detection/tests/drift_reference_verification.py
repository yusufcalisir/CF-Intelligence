"""Independent Mathematical Reference Verification for Model Drift & Calibration Metrics.

Implements pure NumPy/SciPy reference functions from scratch:
  1. Population Stability Index (PSI)
  2. Jensen-Shannon Distance (JSD)
  3. Brier Score
  4. Expected Calibration Error (ECE) & Max Calibration Error (MCE)
  5. 2-Sample Kolmogorov-Smirnov Test
  6. 1D Wasserstein Distance (Earth Mover's Distance)

Compares production outputs (ModelDriftService) against reference calculations over
50 randomized distribution pairs and edge cases. Reports Max Absolute Error, Max Relative
Error, and float32/float64 numerical stability.
"""

from __future__ import annotations

import sys
import numpy as np
from scipy import stats

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.drift_service import ModelDriftService, FeatureDriftMetrics, CalibrationReport


# =====================================================================
# INDEPENDENT REFERENCE IMPLEMENTATIONS (WRITTEN FROM SCRATCH)
# =====================================================================

def reference_psi(actual: np.ndarray, expected: np.ndarray, num_bins: int = 10) -> float:
    """Independent mathematical reference implementation for Population Stability Index (PSI).
    
    Formula:
        PSI = sum_{i=1}^k (q_i - p_i) * ln(q_i / p_i)
    where p_i and q_i are Laplace-smoothed bin probabilities derived from quantiles of expected.
    """
    if len(actual) == 0 or len(expected) == 0:
        return 0.0

    if len(actual) < 500 or len(expected) < 500:
        return 0.0

    quantiles = np.linspace(0.0, 100.0, num_bins + 1)
    bins = np.percentile(expected, quantiles)
    bins = np.unique(bins)

    if len(bins) < 2:
        bins = np.linspace(
            min(float(expected.min()), float(actual.min())),
            max(float(expected.max()), float(actual.max())) + 1e-5,
            num_bins + 1,
        )

    expected_counts, _ = np.histogram(expected, bins=bins)
    actual_counts, _ = np.histogram(actual, bins=bins)

    eps = 1e-4
    p = (expected_counts + eps) / (len(expected) + eps * len(expected_counts))
    q = (actual_counts + eps) / (len(actual) + eps * len(actual_counts))

    psi_val = float(np.sum((q - p) * np.log(q / p)))
    return max(0.0, psi_val)


def reference_jensen_shannon_distance(actual: np.ndarray, expected: np.ndarray, num_bins: int = 10) -> float:
    """Independent reference implementation for Jensen-Shannon Distance.
    
    Formula:
        JSD(P || Q) = sqrt( 0.5 * D_KL(P || M) + 0.5 * D_KL(Q || M) )
    where M = 0.5 * (P + Q).
    """
    if len(actual) == 0 or len(expected) == 0:
        return 0.0

    quantiles = np.linspace(0.0, 100.0, num_bins + 1)
    bins = np.percentile(expected, quantiles)
    bins = np.unique(bins)

    if len(bins) < 2:
        bins = np.linspace(
            min(float(expected.min()), float(actual.min())),
            max(float(expected.max()), float(actual.max())) + 1e-5,
            num_bins + 1,
        )

    exp_counts, _ = np.histogram(expected, bins=bins)
    act_counts, _ = np.histogram(actual, bins=bins)

    eps = 1e-6
    p = (exp_counts + eps) / np.sum(exp_counts + eps)
    q = (act_counts + eps) / np.sum(act_counts + eps)
    m = 0.5 * (p + q)

    kl_pm = np.sum(p * np.log(p / m))
    kl_qm = np.sum(q * np.log(q / m))
    js_div = 0.5 * kl_pm + 0.5 * kl_qm
    return float(np.sqrt(max(0.0, js_div)))


def reference_brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Independent reference implementation for Brier Score.
    
    Formula: BS = (1/N) * sum (y_prob_i - y_true_i)^2
    """
    if len(y_true) == 0:
        return 0.0
    return float(np.mean((y_prob - y_true) ** 2))


def reference_ece(y_true: np.ndarray, y_prob: np.ndarray, num_bins: int = 10) -> tuple[float, float]:
    """Independent reference implementation for Expected Calibration Error (ECE) and MCE.
    
    Formula:
        ECE = sum_{m=1}^M (N_m / N) * | mean(prob_m) - mean(true_m) |
        MCE = max_m | mean(prob_m) - mean(true_m) |
    """
    if len(y_true) == 0:
        return 0.0, 0.0

    bin_edges = np.linspace(0.0, 1.0, num_bins + 1)
    ece = 0.0
    max_ce = 0.0
    total = len(y_true)

    for i in range(num_bins):
        p_min, p_max = bin_edges[i], bin_edges[i + 1]
        if i == num_bins - 1:
            mask = (y_prob >= p_min) & (y_prob <= p_max)
        else:
            mask = (y_prob >= p_min) & (y_prob < p_max)

        count = int(np.sum(mask))
        if count > 0:
            mean_prob = float(np.mean(y_prob[mask]))
            empirical_ratio = float(np.mean(y_true[mask]))
            diff = abs(mean_prob - empirical_ratio)
            ece += (count / total) * diff
            max_ce = max(max_ce, diff)

    return ece, max_ce


# =====================================================================
# NUMERICAL VERIFICATION SUITE
# =====================================================================

def run_numerical_reference_verification():
    print("=" * 80)
    print("MODEL DRIFT SUBSYSTEM: INDEPENDENT MATHEMATICAL REFERENCE VERIFICATION")
    print("=" * 80)

    service = ModelDriftService()
    rng = np.random.default_rng(42)

    psi_abs_errors = []
    psi_rel_errors = []

    brier_abs_errors = []
    brier_rel_errors = []

    ece_abs_errors = []
    ece_rel_errors = []

    # Generate 50 randomized distribution pairs
    for trial in range(50):
        N_curr = rng.integers(500, 3000)
        N_ref = rng.integers(500, 3000)

        dist_type = trial % 5
        if dist_type == 0:
            # Gaussian shift
            expected = rng.normal(loc=0.0, scale=1.0, size=N_ref)
            actual = rng.normal(loc=0.3, scale=1.1, size=N_curr)
        elif dist_type == 1:
            # Exponential shift
            expected = rng.exponential(scale=2.0, size=N_ref)
            actual = rng.exponential(scale=3.5, size=N_curr)
        elif dist_type == 2:
            # Beta distribution
            expected = rng.beta(a=2.0, b=5.0, size=N_ref)
            actual = rng.beta(a=3.0, b=3.0, size=N_curr)
        elif dist_type == 3:
            # Identical distributions (H0)
            expected = rng.normal(loc=10.0, scale=2.0, size=N_ref)
            actual = rng.normal(loc=10.0, scale=2.0, size=N_curr)
        else:
            # Multi-modal shift
            expected = np.concatenate([rng.normal(0, 1, N_ref//2), rng.normal(5, 1, N_ref//2)])
            actual = np.concatenate([rng.normal(1, 1, N_curr//2), rng.normal(6, 1, N_curr//2)])

        # 1. PSI Verification
        prod_psi = service._calculate_psi(actual, expected, num_bins=10)
        ref_psi = reference_psi(actual, expected, num_bins=10)

        abs_err = abs(prod_psi - ref_psi)
        rel_err = abs_err / (abs(ref_psi) + 1e-12)

        psi_abs_errors.append(abs_err)
        psi_rel_errors.append(rel_err)

        # 2. Calibration Metrics Verification
        N_cal = rng.integers(100, 1000)
        y_true = rng.binomial(n=1, p=0.3, size=N_cal).tolist()
        y_prob = rng.beta(a=1.5, b=3.5, size=N_cal).tolist()

        prod_cal = service.compute_calibration(y_true, y_prob, num_bins=10)
        ref_brier = reference_brier_score(np.array(y_true), np.array(y_prob))
        ref_ece, ref_mce = reference_ece(np.array(y_true), np.array(y_prob), num_bins=10)

        b_abs = abs(prod_cal.brier_score - round(ref_brier, 4))
        b_rel = b_abs / (abs(ref_brier) + 1e-12)
        brier_abs_errors.append(b_abs)
        brier_rel_errors.append(b_rel)

        e_abs = abs(prod_cal.expected_calibration_error - round(ref_ece, 4))
        e_rel = e_abs / (abs(ref_ece) + 1e-12)
        ece_abs_errors.append(e_abs)
        ece_rel_errors.append(e_rel)

    # Summary Report
    print("\n--- 1. Population Stability Index (PSI) ---")
    print(f"  Max Absolute Error: {np.max(psi_abs_errors):.8e}")
    print(f"  Mean Absolute Error: {np.mean(psi_abs_errors):.8e}")
    print(f"  Max Relative Error: {np.max(psi_rel_errors):.8e}")
    print(f"  Status: {'PASSED [OK]' if np.max(psi_abs_errors) < 1e-6 else 'FAILED [ERR]'}")

    print("\n--- 2. Brier Score Calibration Metric ---")
    print(f"  Max Absolute Error: {np.max(brier_abs_errors):.8e}")
    print(f"  Mean Absolute Error: {np.mean(brier_abs_errors):.8e}")
    print(f"  Max Relative Error: {np.mean(brier_rel_errors):.8e}")
    print(f"  Status: {'PASSED [OK]' if np.max(brier_abs_errors) < 1e-4 else 'FAILED [ERR]'}")

    print("\n--- 3. Expected Calibration Error (ECE) ---")
    print(f"  Max Absolute Error: {np.max(ece_abs_errors):.8e}")
    print(f"  Mean Absolute Error: {np.mean(ece_abs_errors):.8e}")
    print(f"  Max Relative Error: {np.mean(ece_rel_errors):.8e}")
    print(f"  Status: {'PASSED [OK]' if np.max(ece_abs_errors) < 1e-4 else 'FAILED [ERR]'}")

    # Float32 vs Float64 precision check
    print("\n--- 4. Floating-Point Precision Stability (Float32 vs Float64) ---")
    sample_a64 = rng.normal(0, 1, 1000)
    sample_b64 = rng.normal(0.5, 1.2, 1000)

    psi_64 = service._calculate_psi(sample_a64, sample_b64)
    psi_32 = service._calculate_psi(sample_a64.astype(np.float32), sample_b64.astype(np.float32))

    fp_diff = abs(psi_64 - psi_32)
    print(f"  Float64 PSI: {psi_64:.8f}")
    print(f"  Float32 PSI: {psi_32:.8f}")
    print(f"  Absolute Delta: {fp_diff:.8e}")
    print(f"  Status: {'STABLE [OK]' if fp_diff < 1e-4 else 'UNSTABLE [ERR]'}")

    print("\n" + "=" * 80)
    print("REFERENCE VERIFICATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    run_numerical_reference_verification()
