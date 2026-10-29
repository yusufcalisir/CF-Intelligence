import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { CrossBankIntelligenceWorkspace } from '../CrossBankIntelligenceWorkspace';
import * as queries from '../../../api/queries';

// Mock clipboard
Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn(),
  },
});

describe('CrossBankIntelligenceWorkspace Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockEntities = [
    {
      entity_id: 'ent_alice_999',
      display_label: 'Alice Smith (Masked)',
      entity_type: 'customer',
      risk_level: 'high',
      bank_id: 'bank_a',
      source_alert_id: 'alt_001',
    },
    {
      entity_id: 'ent_bob_888',
      display_label: 'Bob Jones (Masked)',
      entity_type: 'merchant',
      risk_level: 'medium',
      bank_id: 'bank_a',
      source_alert_id: 'alt_002',
    },
  ];

  const mockConsortia = [
    {
      consortium_id: 'consortium_alpha',
      name: 'Consortium Alpha (Tier-1 AML Federation)',
      status: 'ACTIVE',
      counterparts: [
        {
          counterpart_bank_id: 'bank_b',
          display_name: 'Bank B Node',
          is_authorized: true,
          governance_status: 'ACTIVE',
          permitted_purposes: ['FRAUD_INVESTIGATION'],
          disclosure_modes: ['ANONYMIZED_SUMMARY', 'ATTRIBUTED_INSTITUTIONS'],
          execution_readiness: 'OPERATIONAL',
        },
        {
          counterpart_bank_id: 'bank_c',
          display_name: 'Bank C Node',
          is_authorized: true,
          governance_status: 'ACTIVE',
          permitted_purposes: ['FRAUD_INVESTIGATION'],
          disclosure_modes: ['ANONYMIZED_SUMMARY', 'ATTRIBUTED_INSTITUTIONS'],
          execution_readiness: 'OPERATIONAL',
        },
      ],
      permitted_purposes: ['FRAUD_INVESTIGATION'],
    },
  ];

  const mockPersistedEvidence = [
    {
      evidence_id: 'ev_001',
      case_id: 'case_123',
      title: 'Consortium Intelligence Evidence (CONSORTIUM_ALPHA)',
      content_hash: 'a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890',
      batch_query_id: 'batch_query_999',
      uploaded_at: '2026-10-29T12:00:00Z',
      uploaded_by: 'analyst_alice',
      operational_status: 'OPERATIONAL',
      is_authoritative: true,
      disclosure_mode: 'ANONYMIZED_SUMMARY',
      target_bank_id: null, // Strictly masked
      match_confirmed: true,
      match_category: 'EXACT_MATCH',
      confidence: 0.98,
      risk_level: 'HIGH',
      typology_indicators: ['MULE_BURST', 'HIGH_VELOCITY'],
      analyst_notes: 'Verified cross-bank PSI intersection.',
      verification_state: 'VERIFIED',
      retention_status: 'ACTIVE',
      batch_expires_at: '2026-10-30T12:00:00Z',
      explanation: 'Cryptographically verified evidence.',
    },
    {
      evidence_id: 'ev_002',
      case_id: 'case_123',
      title: 'Consortium Intelligence Evidence (CONSORTIUM_BETA)',
      content_hash: 'f0e1d2c3b4a56789abcdef0123456789abcdef0123456789abcdef0123456789',
      batch_query_id: 'batch_query_expired_001',
      uploaded_at: '2026-10-28T08:00:00Z',
      uploaded_by: 'analyst_alice',
      operational_status: 'HISTORICAL_RECORD',
      is_authoritative: true,
      disclosure_mode: 'ATTRIBUTED_INSTITUTIONS',
      target_bank_id: 'bank_c',
      match_confirmed: true,
      match_category: 'PROBABILISTIC_FUZZY',
      confidence: 0.85,
      risk_level: 'MEDIUM',
      typology_indicators: ['STRUCTURING'],
      analyst_notes: 'Attributed partner match.',
      verification_state: 'INTEGRITY_VERIFIED_ONLY',
      retention_status: 'RETENTION_EXPIRED',
      batch_expires_at: null,
      explanation: 'Expired batch evidence remains permanently immutable.',
    },
  ];

  it('renders workspace with header, tenant badge, and eligible case entities', () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: {
        requesting_bank_id: 'bank_a',
        consortia: [],
      },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAttachMultiCounterpartyEvidence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    expect(screen.getByText(/Cross-Bank Consortium Intelligence Workspace/i)).toBeInTheDocument();
    expect(screen.getByText(/TENANT: BANK_A/i)).toBeInTheDocument();
    expect(screen.getAllByText(/OPERATIONAL ASSURANCE/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/24h Bounded Lifecycle/i)).toBeInTheDocument();

    // Check entity select options
    const select = screen.getByTestId('case-entity-select');
    expect(select).toBeInTheDocument();
    expect(screen.getByText(/Alice Smith \(Masked\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Bob Jones \(Masked\)/i)).toBeInTheDocument();
  });

  it('renders empty callout and disables dispatch when no eligible entities exist', () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: mockConsortia },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAttachMultiCounterpartyEvidence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_empty" bankId="bank_a" />);

    expect(
      screen.getByText(/No institution-owned entities currently linked to this case/i)
    ).toBeInTheDocument();

    const dispatchBtn = screen.getByTestId('dispatch-multi-query-btn');
    expect(dispatchBtn).toBeDisabled();
  });

  it('dispatches multi-counterparty query and renders truthful partial outcomes with anonymous masking', async () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: {
        requesting_bank_id: 'bank_a',
        consortia: mockConsortia,
      },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    const mockQueryResponse = {
      query_id: 'batch_query_abc_123',
      consortium_id: 'consortium_alpha',
      requesting_bank_id: 'bank_a',
      query_entity_id: 'ent_alice_999',
      counterpart_count: 2,
      overall_outcome: 'PARTIAL_FAILURE',
      outcome: 'PARTIAL_FAILURE',
      executed_at: '2026-10-29T14:30:00Z',
      results: {
        bank_b: {
          signal_id: 'sig_b_111',
          consortium_id: 'consortium_alpha',
          requesting_bank_id: 'bank_a',
          target_bank_id: null, // Strictly masked under ANONYMIZED_SUMMARY
          match_confirmed: true,
          match_category: 'EXACT_MATCH',
          match_confidence: 0.99,
          contributing_institutions_count: 1,
          contributing_institutions: [],
          disclosure_mode: 'ANONYMIZED_SUMMARY',
          sanitized_risk_level: 'HIGH',
          sanitized_typology_indicators: ['MULE_BURST'],
          execution_mode: 'VERIFIED_OPERATIONAL',
          attestation_status: 'NOT_REQUIRED',
          disclaimer: 'Sanitized.',
        },
      },
      failures: {
        bank_c: {
          target_bank_id: 'bank_c',
          error_category: 'CIRCUIT_BREAKER_ACTIVE',
          error_message: 'Counterpart worker in circuit breaker cooldown.',
          failed_at: '2026-10-29T14:30:00Z',
          retryable: false,
        },
      },
      execution_outcomes: {},
    };

    const mockMutateAsync = vi.fn().mockResolvedValue(mockQueryResponse);
    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: mockMutateAsync,
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAttachMultiCounterpartyEvidence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    const dispatchBtn = screen.getByTestId('dispatch-multi-query-btn');
    expect(dispatchBtn).not.toBeDisabled();
    fireEvent.click(dispatchBtn);

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          requesting_bank_id: 'bank_a',
          query_entity_id: 'ent_alice_999',
          consortium_id: 'consortium_alpha',
          target_bank_ids: ['bank_b', 'bank_c'],
          case_id: 'case_123',
          execution_mode: 'VERIFIED_OPERATIONAL',
          use_established_psi: true,
          require_established_psi: true,
          require_hardware_attestation: false,
          idempotency_key: expect.stringMatching(/^query_/),
        })
      );
    });

    // Check rendered results
    await waitFor(() => {
      expect(screen.getByText('batch_query_abc_123')).toBeInTheDocument();
      expect(screen.getByText('PARTIAL_FAILURE')).toBeInTheDocument();
      // Anonymous masking check: Bank B identity is masked in the DOM
      expect(screen.getAllByText('Anonymous Counterparty').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('ANONYMOUS MASKED').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByTestId('outcome-card-anonymized-0')).toBeInTheDocument();
      expect(screen.queryByTestId('outcome-card-bank_b')).not.toBeInTheDocument();
      expect(screen.getByText(/MATCH CONFIRMED \(EXACT_MATCH\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Confidence:/i)).toBeInTheDocument();
      expect(screen.getByText('MULE_BURST')).toBeInTheDocument();

      // Truthful failure check: Bank C circuit breaker
      expect(screen.getByText('CIRCUIT_BREAKER_ACTIVE')).toBeInTheDocument();
      expect(screen.getByText(/Counterpart worker in circuit breaker cooldown/i)).toBeInTheDocument();
    });
  });

  it('renders verified NO MATCH signal without fabricated 0% intersection label and enables attachment', async () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: mockConsortia },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    const mockNoMatchResponse = {
      query_id: 'batch_no_match_001',
      consortium_id: 'consortium_alpha',
      requesting_bank_id: 'bank_a',
      query_entity_id: 'ent_alice_999',
      counterpart_count: 1,
      overall_outcome: 'COMPLETED',
      outcome: 'COMPLETED',
      executed_at: '2026-10-29T14:30:00Z',
      results: {
        bank_b: {
          signal_id: 'sig_b_no_match',
          consortium_id: 'consortium_alpha',
          requesting_bank_id: 'bank_a',
          target_bank_id: null,
          match_confirmed: false,
          match_category: 'NO_MATCH',
          match_confidence: 1.0,
          disclosure_mode: 'ANONYMIZED_SUMMARY',
          sanitized_risk_level: 'LOW',
          sanitized_typology_indicators: [],
        },
      },
      failures: {},
      execution_outcomes: {},
    };

    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(mockNoMatchResponse),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAttachMultiCounterpartyEvidence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    fireEvent.click(screen.getByTestId('dispatch-multi-query-btn'));

    await waitFor(() => {
      // Truthful label: VERIFIED NO MATCH (NOT "0% INTERSECTION")
      expect(screen.getByText('VERIFIED NO MATCH')).toBeInTheDocument();
      expect(screen.queryByText(/0% INTERSECTION/i)).not.toBeInTheDocument();
      // Negative matches are admissible evidence items; attach button must be enabled
      expect(screen.getByTestId('attach-batch-evidence-btn')).not.toBeDisabled();
    });
  });

  it('attaches eligible batch signals to case evidence and updates ledger', async () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    const refetchEvidence = vi.fn();
    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: refetchEvidence,
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: mockConsortia },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    const mockQueryResponse = {
      query_id: 'batch_query_to_attach',
      consortium_id: 'consortium_alpha',
      requesting_bank_id: 'bank_a',
      query_entity_id: 'ent_alice_999',
      counterpart_count: 1,
      overall_outcome: 'COMPLETED',
      outcome: 'COMPLETED',
      executed_at: '2026-10-29T14:30:00Z',
      results: {
        bank_b: {
          signal_id: 'sig_b_111',
          consortium_id: 'consortium_alpha',
          requesting_bank_id: 'bank_a',
          match_confirmed: true,
          match_category: 'EXACT_MATCH',
          match_confidence: 0.99,
          contributing_institutions_count: 1,
          contributing_institutions: [],
          disclosure_mode: 'ANONYMIZED_SUMMARY',
          sanitized_risk_level: 'HIGH',
          sanitized_typology_indicators: ['MULE_BURST'],
          execution_mode: 'VERIFIED_OPERATIONAL',
          attestation_status: 'NOT_REQUIRED',
          disclaimer: 'Sanitized.',
        },
      },
      failures: {},
      execution_outcomes: {},
    };

    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(mockQueryResponse),
      isPending: false,
    } as any);

    const mockAttachResponse = {
      query_id: 'batch_query_to_attach',
      case_id: 'case_123',
      assembly_status: 'COMPLETED',
      attached_count: 1,
      replayed_count: 0,
      failed_count: 0,
      attached_evidence: [],
      omitted_or_failed: [],
      case_version: 2,
      timeline_event_recorded: true,
      is_duplicate_submission: false,
    };

    const mockAttachMutateAsync = vi.fn().mockResolvedValue(mockAttachResponse);
    vi.spyOn(queries, 'useAttachMultiCounterpartyEvidence').mockReturnValue({
      mutateAsync: mockAttachMutateAsync,
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    // 1. Dispatch query
    fireEvent.click(screen.getByTestId('dispatch-multi-query-btn'));

    // 2. Wait for attach button to be enabled
    await waitFor(() => {
      expect(screen.getByTestId('attach-batch-evidence-btn')).not.toBeDisabled();
    });

    // 3. Click attach button
    fireEvent.click(screen.getByTestId('attach-batch-evidence-btn'));

    await waitFor(() => {
      expect(mockAttachMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          query_id: 'batch_query_to_attach',
          case_id: 'case_123',
          idempotency_key: expect.stringMatching(/^attach_/),
        })
      );
    });

    // 4. Verify success banner and ledger refetch
    await waitFor(() => {
      expect(screen.getByTestId('attach-success-banner')).toBeInTheDocument();
      expect(screen.getByText(/Newly Attached: 1/i)).toBeInTheDocument();
      expect(refetchEvidence).toHaveBeenCalled();
    });
  });

  it('renders persisted intelligence evidence trail with active vs retention expired statuses and truthful badges', () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: mockPersistedEvidence,
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: mockConsortia },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAttachMultiCounterpartyEvidence').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    // Item 1: Active batch under anonymous masking
    expect(screen.getByText('Consortium Intelligence Evidence (CONSORTIUM_ALPHA)')).toBeInTheDocument();
    expect(screen.getByText('Batch Active')).toBeInTheDocument();
    expect(screen.getByText('VERIFIED')).toBeInTheDocument();
    expect(screen.getByText('a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890')).toBeInTheDocument();

    // Item 2: Expired batch under attributed disclosure
    expect(screen.getByText('Consortium Intelligence Evidence (CONSORTIUM_BETA)')).toBeInTheDocument();
    expect(screen.getByText(/Batch Record Purged \(Retention Bounded\)/i)).toBeInTheDocument();
    expect(screen.getByText('INTEGRITY_VERIFIED_ONLY')).toBeInTheDocument();

    const evItem2 = screen.getByTestId('evidence-item-ev_002');
    expect(within(evItem2).getByText('BANK_C')).toBeInTheDocument();
    expect(screen.getByText('f0e1d2c3b4a56789abcdef0123456789abcdef0123456789abcdef0123456789')).toBeInTheDocument();
  });

  it('disables dispatch and shows unauthenticated warning when bankId is missing or empty', () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: mockConsortia },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="" />);

    expect(
      screen.getByText(/Tenant Context Required: An authenticated institution bank ID is required/i)
    ).toBeInTheDocument();
    expect(screen.getByTestId('dispatch-multi-query-btn')).toBeDisabled();
  });

  it('disables dispatch and displays warning callout when capabilities returns empty consortia or errors', () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: [] },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    expect(
      screen.getByText(/No verified consortium participation or authorized bilateral counterparts configured/i)
    ).toBeInTheDocument();
    expect(screen.getByTestId('dispatch-multi-query-btn')).toBeDisabled();
  });

  it('invalidates previous query response when entity, target, or purpose selection changes', async () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: mockConsortia },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    const mockQueryResponse = {
      query_id: 'batch_invalidation_test',
      consortium_id: 'consortium_alpha',
      requesting_bank_id: 'bank_a',
      query_entity_id: 'ent_alice_999',
      counterpart_count: 1,
      overall_outcome: 'COMPLETED',
      outcome: 'COMPLETED',
      executed_at: '2026-10-29T14:30:00Z',
      results: {
        bank_b: {
          signal_id: 'sig_b_inv',
          consortium_id: 'consortium_alpha',
          requesting_bank_id: 'bank_a',
          match_confirmed: true,
          match_category: 'EXACT_MATCH',
          match_confidence: 0.99,
          disclosure_mode: 'ANONYMIZED_SUMMARY',
          sanitized_risk_level: 'HIGH',
          sanitized_typology_indicators: ['MULE_BURST'],
          execution_mode: 'VERIFIED_OPERATIONAL',
          attestation_status: 'NOT_REQUIRED',
        },
      },
      failures: {},
      execution_outcomes: {},
    };

    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(mockQueryResponse),
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    // Dispatch
    fireEvent.click(screen.getByTestId('dispatch-multi-query-btn'));

    await waitFor(() => {
      expect(screen.getByText('batch_invalidation_test')).toBeInTheDocument();
    });

    // Change entity selection
    fireEvent.change(screen.getByTestId('case-entity-select'), {
      target: { value: 'ent_bob_888' },
    });

    // Previous results should be cleared immediately
    await waitFor(() => {
      expect(screen.queryByText('batch_invalidation_test')).not.toBeInTheDocument();
    });
  });

  it('strictly ensures no counterpart bank identities or testids leak in DOM during ANONYMIZED_SUMMARY', async () => {
    vi.spyOn(queries, 'useCaseEligibleEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCaseIntelligenceEvidence').mockReturnValue({
      data: [],
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCrossBankQueryCapabilities').mockReturnValue({
      data: { requesting_bank_id: 'bank_a', consortia: mockConsortia },
      isLoading: false,
      refetch: vi.fn(),
    } as any);

    const mockAnonymizedResponse = {
      query_id: 'batch_leak_test_001',
      consortium_id: 'consortium_alpha',
      requesting_bank_id: 'bank_a',
      query_entity_id: 'ent_alice_999',
      counterpart_count: 2,
      overall_outcome: 'PARTIAL_FAILURE',
      outcome: 'PARTIAL_FAILURE',
      executed_at: '2026-10-29T14:30:00Z',
      results: {
        bank_b: {
          signal_id: 'sig_leak_check_01',
          consortium_id: 'consortium_alpha',
          requesting_bank_id: 'bank_a',
          target_bank_id: null,
          match_confirmed: true,
          match_category: 'EXACT_MATCH',
          match_confidence: 0.99,
          contributing_institutions_count: 1,
          contributing_institutions: [],
          disclosure_mode: 'ANONYMIZED_SUMMARY',
          sanitized_risk_level: 'HIGH',
          sanitized_typology_indicators: ['MULE_BURST'],
          execution_mode: 'VERIFIED_OPERATIONAL',
          attestation_status: 'NOT_REQUIRED',
        },
      },
      failures: {
        bank_c: {
          target_bank_id: 'bank_c',
          error_category: 'CIRCUIT_BREAKER_ACTIVE',
          error_message: 'Failure contacting bank_c during round execution.',
          failed_at: '2026-10-29T14:30:00Z',
          retryable: false,
        },
      },
      execution_outcomes: {},
    };

    vi.spyOn(queries, 'useQueryMultiCounterpartyIntelligence').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(mockAnonymizedResponse),
      isPending: false,
    } as any);

    render(<CrossBankIntelligenceWorkspace caseId="case_123" bankId="bank_a" />);

    fireEvent.click(screen.getByTestId('dispatch-multi-query-btn'));

    await waitFor(() => {
      expect(screen.getByText('batch_leak_test_001')).toBeInTheDocument();
      // Testid checks
      expect(screen.getByTestId('outcome-card-anonymized-0')).toBeInTheDocument();
      expect(screen.getByTestId('outcome-card-anonymized-1')).toBeInTheDocument();
      expect(screen.queryByTestId('outcome-card-bank_b')).not.toBeInTheDocument();
      expect(screen.queryByTestId('outcome-card-bank_c')).not.toBeInTheDocument();

      // Scrubbed error check: "bank_c" in error message must be scrubbed to "[redacted-counterparty]"
      expect(screen.getByText(/Failure contacting \[redacted-counterparty\] during round execution\./i)).toBeInTheDocument();
      expect(screen.queryByText(/Failure contacting bank_c during round execution\./i)).not.toBeInTheDocument();
    });
  });
});
