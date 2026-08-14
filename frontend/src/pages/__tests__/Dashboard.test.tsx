import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import Dashboard from '../Dashboard';
import * as queries from '../../api/queries';
import type { BankInfo } from '../../api/types';

describe('Dashboard (Federated Operations Home) Test Suite', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const mockBanks: BankInfo[] = [
    {
      id: 'bank_a',
      name: 'Meridian National Bank',
      tier: 'tier_1',
      description: 'Tier 1 Global National Bank',
      default_transactions: 125000,
      default_fraud_ratio: 0.024,
      fraud_pattern: 'High-frequency cross-border structuring',
      characteristics: ['Tier 1 Global', 'High Volume'],
    },
    {
      id: 'bank_b',
      name: 'Apex Commercial Bank',
      tier: 'tier_2',
      description: 'Regional Commercial Bank',
      default_transactions: 85000,
      default_fraud_ratio: 0.018,
      fraud_pattern: 'Synthetic identity loans',
      characteristics: ['Commercial Core', 'Low Latency'],
    },
  ];

  const mockSimulations = [
    {
      id: 'sim_active_01',
      status: 'RUNNING',
      current_round: 4,
      total_rounds: 10,
      progress_pct: 40,
      created_at: '2026-08-14T12:00:00Z',
      completed_at: null,
      duration_seconds: 120,
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useBanks').mockReturnValue({
      data: mockBanks,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useSimulations').mockReturnValue({
      data: mockSimulations,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useCreateSimulation').mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useDriftAnalysis').mockReturnValue({
      data: {
        is_drift_detected: false,
        max_psi: 0.04,
        features: [{ feature_name: 'amount', psi_value: 0.02, ks_statistic: 0.01, ks_p_value: 0.88, is_drifted: false }],
      },
      isLoading: false,
    } as any);
  });

  it('renders collaborative fraud intelligence hero and participating bank nodes', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Dashboard />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Collaborative Fraud Intelligence/i)).toBeInTheDocument();
    expect(screen.getByText(/Participating Institutions/i)).toBeInTheDocument();
    expect(screen.getByText(/Meridian National Bank/i)).toBeInTheDocument();
    expect(screen.getByText(/Apex Commercial Bank/i)).toBeInTheDocument();
  });

  it('renders simulation controls panel with configuration inputs and run button', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Dashboard />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Simulation Configuration/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Start Federated Training/i })).toBeInTheDocument();
  });
});
