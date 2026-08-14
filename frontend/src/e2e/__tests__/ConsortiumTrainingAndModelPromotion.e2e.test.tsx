import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import LandingPage from '../../pages/LandingPage';
import Dashboard from '../../pages/Dashboard';
import LiveOperationsView from '../../pages/LiveOperationsView';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';
import type { BankInfo } from '../../api/types';

describe('E2E Business Flow 1: Consortium Federated Training & Model Promotion', () => {
  const createWrapper = (initialRoute = '/operations') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });

    return () => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/operations" element={<LiveOperationsView />} />
              <Route path="/simulation/:id" element={<LiveOperationsView />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  const mockBanks: BankInfo[] = [
    {
      id: 'bank_a',
      name: 'Bank Alpha',
      tier: 'tier_1',
      description: 'Tier 1 Global National Bank',
      default_transactions: 125000,
      default_fraud_ratio: 0.024,
      fraud_pattern: 'High-frequency cross-border structuring',
      characteristics: ['Tier 1 Global', 'High Volume'],
    },
    {
      id: 'bank_b',
      name: 'Bank Beta',
      tier: 'tier_2',
      description: 'Regional Commercial Bank',
      default_transactions: 85000,
      default_fraud_ratio: 0.018,
      fraud_pattern: 'Synthetic identity loans',
      characteristics: ['Commercial Core', 'Low Latency'],
    },
  ];

  const mockActiveSimulation = {
    id: 'sim_e2e_fl_01',
    status: 'COMPLETED',
    current_round: 5,
    total_rounds: 5,
    config: {
      num_rounds: 5,
      local_epochs: 3,
      learning_rate: 0.005,
      batch_size: 64,
      aggregation_strategy: 'FedAvg',
      enable_differential_privacy: true,
      target_epsilon: 1.5,
      enable_web3_settlement: true,
      settlement_currency: 'wCBDC',
    },
    metrics_history: [
      { round: 1, global_loss: 0.42, global_auc: 0.88, precision: 0.84, recall: 0.82, f1: 0.83, epsilon_spent: 0.3 },
      { round: 5, global_loss: 0.12, global_auc: 0.965, precision: 0.94, recall: 0.93, f1: 0.935, epsilon_spent: 1.42 },
    ],
    bank_contributions: [
      { bank_id: 'bank_a', bank_name: 'Bank Alpha', shapley_value: 0.45, token_reward: 4500, data_quality_score: 96 },
      { bank_id: 'bank_b', bank_name: 'Bank Beta', shapley_value: 0.35, token_reward: 3500, data_quality_score: 92 },
    ],
  };

  const mockModelVersions = [
    {
      version: 2,
      filename: 'cfi_global_champion_v2.pt',
      metrics: {
        auc_roc: 0.965,
        f1_score: 0.935,
        precision: 0.94,
        recall: 0.93,
        loss: 0.12,
      },
      is_active: true,
      created_at: '2026-08-14T18:00:00Z',
      promoted_by: 'Consortium Governance Council',
      rollback_target: false,
    },
    {
      version: 1,
      filename: 'cfi_global_champion_v1.pt',
      metrics: {
        auc_roc: 0.92,
        f1_score: 0.89,
        precision: 0.90,
        recall: 0.88,
        loss: 0.28,
      },
      is_active: false,
      created_at: '2026-08-14T12:00:00Z',
      promoted_by: 'Automated CI/CD',
      rollback_target: true,
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
      data: [mockActiveSimulation],
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useSimulation').mockReturnValue({
      data: mockActiveSimulation,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useModelVersions').mockReturnValue({
      data: mockModelVersions,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useDriftAnalysis').mockReturnValue({
      data: {
        is_drift_detected: false,
        max_psi: 0.038,
        features: [{ feature_name: 'tx_amount', psi_value: 0.018, is_drifted: false }],
      },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useCreateSimulation').mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
  });

  it('executes end-to-end federated training cycle, monitors convergence, and inspects model registry promotion', async () => {
    const user = userEvent.setup();
    const Wrapper = createWrapper('/operations');
    render(<Wrapper />);

    // 1. Verify consortium operational view loaded with participating banks
    expect(await screen.findByText(/Live Operations Dashboard/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Bank Alpha/i)[0]).toBeInTheDocument();
    expect(screen.getAllByText(/Bank Beta/i)[0]).toBeInTheDocument();

    // 2. Start / Trigger federated simulation
    const startButton = screen.getByRole('button', { name: /Start Simulation/i });
    expect(startButton).toBeInTheDocument();
    await user.click(startButton);

    // 3. Inspect multi-institution Shapley incentive distribution & Web3 Settlement
    expect(await screen.findByText(/Consortium Incentive Registry/i)).toBeInTheDocument();
    expect(screen.getByText(/Federated Shapley Contribution/i)).toBeInTheDocument();

    // 4. Verify Model Registry contains active version badge
    expect(screen.getByText(/Model Registry & Canary Evaluation/i)).toBeInTheDocument();
    expect(screen.getByText(/Active Production Model \(v2\)/i)).toBeInTheDocument();
  });
});
