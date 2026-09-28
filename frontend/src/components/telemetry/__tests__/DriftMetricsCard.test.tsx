/**
 * DriftMetricsCard Component Unit Test Suite
 * Validates drift status rendering, metric cards, feature table, and retraining triggers.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DriftMetricsCard from '../DriftMetricsCard';
import type { DriftAnalysisReport } from '../../../api/types';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe('DriftMetricsCard Component Test Suite', () => {
  const healthyReport: DriftAnalysisReport = {
    overall_status: 'HEALTHY',
    max_psi: 0.045,
    mean_ks_p_value: 0.825,
    concept_drift_psi: 0.032,
    auto_retrain_triggered: false,
    evaluated_at: '2026-09-28T10:00:00Z',
    feature_drifts: [
      {
        feature_name: 'transaction_amount',
        ks_statistic: 0.025,
        ks_p_value: 0.78,
        wasserstein_distance: 0.12,
        psi: 0.045,
        status: 'STABLE',
      },
      {
        feature_name: 'velocity_1h',
        ks_statistic: 0.018,
        ks_p_value: 0.89,
        wasserstein_distance: 0.08,
        psi: 0.032,
        status: 'STABLE',
      },
    ],
  };

  const warningReport: DriftAnalysisReport = {
    overall_status: 'WARNING',
    max_psi: 0.145,
    mean_ks_p_value: 0.035,
    concept_drift_psi: 0.112,
    auto_retrain_triggered: false,
    evaluated_at: '2026-09-28T11:00:00Z',
    feature_drifts: [
      {
        feature_name: 'transaction_amount',
        ks_statistic: 0.095,
        ks_p_value: 0.025,
        wasserstein_distance: 0.45,
        psi: 0.145,
        status: 'MODERATE_DRIFT',
      },
      {
        feature_name: 'velocity_1h',
        ks_statistic: 0.021,
        ks_p_value: 0.85,
        wasserstein_distance: 0.09,
        psi: 0.035,
        status: 'STABLE',
      },
    ],
  };

  const criticalReport: DriftAnalysisReport = {
    overall_status: 'CRITICAL',
    max_psi: 0.285,
    mean_ks_p_value: 0.002,
    concept_drift_psi: 0.245,
    auto_retrain_triggered: true,
    evaluated_at: '2026-09-28T12:00:00Z',
    feature_drifts: [
      {
        feature_name: 'transaction_amount',
        ks_statistic: 0.185,
        ks_p_value: 0.001,
        wasserstein_distance: 1.15,
        psi: 0.285,
        status: 'SEVERE_DRIFT',
      },
      {
        feature_name: 'channel_id',
        ks_statistic: 0.145,
        ks_p_value: 0.003,
        wasserstein_distance: 0.85,
        psi: 0.215,
        status: 'SEVERE_DRIFT',
      },
      {
        feature_name: 'velocity_1h',
        ks_statistic: 0.020,
        ks_p_value: 0.88,
        wasserstein_distance: 0.07,
        psi: 0.025,
        status: 'STABLE',
      },
    ],
  };

  it('renders healthy drift state with stable badges and KPIs', () => {
    render(<DriftMetricsCard data={healthyReport} />, { wrapper: createWrapper() });

    expect(screen.getByText('Feature & Concept Drift Profiling')).toBeInTheDocument();
    expect(screen.getByTestId('overall-status-badge')).toHaveTextContent(/STABLE \/ HEALTHY/i);
    expect(screen.getByTestId('max-psi-value')).toHaveTextContent('0.0450');
    expect(screen.getByTestId('concept-psi-value')).toHaveTextContent('0.0320');
    expect(screen.getByTestId('ks-pvalue-value')).toHaveTextContent('0.8250');
    expect(screen.getByTestId('retrain-status-text')).toHaveTextContent('STANDBY');
    expect(screen.getByText('transaction_amount')).toBeInTheDocument();
    expect(screen.getByText('velocity_1h')).toBeInTheDocument();
  });

  it('renders moderate drift state with warning badge and values', () => {
    render(<DriftMetricsCard data={warningReport} />, { wrapper: createWrapper() });

    expect(screen.getByTestId('overall-status-badge')).toHaveTextContent(/MODERATE DRIFT/i);
    expect(screen.getByTestId('max-psi-value')).toHaveTextContent('0.1450');
    expect(screen.getByTestId('concept-psi-value')).toHaveTextContent('0.1120');
    expect(screen.getByText('MODERATE_DRIFT')).toBeInTheDocument();
  });

  it('renders critical drift state with critical badge and triggered retraining indicator', () => {
    render(<DriftMetricsCard data={criticalReport} />, { wrapper: createWrapper() });

    expect(screen.getByTestId('overall-status-badge')).toHaveTextContent(/CRITICAL DRIFT/i);
    expect(screen.getByTestId('max-psi-value')).toHaveTextContent('0.2850');
    expect(screen.getByTestId('concept-psi-value')).toHaveTextContent('0.2450');
    expect(screen.getByTestId('retrain-status-text')).toHaveTextContent(/TRIGGERED/i);
    expect(screen.getAllByText('SEVERE_DRIFT')).toHaveLength(2);
  });

  it('renders loading skeleton when isLoading is true', () => {
    const { container } = render(<DriftMetricsCard isLoading={true} />, { wrapper: createWrapper() });

    expect(container.querySelector('.animate-pulse')).toBeInTheDocument();
  });

  it('allows selecting drifted features and executing manual/automated retraining callback', async () => {
    const handleRetrain = vi.fn().mockResolvedValue(undefined);

    render(
      <DriftMetricsCard data={criticalReport} onTriggerRetrain={handleRetrain} />,
      { wrapper: createWrapper() }
    );

    // Click "Select All Drifted" button
    const selectAllBtn = screen.getByRole('button', { name: /Select All Drifted/i });
    fireEvent.click(selectAllBtn);

    // Verify targeted feature text updates
    expect(screen.getByTestId('targeted-features-summary')).toHaveTextContent(/2 feature\(s\) targeted for retraining/i);

    // Click Trigger Automated Retraining button
    const triggerBtn = screen.getByTestId('trigger-retrain-btn');
    fireEvent.click(triggerBtn);

    await waitFor(() => {
      expect(handleRetrain).toHaveBeenCalledWith(['transaction_amount', 'channel_id']);
    });

    // Check feedback message
    expect(screen.getByTestId('retrain-feedback-msg')).toHaveTextContent(/Retraining dispatched for 2 feature\(s\)/i);
  });

  it('handles individual feature checkbox selection', () => {
    render(<DriftMetricsCard data={criticalReport} />, { wrapper: createWrapper() });

    const checkbox = screen.getByLabelText(/Select velocity_1h/i);
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    expect(screen.getByTestId('targeted-features-summary')).toHaveTextContent(/1 feature\(s\) targeted for retraining/i);

    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });
});
