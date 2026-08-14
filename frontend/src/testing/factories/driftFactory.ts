import type { CalibrationReport, DriftAnalysisReport } from '../../api/types';

/**
 * Creates a mock DriftAnalysisReport response.
 */
export function createMockDriftAnalysis(overrides: Partial<DriftAnalysisReport> = {}): DriftAnalysisReport {
  return {
    overall_status: 'stable',
    max_psi: overrides.max_psi ?? 0.045,
    mean_ks_p_value: 0.88,
    concept_drift_psi: overrides.concept_drift_psi ?? 0.038,
    auto_retrain_triggered: false,
    evaluated_at: '2026-08-14T10:00:00Z',
    feature_drifts: overrides.feature_drifts || [
      {
        feature_name: 'transaction_amount',
        ks_statistic: 0.021,
        ks_p_value: 0.85,
        wasserstein_distance: 120.5,
        psi: 0.045,
        status: 'stable',
      },
      {
        feature_name: 'velocity_1h',
        ks_statistic: 0.018,
        ks_p_value: 0.92,
        wasserstein_distance: 0.42,
        psi: 0.038,
        status: 'stable',
      },
    ],
    ...overrides,
  };
}

/**
 * Creates a mock CalibrationReport response.
 */
export function createMockCalibrationReport(overrides: Partial<CalibrationReport> = {}): CalibrationReport {
  return {
    expected_calibration_error: overrides.expected_calibration_error ?? 0.018,
    max_calibration_error: overrides.max_calibration_error ?? 0.034,
    brier_score: overrides.brier_score ?? 0.012,
    is_well_calibrated: true,
    evaluated_at: '2026-08-14T10:00:00Z',
    bins: overrides.bins || [
      { bin_index: 1, prob_min: 0.0, prob_max: 0.2, mean_predicted_prob: 0.1, empirical_fraud_ratio: 0.09, sample_count: 500 },
      { bin_index: 2, prob_min: 0.2, prob_max: 0.4, mean_predicted_prob: 0.3, empirical_fraud_ratio: 0.28, sample_count: 850 },
    ],
    ...overrides,
  };
}
