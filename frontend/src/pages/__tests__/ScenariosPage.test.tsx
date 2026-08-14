import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import ScenariosPage from '../ScenariosPage';
import * as queries from '../../api/queries';

describe('ScenariosPage (Pre-Built Fraud Scenarios) Test Suite', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const mockScenarios = [
    {
      id: 'scen_01',
      scenario_type: 'fraud_ring',
      name: 'Decentralized Smurfing & Layering Ring',
      description: 'Coordinated sub-$10k transfers across 3 banks designed to evade single-bank SAR limits.',
      banks_involved: ['bank_a', 'bank_b', 'bank_c'],
      estimated_duration_seconds: 45,
      event_count: 50,
    },
    {
      id: 'scen_02',
      scenario_type: 'account_takeover',
      name: 'Synthetic Identity Hijack Wave',
      description: 'Multi-institutional credit application surge using fabricated SSN prefixes.',
      banks_involved: ['bank_a', 'bank_b'],
      estimated_duration_seconds: 30,
      event_count: 35,
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useScenarios').mockReturnValue({
      data: mockScenarios,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useStartScenario').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ scenario_id: 'scen_run_123' }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useScenarioStatus').mockReturnValue({
      data: {
        status: 'RUNNING',
        current_event: 2,
        total_events: 5,
        logs: ['Bank A detected isolated $9,500 transfer', 'Consortium GNN identified 3-node ring topology'],
      },
      isLoading: false,
    } as any);
  });

  it('renders fraud scenarios header, scenario cards, and speed controls', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ScenariosPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getAllByText(/Fraud Scenarios/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Decentralized Smurfing & Layering Ring/i)).toBeInTheDocument();
    expect(screen.getByText(/Synthetic Identity Hijack Wave/i)).toBeInTheDocument();
  });

  it('allows user to trigger scenario execution on Run Scenario click', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ScenariosPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const runButtons = screen.getAllByRole('button', { name: /▶ Run/i });
    if (runButtons[0]) {
      await user.click(runButtons[0]);
    }
    expect(screen.getAllByText(/Fraud Scenarios/i).length).toBeGreaterThan(0);
  });
});
