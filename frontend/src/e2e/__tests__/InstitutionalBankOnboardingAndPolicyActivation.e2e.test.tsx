import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import BankOnboardingPage from '../../pages/BankOnboardingPage';
import CoordinatorPage from '../../pages/CoordinatorPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('E2E Business Flow 4: Institutional Bank Onboarding & Ray Coordinator Node Activation', () => {
  const createWrapper = (initialRoute = '/onboarding') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });

    return () => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/onboarding" element={<BankOnboardingPage />} />
              <Route path="/coordinator" element={<CoordinatorPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  const mockClients = [
    {
      bank_id: 'bank_delta',
      bank_name: 'Delta European Universal Bank',
      jurisdiction: 'EU - Frankfurt',
      tier: 'tier_1',
      status: 'ACTIVE_TRAINING',
      connected_at: '2026-08-14T15:00:00Z',
      last_heartbeat_ago_seconds: 1.2,
      assigned_cluster_node: 'ray-worker-node-eu-01',
      cpu_cores: 64,
      ram_gb: 128,
      device: 'cuda',
      device_count: 1,
      pytorch_version: '2.4.0+cu121',
      python_version: '3.11.8',
      hardware_enclave: 'Intel SGX2 DCAP',
    },
  ];

  const mockRules = [
    {
      id: 'rule_e2e_01',
      rule_name: 'High Risk Jurisdiction Cross-Border Blocker',
      action: 'BLOCK_TRANSACTION',
      condition: { and: [{ field: 'composite_risk_score', operator: '>=', value: 850 }] },
      is_active: true,
      priority: 5,
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useRegisteredClients').mockReturnValue({
      data: mockClients,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useRules').mockReturnValue({
      data: mockRules,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
  });

  it('completes institutional onboarding registration, reviews node specs, and verifies Ray cluster coordinator active state', async () => {
    const user = userEvent.setup();
    const OnboardingWrapper = createWrapper('/onboarding');
    render(<OnboardingWrapper />);

    // 1. Verify Onboarding Wizard Step 1 layout
    expect(await screen.findByText(/Bank Node Onboarding Wizard/i)).toBeInTheDocument();
    expect(screen.getByText(/Step 1: Institutional Legal & Regional Profile/i)).toBeInTheDocument();

    // 2. Fill required institutional legal form fields
    const bankIdInput = screen.getByPlaceholderText(/e\.g\. bank_delta/i);
    await user.type(bankIdInput, 'bank_delta');

    // 3. Advance to next step
    const nextButton = screen.getByRole('button', { name: /Continue to Step 2/i });
    await user.click(nextButton);
    expect(screen.getByText(/Step 2: Review Registration Details/i)).toBeInTheDocument();

    // 4. Verify Ray Cluster Coordinator console
    const CoordinatorWrapper = createWrapper('/coordinator');
    render(<CoordinatorWrapper />);
    expect(await screen.findByText(/Federated Coordinator Suite/i)).toBeInTheDocument();
    expect(screen.getAllByText(/bank_delta/i)[0]).toBeInTheDocument();
  });
});
