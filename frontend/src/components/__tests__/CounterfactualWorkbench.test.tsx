import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { CounterfactualWorkbench } from '../CounterfactualWorkbench';
import * as api from '../../services/api';

describe('CounterfactualWorkbench Component (User Interaction & Deep Linking)', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    });
  });

  const renderWorkbench = (initialEntries: string[] = ['/workbench']) => {
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <CounterfactualWorkbench />
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  it('renders initial parameters, risk score gauge, and slider controls', () => {
    renderWorkbench();

    expect(screen.getByText(/Counterfactual Remediation Workbench/i)).toBeInTheDocument();
    expect(screen.getByText(/Interactive Minimum Remediating Feature Path Simulator/i)).toBeInTheDocument();
    expect(screen.getByText(/Transaction Amount \(\$\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Hourly Velocity \(txns\/hr\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Merchant Risk Category/i)).toBeInTheDocument();
    expect(screen.getByText(/Simulate Optimal Counterfactual Path/i)).toBeInTheDocument();
  });

  it('reads ?alert_id=... from URL search params and updates active target alert', () => {
    renderWorkbench(['/workbench?alert_id=alt_9002']);

    expect(screen.getByText(/Counterfactual Remediation Workbench/i)).toBeInTheDocument();
    // Verify alert selector or alert identifier is active
    expect(screen.getByDisplayValue('alt_9002')).toBeInTheDocument();
  });

  it('allows user to simulate optimal counterfactual path on button click', async () => {
    const user = userEvent.setup();

    const mockReport = {
      alert_id: 'alt_1001',
      original_score: 820.0,
      target_score: 350.0,
      remediated_score: 345.0,
      is_cleared: true,
      changes: [
        {
          feature: 'velocity',
          original_value: 28,
          suggested_value: 5,
          delta: -23,
          description: 'Reduce hourly transaction burst rate below 10 txns/hr.',
        },
        {
          feature: 'transaction_amount',
          original_value: 15000,
          suggested_value: 4200,
          delta: -10800,
          description: 'Lower payment amount under single-transaction AML threshold.',
        },
      ],
      summary_text: 'Optimal minimal perturbation path drops risk score from 820 to 345.',
    };

    vi.spyOn(api, 'fetchCounterfactual').mockResolvedValue(mockReport);

    renderWorkbench();

    const simulateBtn = screen.getByRole('button', { name: /Simulate Optimal Counterfactual Path/i });
    await user.click(simulateBtn);

    expect(api.fetchCounterfactual).toHaveBeenCalledWith(
      expect.objectContaining({
        alert_id: 'alt_1001',
        target_score: 350.0,
        amount: 15000,
        velocity: 28,
        merchant_risk: 0.95,
      })
    );

    expect(await screen.findByText(/Remediation Action Path/i)).toBeInTheDocument();
    expect(screen.getByText(/Reduce hourly transaction burst rate/i)).toBeInTheDocument();
    expect(screen.getByText(/Optimal minimal perturbation path/i)).toBeInTheDocument();

    // Verify "Apply Suggested Values" button is present and clickable
    const applyBtn = screen.getByRole('button', { name: /Apply Suggested Values/i });
    expect(applyBtn).toBeInTheDocument();
    await user.click(applyBtn);

    // Verify amount is updated to suggested value ($4,200)
    expect(screen.getByText(/\$4,200/i)).toBeInTheDocument();
  });

  it('displays error banner when counterfactual simulation fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'fetchCounterfactual').mockRejectedValue(new Error('Backend ML engine offline'));

    renderWorkbench();

    const simulateBtn = screen.getByRole('button', { name: /Simulate Optimal Counterfactual Path/i });
    await user.click(simulateBtn);

    expect(await screen.findByText(/Counterfactual Simulation Error/i)).toBeInTheDocument();
    expect(screen.getByText(/Backend ML engine offline/i)).toBeInTheDocument();
  });

  it('resets sliders and clears remediation report when user clicks reset button', async () => {
    const user = userEvent.setup();
    renderWorkbench();

    const resetBtn = screen.getByRole('button', { name: /Reset Parameters/i });
    await user.click(resetBtn);

    expect(screen.getByText(/\$15,000/i)).toBeInTheDocument();
    expect(screen.getByText(/28 txns/i)).toBeInTheDocument();
  });

  it('switches parameters when clicking preset scenario buttons', async () => {
    const user = userEvent.setup();
    renderWorkbench();

    const smurfingBtn = screen.getByRole('button', { name: /Smurfing Burst/i });
    await user.click(smurfingBtn);

    expect(screen.getAllByText(/\$25,000/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/35 txns/i)).toBeInTheDocument();

    const retailBtn = screen.getByRole('button', { name: /Low-Risk Retail/i });
    await user.click(retailBtn);

    expect(screen.getByText(/\$1,200/i)).toBeInTheDocument();
    expect(screen.getByText(/4 txns/i)).toBeInTheDocument();
  });
});
