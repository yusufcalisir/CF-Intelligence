import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import BankOnboardingPage from '../../pages/BankOnboardingPage';
import CoordinatorPage from '../../pages/CoordinatorPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('Integration: Bank Onboarding Wizard & Ray Coordinator Cluster', () => {
  const createWrapper = (initialRoute = '/onboarding') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    return ({ children }: { children?: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/onboarding" element={<BankOnboardingPage />} />
              <Route path="/coordinator" element={<CoordinatorPage />} />
            </Route>
          </Routes>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useRegisteredClients').mockReturnValue({
      data: [
        {
          bank_id: 'bank_alpha',
          status: 'ONLINE',
          pytorch_version: '2.2.0+cu121',
          python_version: '3.12.1',
          ram_gb: 64.0,
          hardware_type: 'cuda',
          device_count: 2,
          last_heartbeat_ago_seconds: 1.2,
        },
      ],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useNegotiatedParams').mockReturnValue({
      data: {
        batch_size: 32,
        learning_rate: 0.001,
        local_epochs: 3,
        privacy_budget_per_round: 0.2,
        aggregation_algorithm: 'FED_AVG_WEIGHTED',
        min_client_fraction: 1.0,
      },
      isLoading: false,
    } as any);
  });

  it('completes step 1 in bank onboarding wizard and advances to step 2 verification', async () => {
    const user = userEvent.setup();
    const Wrapper = createWrapper('/onboarding');
    render(<Wrapper />);

    expect(screen.getByText(/Bank Node Onboarding Wizard/i)).toBeInTheDocument();
    expect(screen.getByText(/Step 1: Institutional Legal & Regional Profile/i)).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText(/e\.g\. Delta International Bank AG/i);
    await user.type(nameInput, 'Horizon Trust Bank NA');

    const nextBtn = screen.getByRole('button', { name: /Continue to Step 2/i });
    await user.click(nextBtn);

    expect(screen.getByText(/Step 2: Review Registration Details/i)).toBeInTheDocument();
  });

  it('renders coordinator dashboard with active Ray cluster status and node nodes', () => {
    const Wrapper = createWrapper('/coordinator');
    render(<Wrapper />);

    expect(screen.getByText(/Federated Coordinator Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/Dynamic client registry/i)).toBeInTheDocument();
  });
});
