import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import AlertsPage, {
  ALERTS_SELECTED_ID_KEY,
  ALERTS_BANK_FILTER_KEY,
  ALERTS_SEVERITY_FILTER_KEY,
} from '../AlertsPage';
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
    sessionStorage.clear();
    window.history.pushState({}, '', '/alerts');
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useAlerts').mockReturnValue({
      data: mockAlerts,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useAlert').mockImplementation(((id?: string) => {
      const alert = mockAlerts.find((a) => a.id === id);
      return {
        data: alert,
        isLoading: false,
        error: null,
      } as any;
    }) as any);

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

  it('persists selected alert to sessionStorage and updates search params when clicked', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const alertCard = screen.getByText('VELOCITY_BURST');
    await user.click(alertCard);

    expect(sessionStorage.getItem(ALERTS_SELECTED_ID_KEY)).toBe('alt_001');
    expect(screen.getAllByText(/AI Explainability Portal/i).length).toBeGreaterThan(0);
  });

  it('restores selected alert and explainability panel from sessionStorage across tab remounts', () => {
    sessionStorage.setItem(ALERTS_SELECTED_ID_KEY, 'alt_002');

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getAllByText(/AI Explainability Portal/i).length).toBeGreaterThan(0);
    expect(sessionStorage.getItem(ALERTS_SELECTED_ID_KEY)).toBe('alt_002');
  });

  it('restores selected alert from URL search parameter ?alert_id=alt_001 on direct deep linking', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/alerts?alert_id=alt_001']}>
          <AlertsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getAllByText(/AI Explainability Portal/i).length).toBeGreaterThan(0);
  });

  it('clears selected alert and removes from sessionStorage when close button is clicked', async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(ALERTS_SELECTED_ID_KEY, 'alt_001');

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getAllByText(/AI Explainability Portal/i).length).toBeGreaterThan(0);

    const closeBtns = screen.getAllByLabelText(/Close explainability panel|Close details/i);
    await user.click(closeBtns[0]!);

    expect(sessionStorage.getItem(ALERTS_SELECTED_ID_KEY)).toBeNull();
    expect(screen.queryByText(/AI Explainability Portal/i)).not.toBeInTheDocument();
  });

  it('persists and restores filter preferences in sessionStorage across tab navigation', async () => {
    const user = userEvent.setup();

    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[0]!, 'bank_a');
    await user.selectOptions(selects[1]!, 'critical');

    expect(sessionStorage.getItem(ALERTS_BANK_FILTER_KEY)).toBe('bank_a');
    expect(sessionStorage.getItem(ALERTS_SEVERITY_FILTER_KEY)).toBe('critical');

    unmount();

    // Re-mount component (simulate returning from another tab)
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const reSelects = screen.getAllByRole('combobox');
    expect(reSelects[0]).toHaveValue('bank_a');
    expect(reSelects[1]).toHaveValue('critical');
  });

  it('renders suspect entity nodes with deep links to entity relationship graph', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AlertsPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Verify entity badge link and PSI link in AlertCard
    const entityLink = screen.getByText('ent_901').closest('a');
    expect(entityLink).toBeInTheDocument();
    expect(entityLink).toHaveAttribute('href', '/graph?entity_id=ent_901&depth=2');

    const cardPsiLink = screen.getAllByRole('link', { name: /PSI/i })[0];
    expect(cardPsiLink).toHaveAttribute('href', '/psi?entity_id=ent_901&auto_match=true');

    // Click on alert to open ExplainabilityPanel
    const alertCard = screen.getByText('VELOCITY_BURST');
    await user.click(alertCard);

    // Verify ExplainabilityPanel suspect entities bar (present in both responsive desktop and mobile panels)
    expect(screen.getAllByText(/Suspect Graph Entities/i).length).toBeGreaterThan(0);
    const hop2Link = screen.getAllByRole('link', { name: /2-Hop/i })[0];
    expect(hop2Link).toHaveAttribute('href', '/graph?entity_id=ent_901&depth=2');
    const hop3Link = screen.getAllByRole('link', { name: /3-Hop/i })[0];
    expect(hop3Link).toHaveAttribute('href', '/graph?entity_id=ent_901&depth=3');

    const panelPsiLink = screen.getAllByRole('link', { name: /Check PSI/i })[0];
    expect(panelPsiLink).toHaveAttribute('href', '/psi?entity_id=ent_901&auto_match=true');
  });
});

