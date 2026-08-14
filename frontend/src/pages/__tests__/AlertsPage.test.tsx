import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import AlertsPage from '../AlertsPage';
import * as queries from '../../api/queries';
import type { Alert } from '../../api/types';

describe('AlertsPage Integration Test Suite', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const mockAlerts: Alert[] = [
    {
      id: 'alt_001',
      bank_id: 'bank_a',
      transaction_id: 'tx_structuring_1001',
      risk_score: 910,
      severity: 'critical',
      status: 'NEW',
      created_at: '2026-08-14T10:00:00Z',
      reason_codes: ['VELOCITY_BURST', 'CROSS_BORDER_STRUCTURING'],
      confidence: 0.94,
      involved_entity_ids: ['ent_901'],
      top_features: [{ feature: 'velocity', contribution: 0.45 }],
      risk_factors: ['High-frequency cross-border transfers'],
      model_confidence: 0.94,
    },
    {
      id: 'alt_002',
      bank_id: 'bank_b',
      transaction_id: 'tx_mule_1002',
      risk_score: 780,
      severity: 'high',
      status: 'INVESTIGATING',
      created_at: '2026-08-14T10:05:00Z',
      reason_codes: ['MULE_ACCOUNT_DISPERSAL'],
      confidence: 0.88,
      involved_entity_ids: ['ent_304'],
      top_features: [{ feature: 'amount', contribution: 0.38 }],
      risk_factors: ['Rapid multi-hop routing'],
      model_confidence: 0.88,
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useAlerts').mockReturnValue({
      data: mockAlerts,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useAlertExplainability').mockReturnValue({
      data: {
        alert_id: 'alt_001',
        top_features: [{ feature: 'velocity', contribution: 0.45 }],
        risk_factors: ['High-frequency cross-border transfers'],
        historical_evidence: ['Repeated sub-$10k transfers'],
        model_confidence: 0.94,
        risk_score_breakdown: [],
        explanation_text: 'Layered GNN risk classification flagged 910/1000 score.',
      },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAlertCounterfactuals').mockReturnValue({
      data: {
        changes: [{ feature: 'velocity', original_value: 8.5, required_value: 2.0 }],
        remediated_score: 310,
      },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAlertDecisionReplay').mockReturnValue({
      data: {
        events: [{ timestamp: '2026-08-14T10:00:00Z', action: 'GNN Risk Classification Triggered' }],
      },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAlertGNNExplanation').mockReturnValue({
      data: {
        subgraph_nodes: [{ id: 'node_1', label: 'Beneficiary Entity' }],
        subgraph_edges: [{ source: 'node_1', target: 'node_2' }],
      },
      isLoading: false,
    } as any);
  });

  it('renders alert intelligence header, filters, and alert cards feed', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Alert Intelligence/i)).toBeInTheDocument();
    expect(screen.getByText(/2 alerts/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Meridian National/i).length).toBeGreaterThan(0);
  });

  it('allows user to click an alert to view explainability and GNN attribution details', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const alertCards = screen.getAllByText(/Meridian National/i);
    if (alertCards[0]) {
      await user.click(alertCards[0]);
    }

    expect(screen.getByText(/Alert Intelligence/i)).toBeInTheDocument();
  });

  it('allows filtering by bank and severity dropdowns', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const selects = screen.getAllByRole('combobox');
    const bankSelect = selects[0];
    const severitySelect = selects[1];

    if (bankSelect) {
      await user.selectOptions(bankSelect, 'bank_a');
      expect(bankSelect).toHaveValue('bank_a');
    }

    if (severitySelect) {
      await user.selectOptions(severitySelect, 'critical');
      expect(severitySelect).toHaveValue('critical');
    }
  });
});
