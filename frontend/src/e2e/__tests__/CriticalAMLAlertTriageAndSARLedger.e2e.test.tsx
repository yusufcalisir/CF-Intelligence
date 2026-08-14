import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import AlertsPage from '../../pages/AlertsPage';
import CasesPage from '../../pages/CasesPage';
import CaseDetailPage from '../../pages/CaseDetailPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('E2E Business Flow 2: Critical AML Alert Triage & Cryptographic Case Ledger', () => {
  const createWrapper = (initialRoute = '/alerts') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });

    return () => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/alerts" element={<AlertsPage />} />
              <Route path="/cases" element={<CasesPage />} />
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  const mockAlerts = [
    {
      id: 'alt_9912',
      account_id: 'acc_meridian_7721',
      bank_id: 'bank_a',
      amount: 9850,
      timestamp: '2026-08-14T12:30:00Z',
      risk_score: 940,
      severity: 'critical',
      status: 'NEW',
      is_synthetic: false,
      confidence: 0.965,
      reason_codes: ['CROSS_BORDER_STRUCTURING', 'RAPID_OUTFLOW_VELOCITY'],
      involved_entity_ids: ['acc_meridian_7721', 'acc_apex_3310', 'dev_fp_9921'],
      model_confidence: 0.965,
      top_features: [{ feature: 'velocity_1h', importance: 0.42 }],
    },
  ];

  const mockCases = [
    {
      id: 'case_aml_707',
      title: 'Decentralized Cross-Bank Smurfing Syndicate #707',
      status: 'UNDER_INVESTIGATION',
      priority: 'CRITICAL',
      assigned_analyst: 'sarah.connor@fincrime.org',
      created_at: '2026-08-14T13:00:00Z',
      alert_ids: ['alt_9912'],
      notes: [{ id: 'n1', author: 'Sarah C.', text: 'Identified 3-bank coordinated ring under $10k limit.', created_at: '2026-08-14T13:05:00Z' }],
      evidence_items: [
        {
          id: 'ev_01',
          evidence_type: 'SAR_TELEMETRY',
          title: 'GraphSAGE Ring Subgraph Topology Snapshot',
          file_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
          content_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
          uploaded_by: 'sarah.connor@fincrime.org',
          uploaded_at: '2026-08-14T13:10:00Z',
        },
      ],
      timeline: [
        { id: 't1', event_type: 'CASE_CREATED', description: 'AML Case opened from Alert alt_9912', timestamp: '2026-08-14T13:00:00Z' },
      ],
    },
  ];

  const mockEvidenceList = [
    {
      id: 'ev_01',
      title: 'GraphSAGE Ring Subgraph Topology Snapshot',
      evidence_type: 'SAR_TELEMETRY',
      file_path: 'evidence/graphsage_ring_707.json',
      file_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      content_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      uploaded_by: 'sarah.connor@fincrime.org',
      uploaded_at: '2026-08-14T13:10:00Z',
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useAlerts').mockReturnValue({
      data: mockAlerts,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCases').mockReturnValue({
      data: mockCases,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCase').mockReturnValue({
      data: mockCases[0],
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
      data: mockEvidenceList,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useAddEvidence').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ id: 'ev_new_02', file_hash: 'a1b2c3d4e5f6' }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAlertExplainability').mockReturnValue({
      data: {
        top_features: [{ feature: 'velocity_1h', score: 0.42, importance: 0.42 }],
        risk_factors: ['Cross-border velocity structuring', 'New device fingerprint'],
        risk_score_breakdown: [
          { signal_name: 'Rapid_Velocity', score: 85, weight: 0.4 },
          { signal_name: 'Amount_Structuring', score: 92, weight: 0.6 },
        ],
      },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAlertCounterfactuals').mockReturnValue({
      data: { counterfactuals: [] },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAlertDecisionReplay').mockReturnValue({
      data: { replay_steps: [] },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAlertGNNExplanation').mockReturnValue({
      data: { nodes: [], edges: [] },
      isLoading: false,
    } as any);
  });

  it('triages critical multi-institution AML alert and inspects AI Explainability Portal and GNN Graph breakdown', async () => {
    const user = userEvent.setup();
    const AlertsWrapper = createWrapper('/alerts');
    render(<AlertsWrapper />);

    // 1. Verify Alert stream presents critical multi-bank alert
    expect(await screen.findByText(/Alert Intelligence/i)).toBeInTheDocument();
    expect(screen.getAllByText(/critical/i)[0]).toBeInTheDocument();
    expect(screen.getByText(/CROSS_BORDER_STRUCTURING/i)).toBeInTheDocument();

    // 2. Open Alert drilldown
    const alertCard = screen.getByText(/CROSS_BORDER_STRUCTURING/i);
    await user.click(alertCard);
    const portals = await screen.findAllByText(/AI Explainability Portal/i);
    expect(portals[0]).toBeInTheDocument();
  });

  it('inspects case ledger and verifies immutable cryptographic SHA-256 evidence item', async () => {
    const CaseDetailWrapper = createWrapper('/cases/case_aml_707');
    render(<CaseDetailWrapper />);

    // 1. Verify Case details view
    expect(await screen.findByText(/Decentralized Cross-Bank Smurfing Syndicate #707/i)).toBeInTheDocument();
    expect(screen.getAllByText(/GraphSAGE Ring Subgraph Topology Snapshot/i)[0]).toBeInTheDocument();

    // 2. Verify immutable cryptographic SHA-256 hash badge
    expect(screen.getAllByText(/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/i)[0]).toBeInTheDocument();
  });
});
