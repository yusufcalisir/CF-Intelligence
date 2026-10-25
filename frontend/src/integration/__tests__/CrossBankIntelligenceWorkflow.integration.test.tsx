import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ExplainabilityPanel } from '../../pages/AlertsPage';
import { apiClient } from '../../api/client';
import * as queries from '../../api/queries';

describe('Integration: Cross-Bank Operational PSI Intelligence & Case Evidence Attachment', () => {
  const mockAlerts = [
    {
      id: 'alt_integ_881',
      bank_id: 'bank_a',
      transaction_id: 'tx_mule_881',
      risk_score: 910,
      severity: 'critical' as const,
      status: 'NEW',
      created_at: '2026-10-25T14:00:00Z',
      reason_codes: ['CROSS_BORDER_MULE_RING', 'UNUSUAL_VELOCITY'],
      confidence: 0.95,
      involved_entity_ids: ['ent_case_target_a'],
      model_confidence: 0.95,
      top_features: [{ feature: 'outflow_velocity', contribution: 0.65 }],
      risk_factors: ['High Velocity Outflow'],
    },
  ];

  const mockCases = [
    {
      id: 'case_aml_881',
      title: 'Cross-Bank Syndicate Investigation #881',
      status: 'UNDER_INVESTIGATION',
      priority: 'CRITICAL',
      assigned_analyst: 'sarah.analyst@bank-a.internal',
      created_at: '2026-10-25T14:30:00Z',
      alert_ids: ['alt_integ_881'],
      evidence_count: 0,
    },
  ];

  const mockOperationalSignal = {
    signal_id: 'sig_operational_881',
    consortium_id: 'c_docker_psi',
    requesting_bank_id: 'bank_a',
    query_entity_id: 'ent_case_target_a',
    privacy_hash_truncated: 'ent_case...[BLINDED]',
    match_confirmed: true,
    match_category: 'PROBABILISTIC_FUZZY',
    match_confidence: 0.999999999,
    contributing_institutions_count: 1,
    contributing_institutions: ['bank_b'],
    disclosure_mode: 'ATTRIBUTED_INSTITUTIONS',
    sanitized_risk_level: 'HIGH',
    sanitized_typology_indicators: ['CROSS_BANK_ENTITY_OBSERVED', 'CYBER_FRAUD'],
    execution_mode: 'VERIFIED_OPERATIONAL',
    attestation_status: 'ISOLATED_MULTI_PROCESS',
    is_authoritative: true,
    is_synthetic: false,
    provenance: {
      query_timestamp: '2026-10-25T14:35:00Z',
      execution_environment: 'ISOLATED_MULTI_PROCESS_WORKER_NODES',
      operational_status: 'VERIFIED_OPERATIONAL',
      protocol: 'OPENMINED-PSI-2.0.6-ECDH',
      ticket_id: 'ticket_881',
      receipt_id: 'receipt_881',
      fpr: 1e-9,
      probabilistic_uncertainty_note:
        'Intersection detected via ECDH PSI with Golomb Compressed Sets. Subject to calibrated false positive bound 1e-9.',
    },
  };

  const mockAttachResponse = {
    evidence_id: 'ev_cross_bank_881',
    case_id: 'case_aml_881',
    signal_id: 'sig_operational_881',
    content_hash: 'c8f71239ab87e9123048bcae1928374918237491823749182374918237491283',
    attached_at: '2026-10-25T14:36:00Z',
    status: 'ATTACHED',
  };

  const renderComponent = () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ExplainabilityPanel alert={mockAlerts[0] as any} />
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useAlerts').mockReturnValue({
      data: mockAlerts,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(queries, 'useCases').mockReturnValue({
      data: mockCases,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(queries, 'useAlert').mockReturnValue({
      data: mockAlerts[0],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(queries, 'useAlertExplainability').mockReturnValue({
      data: {
        alert_id: 'alt_integ_881',
        risk_factors: ['High Velocity Outflow'],
        top_features: [{ feature: 'outflow_velocity', contribution: 0.65 }],
      },
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(queries, 'useAlertCounterfactuals').mockReturnValue({
      data: { changes: [] },
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(queries, 'useAlertDecisionReplay').mockReturnValue({
      data: { events: [] },
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(queries, 'useAlertGNNExplanation').mockReturnValue({
      data: {
        subgraph_nodes_count: 5,
        subgraph_edges_count: 8,
        primary_driver_text: 'Cross-bank structuring detected',
        top_contributing_edges: [],
      },
      isLoading: false,
      error: null,
    } as any);
  });

  it('renders Consortium Intel tab, triggers verified operational PSI query, displays probabilistic match badge, and attaches evidence to case', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockImplementation(async (url: string, _payload: any) => {
      if (url === '/api/v1/intelligence/cross-bank/query') {
        return { data: mockOperationalSignal };
      }
      if (url.includes('/evidence/cross-bank-intelligence')) {
        return { data: mockAttachResponse };
      }
      return { data: {} };
    });

    renderComponent();

    // 1. Click Consortium Intel tab
    const tabBtn = await screen.findByText(/Consortium Intel/i);
    fireEvent.click(tabBtn);

    // 2. Verify Operational PSI controls and initiate query
    expect(await screen.findByText(/Authorized Consortium Matching/i)).toBeInTheDocument();
    expect(screen.getByText(/Zero-Raw-PII Boundary/i)).toBeInTheDocument();

    const queryBtn = screen.getByRole('button', {
      name: /Query Cross-Bank Intelligence \(Operational PSI\)/i,
    });
    expect(queryBtn).toBeEnabled();
    fireEvent.click(queryBtn);

    // 3. Assert API call payload contract
    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(
        '/api/v1/intelligence/cross-bank/query',
        expect.objectContaining({
          requesting_bank_id: 'bank_a',
          query_entity_id: 'ent_case_target_a',
          execution_mode: 'VERIFIED_OPERATIONAL',
          require_established_psi: true,
        })
      );
    });

    // 4. Assert Probabilistic Match Badge and Calibrated Uncertainty Note
    await waitFor(() => {
      expect(screen.getByText(/PROBABILISTIC MATCH \(FPR 1e-9\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Verified Operational Enclave \(OpenMined PSI\)/i)).toBeInTheDocument();
    });

    // 5. Attach evidence to the investigation case
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Anchor to Case/i })).toBeInTheDocument();
    });
    const attachBtn = screen.getByRole('button', { name: /Anchor to Case/i });
    fireEvent.click(attachBtn);

    // 6. Verify Evidence Registry API call
    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(
        '/api/v1/cases/case_aml_881/evidence/cross-bank-intelligence',
        expect.objectContaining({
          signal_id: 'sig_operational_881',
        })
      );
    });

    // 7. Assert that cryptographic content hash is surfaced in the UI
    expect(await screen.findByText(/SHA-256 Content Hash Anchored/i)).toBeInTheDocument();
    expect(screen.getByTitle(mockAttachResponse.content_hash)).toBeInTheDocument();
    expect(screen.getByText(/Anchored to Case #case_aml/i)).toBeInTheDocument();
  });

  it('fails closed and displays error banner when consortium consent is revoked or unavailable', async () => {
    vi.spyOn(apiClient, 'post').mockImplementation(async (url: string) => {
      if (url === '/api/v1/intelligence/cross-bank/query') {
        const error = new Error('ConsortiumSecurityError: Adoption rejection: Bilateral grant consent revoked.');
        (error as any).response = {
          status: 400,
          data: {
            error: 'ConsortiumSecurityError: Adoption rejection: Bilateral grant consent revoked.',
            error_type: 'ConsortiumSecurityError',
          },
        };
        throw error;
      }
      return { data: {} };
    });

    renderComponent();

    const tabBtn = await screen.findByText(/Consortium Intel/i);
    fireEvent.click(tabBtn);

    const queryBtn = screen.getByRole('button', {
      name: /Query Cross-Bank Intelligence \(Operational PSI\)/i,
    });
    fireEvent.click(queryBtn);

    // Verify Error State Display in UI
    await waitFor(() => {
      expect(screen.getByText(/Query Execution Failed/i)).toBeInTheDocument();
      expect(
        screen.getByText(/ConsortiumSecurityError: Adoption rejection: Bilateral grant consent revoked/i)
      ).toBeInTheDocument();
    });
  });
});
