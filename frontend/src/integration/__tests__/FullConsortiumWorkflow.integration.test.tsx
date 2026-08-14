import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import LandingPage from '../../pages/LandingPage';
import Dashboard from '../../pages/Dashboard';
import LiveOperationsView from '../../pages/LiveOperationsView';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('Integration: Full Consortium & Federated Learning Workflow', () => {
  const createWrapper = (initialRoute = '/') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    return ({ children }: { children?: React.ReactNode }) => (
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
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useBanks').mockReturnValue({
      data: [
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
      ],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useSimulations').mockReturnValue({
      data: [
        {
          id: 'sim_active_99',
          status: 'COMPLETED',
          current_round: 10,
          total_rounds: 10,
          progress_pct: 100,
          created_at: '2026-08-14T08:00:00Z',
          completed_at: '2026-08-14T08:05:00Z',
          duration_seconds: 300,
        },
      ],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useSimulation').mockReturnValue({
      data: {
        id: 'sim_active_99',
        status: 'COMPLETED',
        current_round: 10,
        total_rounds: 10,
        progress_pct: 100,
        created_at: '2026-08-14T08:00:00Z',
        started_at: '2026-08-14T08:00:00Z',
        completed_at: '2026-08-14T08:05:00Z',
        duration_seconds: 300,
        error_message: null,
        banks: [
          {
            id: 'bank_a',
            name: 'Meridian National Bank',
            tier: 'tier_1',
            fraud_ratio: 0.024,
            num_transactions: 125000,
            status: 'completed',
            improvement: { auc: 0.12, f1: 0.14 },
            data_profile: null,
            local_metrics: {
              accuracy: 0.92,
              precision: 0.88,
              recall: 0.84,
              f1_score: 0.86,
              auc_roc: 0.91,
              loss: 0.22,
              confusion_matrix: [[900, 100], [50, 950]],
              roc_fpr: [0, 0.1, 1],
              roc_tpr: [0, 0.9, 1],
              roc_thresholds: [1, 0.5, 0],
              feature_importance: { amount: 0.4, velocity: 0.6 },
            },
            federated_metrics: {
              accuracy: 0.97,
              precision: 0.94,
              recall: 0.91,
              f1_score: 0.925,
              auc_roc: 0.965,
              loss: 0.09,
              confusion_matrix: [[950, 50], [20, 980]],
              roc_fpr: [0, 0.05, 1],
              roc_tpr: [0, 0.95, 1],
              roc_thresholds: [1, 0.5, 0],
              feature_importance: { amount: 0.45, velocity: 0.55 },
            },
          },
        ],
        rounds: [
          {
            round_number: 1,
            total_rounds: 10,
            global_loss: 0.52,
            participating_banks: ['bank_a'],
            dropped_banks: [],
            duration_ms: 280,
            privacy_budget: 0.2,
          },
          {
            round_number: 10,
            total_rounds: 10,
            global_loss: 0.09,
            participating_banks: ['bank_a'],
            dropped_banks: [],
            duration_ms: 250,
            privacy_budget: 2.0,
          },
        ],
        config: {
          num_rounds: 10,
          local_epochs: 3,
          learning_rate: 0.001,
          batch_size: 32,
          min_clients_per_round: 1,
          enable_latency_simulation: false,
          latency_min_ms: 50,
          latency_max_ms: 200,
          enable_dropout_simulation: false,
          dropout_probability: 0,
          enable_reconnect_simulation: false,
          privacy_mechanism: 'differential_privacy',
          dp_epsilon: 2.0,
          dp_delta: 1e-5,
          dp_max_grad_norm: 1.0,
          dp_mode: 'opacus',
          bank_a_transactions: 125000,
          bank_b_transactions: 85000,
          bank_c_transactions: 45000,
          aggregation_method: 'fed_avg',
          enable_poisoning_simulation: false,
          poisoning_bank_id: 'none',
          poisoning_scale: 1.0,
          fl_engine_type: 'custom',
        },
      },
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useModelVersions').mockReturnValue({
      data: [
        {
          version: 1,
          simulation_id: 'sim_active_99',
          created_at: '2026-08-14T08:05:00Z',
          metrics: { auc_roc: 0.965, f1_score: 0.925, loss: 0.09, accuracy: 0.97, precision: 0.94, recall: 0.91, confusion_matrix: [], roc_fpr: [], roc_tpr: [], roc_thresholds: [], feature_importance: {} },
          is_champion: true,
          status: 'champion',
          parameters_count: 125000,
        },
      ],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAIActComplianceReport').mockReturnValue({
      data: {
        article_10_governance: { status: 'COMPLIANT', details: 'Zero raw PII decentralized training' },
        article_13_transparency: { status: 'COMPLIANT', details: 'Cryptographic hash audit logs verified' },
        fairness_evaluation: { disparate_impact_ratio: 0.95, equal_opportunity_difference: 0.02 },
      },
      isLoading: false,
    } as any);
  });

  it('navigates seamlessly from SaaS landing hero to live federated operations and model registry', () => {
    const Wrapper = createWrapper('/simulation/sim_active_99');
    render(<Wrapper />);

    expect(screen.getByText(/Live Operations Dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/Consortium Federated Learning Telemetry/i)).toBeInTheDocument();
  });
});
