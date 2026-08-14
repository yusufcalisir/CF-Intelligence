import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CounterfactualWorkbench } from '../CounterfactualWorkbench';
import * as api from '../../services/api';

describe('CounterfactualWorkbench Component (User Interaction)', () => {
  it('renders initial parameters, risk score gauge, and slider controls', () => {
    render(<CounterfactualWorkbench />);

    expect(screen.getByText(/Counterfactual Remediation Workbench/i)).toBeInTheDocument();
    expect(screen.getByText(/Interactive Minimum Remediating Feature Path Simulator/i)).toBeInTheDocument();
    expect(screen.getByText(/Transaction Amount \(\$\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Hourly Velocity \(txns\/hr\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Merchant Risk Category/i)).toBeInTheDocument();
    expect(screen.getByText(/Simulate Optimal Counterfactual Path/i)).toBeInTheDocument();
  });

  it('allows user to simulate optimal counterfactual path on button click', async () => {
    const user = userEvent.setup();

    const mockReport = {
      alert_id: 'alt_1001',
      original_score: 820.0,
      target_score: 350.0,
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

    render(<CounterfactualWorkbench />);

    const simulateBtn = screen.getByRole('button', { name: /Simulate Optimal Counterfactual Path/i });
    await user.click(simulateBtn);

    expect(await screen.findByText(/Remediation Action Path/i)).toBeInTheDocument();
    expect(screen.getByText(/Reduce hourly transaction burst rate/i)).toBeInTheDocument();
    expect(screen.getByText(/Optimal minimal perturbation path/i)).toBeInTheDocument();
  });

  it('resets sliders and clears remediation report when user clicks reset button', async () => {
    const user = userEvent.setup();
    render(<CounterfactualWorkbench />);

    const resetBtn = screen.getByRole('button', { name: /Reset Parameters/i });
    await user.click(resetBtn);

    expect(screen.getByText(/\$15,000/i)).toBeInTheDocument();
    expect(screen.getByText(/28 txns/i)).toBeInTheDocument();
  });
});
