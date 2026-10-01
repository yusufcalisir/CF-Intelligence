import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import PrivacyDefensePage from '../PrivacyDefensePage';
import * as queries from '../../api/queries';

const mockMethods = [
  {
    id: 'fedavg',
    label: 'Federated Averaging (FedAvg)',
    description: 'Baseline federated aggregation across participating banks',
    paper: 'McMahan et al. 2017',
    byzantine_robust: false,
    colluding_defense: false,
  },
  {
    id: 'krum',
    label: 'Multi-Krum Robust Aggregation',
    description: 'Euclidean distance-based Byzantine gradient filtering defense',
    paper: 'Blanchard et al. 2017',
    byzantine_robust: true,
    colluding_defense: false,
  },
  {
    id: 'bulyan',
    label: 'Bulyan (Multi-Attacker)',
    description: 'Krum + Trimmed Mean hybrid defense against colluding Sybil nodes',
    paper: 'Mhamdi et al. 2018',
    byzantine_robust: true,
    colluding_defense: true,
  },
];

const mockBudgetLogs = [
  {
    simulation_id: 'sim-test-01',
    total_epsilon: 1.85,
    delta: 1e-5,
    rounds_spent: 10,
    epsilon_per_round: 0.185,
    epsilon_history: [0.185, 0.37, 0.555, 0.74, 0.925, 1.11, 1.295, 1.48, 1.665, 1.85],
    budget_exhausted: false,
    epsilon_limit: 8.0,
  },
];

const mockBankBudgets = {
  consortium_target_epsilon: 4.0,
  consortium_target_delta: 1e-5,
  total_nodes_active: 3,
  any_budget_exceeded: false,
  training_circuit_breaker_active: false,
  frozen_by_node: null,
  frozen_at: null,
  freeze_reason: null,
  node_budgets: [
    {
      node_id: 'bank-a',
      bank_name: 'Meridian Trust',
      tier: 'Tier 1 Global Systemic',
      rounds_completed: 10,
      cumulative_epsilon: 1.85,
      target_epsilon: 4.0,
      target_delta: 1e-5,
      budget_exhaustion_pct: 46.25,
      is_budget_exceeded: false,
      optimal_alpha_order: 14,
      calibrated_sigma: 1.12,
      risk_tier: 'safe',
    },
  ],
  global_cumulative_rdp: { '14': 0.132 },
  updated_at: '2026-10-01T12:00:00Z',
};

let queryClient: QueryClient;

const createWrapper = () => {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
};

describe('PrivacyDefensePage', () => {
  beforeEach(() => {
    vi.spyOn(queries, 'useAggregationMethods').mockReturnValue({
      data: mockMethods,
      isLoading: false,
      isError: false,
    } as any);

    vi.spyOn(queries, 'usePrivacyBudgetLog').mockReturnValue({
      data: mockBudgetLogs,
      isLoading: false,
      isError: false,
    } as any);

    vi.spyOn(queries, 'useBankRDPBudgets').mockReturnValue({
      data: mockBankBudgets,
      isLoading: false,
      isError: false,
    } as any);
  });

  afterEach(() => {
    if (queryClient) {
      queryClient.cancelQueries();
      queryClient.clear();
    }
    vi.restoreAllMocks();
  });
  it('renders privacy defense header and attack audit modules', () => {
    render(<PrivacyDefensePage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Privacy Defense & Byzantine Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/Zero Raw PII Verified/i)).toBeInTheDocument();
    expect(screen.getByText(/Byzantine Defenses/i)).toBeInTheDocument();
    expect(screen.getByText(/Attack Audits/i)).toBeInTheDocument();
    expect(screen.getByText(/Privacy Budget Log/i)).toBeInTheDocument();
  });

  it('renders attack audits tab with MIA empirical simulator', async () => {
    render(<PrivacyDefensePage />, { wrapper: createWrapper() });

    const auditTabBtn = screen.getByRole('button', { name: /Attack Audits/i });
    fireEvent.click(auditTabBtn);

    expect(await screen.findByText(/Adversarial Privacy Attack Evaluators/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Membership Inference Attack \(MIA\)/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Membership Inference Attack \(MIA\) Empirical Simulator/i)).toBeInTheDocument();
    expect(screen.getByText(/Differential Privacy Noise Level/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run MIA Audit/i })).toBeInTheDocument();
  });

  it('renders privacy budget log tab with circuit breaker and bank RDP accountants', async () => {
    render(<PrivacyDefensePage />, { wrapper: createWrapper() });

    const budgetTabBtn = screen.getByRole('button', { name: /Privacy Budget Log/i });
    fireEvent.click(budgetTabBtn);

    expect(await screen.findByText(/Enterprise Privacy Budget Audit Log \(DP-SGD ε\)/i)).toBeInTheDocument();
    expect(screen.getByText(/CONSORTIUM SAFETY LOCK/i)).toBeInTheDocument();
    expect(screen.getByText(/Bank-Level Rényi DP Accountants/i)).toBeInTheDocument();
    expect(screen.getByText(/DP-SGD Noise Calibration & Privacy-Utility Frontier/i)).toBeInTheDocument();
    expect(screen.getByText(/Phase 15.1 — RDP Moments Accountant/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Emergency Safety Freeze/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset All Budgets/i })).toBeInTheDocument();
  });
});

