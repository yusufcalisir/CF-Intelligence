import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import CalibrationReliabilityPlot from '../CalibrationReliabilityPlot';
import type { CalibrationReport } from '../../../api/types';

describe('CalibrationReliabilityPlot Component Test Suite', () => {
  const mockReport: CalibrationReport = {
    expected_calibration_error: 0.0482,
    max_calibration_error: 0.1250,
    brier_score: 0.0384,
    is_well_calibrated: true,
    evaluated_at: '2026-09-28T04:00:00Z',
    bins: [
      { bin_index: 1, prob_min: 0.0, prob_max: 0.1, mean_predicted_prob: 0.05, empirical_fraud_ratio: 0.045, sample_count: 1200 },
      { bin_index: 2, prob_min: 0.1, prob_max: 0.2, mean_predicted_prob: 0.15, empirical_fraud_ratio: 0.135, sample_count: 800 },
      { bin_index: 3, prob_min: 0.2, prob_max: 0.3, mean_predicted_prob: 0.25, empirical_fraud_ratio: 0.220, sample_count: 600 },
      { bin_index: 4, prob_min: 0.3, prob_max: 0.4, mean_predicted_prob: 0.35, empirical_fraud_ratio: 0.320, sample_count: 450 },
      { bin_index: 5, prob_min: 0.4, prob_max: 0.5, mean_predicted_prob: 0.45, empirical_fraud_ratio: 0.440, sample_count: 350 },
      { bin_index: 6, prob_min: 0.5, prob_max: 0.6, mean_predicted_prob: 0.55, empirical_fraud_ratio: 0.570, sample_count: 280 },
      { bin_index: 7, prob_min: 0.6, prob_max: 0.7, mean_predicted_prob: 0.65, empirical_fraud_ratio: 0.680, sample_count: 240 },
      { bin_index: 8, prob_min: 0.7, prob_max: 0.8, mean_predicted_prob: 0.75, empirical_fraud_ratio: 0.790, sample_count: 200 },
      { bin_index: 9, prob_min: 0.8, prob_max: 0.9, mean_predicted_prob: 0.85, empirical_fraud_ratio: 0.870, sample_count: 180 },
      { bin_index: 10, prob_min: 0.9, prob_max: 1.0, mean_predicted_prob: 0.95, empirical_fraud_ratio: 0.960, sample_count: 350 },
    ],
  };

  it('renders reliability diagram header, metrics and method selector', () => {
    render(<CalibrationReliabilityPlot report={mockReport} />);

    expect(screen.getByText(/Probability Calibration & Reliability Curve/i)).toBeInTheDocument();
    expect(screen.getByText(/Expected Calib Error \(ECE\)/i)).toBeInTheDocument();
    expect(screen.getByText('0.0482')).toBeInTheDocument();
    expect(screen.getByText(/Max Calib Error \(MCE\)/i)).toBeInTheDocument();
    expect(screen.getByText('0.1250')).toBeInTheDocument();
    expect(screen.getByText(/Brier Score \(MSE\)/i)).toBeInTheDocument();
    expect(screen.getByText('0.0384')).toBeInTheDocument();
    expect(screen.getByText(/WELL-CALIBRATED/i)).toBeInTheDocument();
  });

  it('renders loading state when isLoading is true', () => {
    render(<CalibrationReliabilityPlot isLoading={true} />);

    expect(screen.getByText(/Evaluating probability calibration/i)).toBeInTheDocument();
  });

  it('switches between Raw, Platt Scaling, and Isotonic Regression', () => {
    render(<CalibrationReliabilityPlot report={mockReport} />);

    // Click Platt Scaling button
    const plattBtn = screen.getByRole('button', { name: /Platt Scaling/i });
    fireEvent.click(plattBtn);
    expect(screen.getByText(/PLATT active/i)).toBeInTheDocument();
    expect(screen.getByText(/Platt scaling fits parametric logistic sigmoid/i)).toBeInTheDocument();

    // Click Isotonic button
    const isotonicBtn = screen.getByRole('button', { name: /Isotonic \(PAVA\)/i });
    fireEvent.click(isotonicBtn);
    expect(screen.getByText(/ISOTONIC active/i)).toBeInTheDocument();
    expect(screen.getByText(/Isotonic regression applies non-parametric/i)).toBeInTheDocument();

    // Return to Raw
    const rawBtn = screen.getByRole('button', { name: /Raw Model/i });
    fireEvent.click(rawBtn);
    expect(screen.getByText(/RAW active/i)).toBeInTheDocument();
  });

  it('toggles the 10-bin breakdown table', () => {
    render(<CalibrationReliabilityPlot report={mockReport} />);

    const toggleBtn = screen.getByRole('button', { name: /View 10-Bin Data/i });
    expect(toggleBtn).toBeInTheDocument();

    // Initially table is hidden
    expect(screen.queryByText('Pred Conf')).not.toBeInTheDocument();

    // Click to show table
    fireEvent.click(toggleBtn);
    expect(screen.getByText('Pred Conf')).toBeInTheDocument();
    expect(screen.getByText('Actual Rate')).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('#10')).toBeInTheDocument();

    // Click to hide
    const hideBtn = screen.getByRole('button', { name: /Hide 10-Bin Data/i });
    fireEvent.click(hideBtn);
    expect(screen.queryByText('Pred Conf')).not.toBeInTheDocument();
  });

  it('displays DEGRADED status when calibration metrics exceed threshold', () => {
    const degradedReport: CalibrationReport = {
      ...mockReport,
      expected_calibration_error: 0.1850,
      brier_score: 0.2800,
      is_well_calibrated: false,
    };

    render(<CalibrationReliabilityPlot report={degradedReport} />);

    expect(screen.getByText(/DEGRADED CALIB/i)).toBeInTheDocument();
  });
});
