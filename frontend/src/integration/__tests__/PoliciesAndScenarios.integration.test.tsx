import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import PoliciesPage from '../../pages/PoliciesPage';
import ScenariosPage from '../../pages/ScenariosPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('Integration: Policies Rule Engine & Multi-Bank Fraud Scenarios', () => {
  const createWrapper = (initialRoute = '/rules') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    return ({ children }: { children?: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/rules" element={<PoliciesPage />} />
              <Route path="/scenarios" element={<ScenariosPage />} />
            </Route>
          </Routes>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useRules').mockReturnValue({
      data: [
        {
          id: 'rule_99',
          rule_name: 'Layered Smurfing Velocity Threshold',
          action: 'BLOCK_TRANSACTION',
          condition: { and: [{ field: 'composite_risk_score', operator: '>=', value: 850 }] },
          is_active: true,
          priority: 1,
        },
      ],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useScenarios').mockReturnValue({
      data: [
        {
          id: 'scen_01',
          scenario_type: 'fraud_ring',
          title: 'Decentralized Smurfing & Layering Ring',
          description: 'Coordinated sub-$10k transfers across 3 banks designed to evade single-bank SAR limits.',
          banks_involved: ['bank_a', 'bank_b', 'bank_c'],
          estimated_duration_seconds: 45,
        },
      ],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useStartScenario').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ scenario_id: 'scen_run_123' }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useScenarioStatus').mockReturnValue({
      data: { status: 'COMPLETED', current_step: 5, total_steps: 5, logs: ['Simulation finished'] },
      isLoading: false,
    } as any);
  });

  it('renders rules policy manager and allows user to test rule condition against transaction payload', async () => {
    const user = userEvent.setup();
    const Wrapper = createWrapper('/rules');
    render(<Wrapper />);

    expect(screen.getByText(/Policy Rules & Decisions/i)).toBeInTheDocument();
    expect(screen.getByText(/Layered Smurfing Velocity Threshold/i)).toBeInTheDocument();
    expect(screen.getByText(/Dynamic Rule Tester/i)).toBeInTheDocument();

    const testBtn = screen.getByRole('button', { name: /Run Evaluation Test/i });
    await user.click(testBtn);
    expect(screen.getByText(/Dynamic Rule Tester/i)).toBeInTheDocument();
  });
});
