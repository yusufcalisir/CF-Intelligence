import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Predictor } from '../Predictor';
import * as api from '../../services/api';

describe('Predictor Component (User Interaction)', () => {
  it('renders form inputs for transaction attributes and submit button', () => {
    render(<Predictor />);

    expect(screen.getByText(/Real-Time Risk Scoring Engine/i)).toBeInTheDocument();
    expect(screen.getByText(/Evaluate Single ISO 20022 Financial Transaction Payload/i)).toBeInTheDocument();
    expect(screen.getByText(/Transaction Amount \(\$\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Merchant Category/i)).toBeInTheDocument();
    expect(screen.getByText(/Country Jurisdiction/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Evaluate Transaction Risk/i })).toBeInTheDocument();
  });

  it('allows user to change form values and submit transaction for risk scoring', async () => {
    const user = userEvent.setup();

    const mockPredictResponse = {
      fraud_probability: 0.88,
      risk_score: 880.0,
      is_fraud_suspected: true,
      risk_level: 'CRITICAL' as const,
      breakdown: { base_model: 0.85, gnn_embedding: 0.92 },
      alert_details: {
        alert_id: 'ALT-TEST-99',
        severity: 'CRITICAL' as const,
        reason_codes: ['HIGH-AMOUNT', 'HIGH-RISK-JURISDICTION'],
        explanation: 'Transaction flagged due to anomalous velocity.',
        top_features: [
          { feature: 'country_code', contribution: 0.42 },
          { feature: 'transaction_amount', contribution: 0.35 },
          { feature: 'velocity', contribution: 0.23 },
        ],
      },
      policy_action: 'BLOCK_AND_FLAG' as const,
      triggered_rules: ['RULE-001-HIGH-RISK-COUNTRY'],
    };

    vi.spyOn(api, 'predictTransaction').mockResolvedValue(mockPredictResponse);

    render(<Predictor />);

    const amountInput = screen.getByDisplayValue('15000');
    await user.clear(amountInput);
    await user.type(amountInput, '25000');

    const submitBtn = screen.getByRole('button', { name: /Evaluate Transaction Risk/i });
    await user.click(submitBtn);

    expect(await screen.findByText(/880.0 \/ 1000/i)).toBeInTheDocument();
    expect(screen.getByText(/BLOCK_AND_FLAG/i)).toBeInTheDocument();
    expect(screen.getByText(/SHAP Feature Contributions/i)).toBeInTheDocument();
    expect(screen.getByText(/country_code/i)).toBeInTheDocument();
  });
});
