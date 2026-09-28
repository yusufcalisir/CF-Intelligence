import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CounterfactualExplorer } from '../CounterfactualExplorer';
import * as api from '../../../services/api';

describe('CounterfactualExplorer Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockReport = {
    alert_id: 'alt_1001',
    original_score: 820.0,
    remediated_score: 290.0,
    is_cleared: true,
    changes: [
      {
        feature: 'country_code',
        original_value: 'KP',
        suggested_value: 'US',
        delta: -250,
        description: 'Originate transaction from domestic home country (US) instead of high-risk jurisdiction',
      },
      {
        feature: 'transaction_amount',
        original_value: '$15,000.00',
        suggested_value: '$50.00',
        delta: -280,
        description: 'Reduce transaction amount to $50.00 within standard profile',
      },
    ],
    summary_text: 'This alert was CLEARED to 290.0/1000 via verified engine re-scoring.',
  };

  it('renders immutable attribute guardrails and domain bounded sliders', async () => {
    vi.spyOn(api, 'fetchCounterfactual').mockResolvedValue(mockReport);

    render(<CounterfactualExplorer alertId="alt_test_01" initialScore={820.0} />);

    expect(screen.getByText(/Counterfactual Explorer/i)).toBeInTheDocument();
    expect(screen.getByText(/Immutable Attribute Guardrails/i)).toBeInTheDocument();
    expect(screen.getByText(/Customer National ID Hash/i)).toBeInTheDocument();
    expect(screen.getByText(/Account Tenure \(Days\)/i)).toBeInTheDocument();
    expect(screen.getByText(/0% Mutation Permitted/i)).toBeInTheDocument();

    // Check sliders
    expect(screen.getByText(/Transaction Monetary Amount \(\$\):/i)).toBeInTheDocument();
    expect(screen.getByText(/Burst Velocity \(txns\/hr\):/i)).toBeInTheDocument();
    expect(screen.getByText(/Merchant Risk Factor:/i)).toBeInTheDocument();

    // Await report resolution
    await waitFor(() => {
      expect(screen.getByText(/Verified Remediation Steps/i)).toBeInTheDocument();
      expect(screen.getByText(/2 Action\(s\)/i)).toBeInTheDocument();
    });
  });

  it('displays re-inference telemetry with risk reduction and cleared badge', async () => {
    vi.spyOn(api, 'fetchCounterfactual').mockResolvedValue(mockReport);

    render(<CounterfactualExplorer alertId="alt_test_01" initialScore={820.0} />);

    await waitFor(() => {
      expect(screen.getByText(/Live Re-Inference Telemetry/i)).toBeInTheDocument();
      expect(screen.getByText(/CLEARED \(ALLOW\)/i)).toBeInTheDocument();
      expect(screen.getByText(/-530.0 pts/i)).toBeInTheDocument();
    });
  });

  it('allows user to run re-inference on button click', async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(api, 'fetchCounterfactual').mockResolvedValue(mockReport);

    render(<CounterfactualExplorer alertId="alt_test_01" initialScore={820.0} />);

    const runBtn = await screen.findByRole('button', { name: /Run Re-Inference/i });
    await user.click(runBtn);

    expect(fetchSpy).toHaveBeenCalled();
  });

  it('displays error message when re-inference fails', async () => {
    vi.spyOn(api, 'fetchCounterfactual').mockRejectedValue(new Error('Domain connection timeout'));

    render(<CounterfactualExplorer alertId="alt_test_01" initialScore={820.0} />);

    await waitFor(() => {
      expect(screen.getByText(/Domain connection timeout/i)).toBeInTheDocument();
    });
  });
});
