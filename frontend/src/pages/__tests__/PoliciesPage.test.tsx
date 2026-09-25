import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import PoliciesPage from '../PoliciesPage';
import * as queries from '../../api/queries';

describe('PoliciesPage (Rule Engine & AML Policies) Test Suite', () => {
  let queryClient: QueryClient;

  const mockRules = [
    {
      id: 'rule_01',
      rule_name: 'High Risk Jurisdiction Velocity Cap',
      action: 'BLOCK_TRANSACTION',
      condition: { and: [{ field: 'composite_risk_score', operator: '>=', value: 830 }] },
      is_active: true,
      priority: 10,
      description: 'Flags extreme cross-border transactions',
    },
    {
      id: 'rule_02',
      rule_name: 'Rapid Multi-Card Trial Flag',
      action: 'REQUIRE_MFA',
      condition: { and: [{ field: 'velocity', operator: '>=', value: 5.0 }] },
      is_active: false,
      priority: 20,
      description: 'Secondary auth on repeated card tries',
    },
  ];

  const mockAlerts = [
    {
      id: 'alt_live_01',
      transaction_id: 'TXN-LIVE-9921',
      bank_id: 'garanti_bbva',
      risk_score: 890,
      severity: 'critical',
      status: 'new',
      reason_codes: ['BURST_VELOCITY', 'SANCTIONS_GEO'],
      confidence: 0.94,
      created_at: '2026-09-26T00:00:00Z',
      top_features: [{ feature: 'velocity_1h', contribution: 0.65 }],
      risk_factors: ['GEO_MISMATCH'],
      model_confidence: 0.94,
    },
  ];

  const mockCreateMutate = vi.fn().mockResolvedValue({ id: 'rule_new_99' });
  const mockUpdateMutate = vi.fn().mockResolvedValue({ id: 'rule_01' });
  const mockDeleteMutate = vi.fn().mockResolvedValue({ id: 'rule_01' });
  const mockTestMutate = vi.fn().mockResolvedValue({
    matches: true,
    message: 'Rule condition matched test payload',
    matched_fields: ['velocity_1h', 'amount'],
  });

  beforeEach(() => {
    vi.restoreAllMocks();
    mockCreateMutate.mockClear();
    mockUpdateMutate.mockClear();
    mockDeleteMutate.mockClear();
    mockTestMutate.mockClear();

    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    vi.spyOn(queries, 'useRules').mockReturnValue({
      data: mockRules,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useAlerts').mockReturnValue({
      data: mockAlerts,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCreateRule').mockReturnValue({
      mutateAsync: mockCreateMutate,
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useUpdateRule').mockReturnValue({
      mutateAsync: mockUpdateMutate,
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useDeleteRule').mockReturnValue({
      mutateAsync: mockDeleteMutate,
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useTestRule').mockReturnValue({
      mutateAsync: mockTestMutate,
      isPending: false,
    } as any);
  });

  it('renders AML policies header, active rules table, and rule tester', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <PoliciesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Policy Rules & Decisions/i)).toBeInTheDocument();
    expect(screen.getByText(/High Risk Jurisdiction Velocity Cap/i)).toBeInTheDocument();
    expect(screen.getByText(/Rapid Multi-Card Trial Flag/i)).toBeInTheDocument();
    expect(screen.getByText(/Dynamic Rule Tester/i)).toBeInTheDocument();
  });

  it('opens add rule modal when clicking Add Policy Rule button', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <PoliciesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const addBtn = screen.getByRole('button', { name: /Add Policy Rule/i });
    fireEvent.click(addBtn);

    expect(screen.getByText(/Create Dynamic Policy Rule/i)).toBeInTheDocument();
  });

  it('loads ready AML rule template into create modal and populates AST', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <PoliciesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Open Add Rule modal
    fireEvent.click(screen.getByRole('button', { name: /Add Policy Rule/i }));
    expect(screen.getByText(/Hazır AML Kural Şablonları/i)).toBeInTheDocument();

    // Click Smurfing / Structuring template button
    const smurfingBtn = screen.getByLabelText('Apply Template smurfing_structuring');
    fireEvent.click(smurfingBtn);

    // Verify template fields were populated
    const ruleNameInput = screen.getByLabelText('Modal Rule Name') as HTMLInputElement;
    expect(ruleNameInput.value).toBe('smurfing_structuring_threshold_sar');

    const actionSelect = screen.getByLabelText('Modal Triggered Action') as HTMLSelectElement;
    expect(actionSelect.value).toBe('ESCALATE_TO_SAR');

    const conditionTextarea = screen.getByLabelText('Modal Condition JSON AST') as HTMLTextAreaElement;
    expect(conditionTextarea.value).toContain('min_value');
    expect(conditionTextarea.value).toContain('9000');

    // Submit form
    fireEvent.click(screen.getByRole('button', { name: /Register Rule/i }));

    await waitFor(() => {
      expect(mockCreateMutate).toHaveBeenCalledTimes(1);
      expect(mockCreateMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          rule_name: 'smurfing_structuring_threshold_sar',
          action: 'ESCALATE_TO_SAR',
          is_active: true,
        })
      );
    });
  });

  it('loads live suspicious alert into Dynamic Rule Tester when clicking Son Şüpheli Alarmı Yükle', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <PoliciesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Click Son Şüpheli Alarmı Yükle button
    const loadAlertBtn = screen.getByRole('button', { name: /Son Şüpheli Alarmı Yükle/i });
    fireEvent.click(loadAlertBtn);

    // Check that live alert info banner is displayed
    expect(screen.getByText(/Canlı Alarm Yüklendi:/i)).toBeInTheDocument();
    expect(screen.getAllByText(/TXN-LIVE-9921/i).length).toBeGreaterThanOrEqual(1);

    // Verify transaction textarea contains live alert parameters
    const txnTextarea = screen.getByLabelText(/Mock Transaction Payload/i) as HTMLTextAreaElement;
    expect(txnTextarea.value).toContain('TXN-LIVE-9921');
    expect(txnTextarea.value).toContain('890');
  });

  it('opens edit rule modal when clicking Edit on an existing rule and saves updates', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <PoliciesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Click Edit on rule_01
    const editBtn = screen.getByLabelText(/Edit Rule High Risk Jurisdiction Velocity Cap/i);
    fireEvent.click(editBtn);

    // Verify modal is in edit mode
    expect(screen.getByText(/Edit Policy Rule: High Risk Jurisdiction Velocity Cap/i)).toBeInTheDocument();

    // Verify fields are pre-populated
    const ruleNameInput = screen.getByLabelText('Modal Rule Name') as HTMLInputElement;
    expect(ruleNameInput.value).toBe('High Risk Jurisdiction Velocity Cap');

    // Modify rule name
    fireEvent.change(ruleNameInput, { target: { value: 'Updated Jurisdiction Velocity Rule' } });

    // Submit changes
    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(mockUpdateMutate).toHaveBeenCalledTimes(1);
      expect(mockUpdateMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'rule_01',
          rule_name: 'Updated Jurisdiction Velocity Rule',
          action: 'BLOCK_TRANSACTION',
        })
      );
    });
  });

  it('loads rule condition AST directly into Dynamic Rule Tester when clicking Test In Tester', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <PoliciesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    // Click Test In Tester on rule_01
    const testInTesterBtn = screen.getByLabelText(/Test Rule High Risk Jurisdiction Velocity Cap in engine/i);
    fireEvent.click(testInTesterBtn);

    // Verify condition textarea in tester has rule_01 condition
    const conditionTextarea = screen.getByLabelText('Condition JSON AST') as HTMLTextAreaElement;
    expect(conditionTextarea.value).toContain('composite_risk_score');
    expect(conditionTextarea.value).toContain('830');

    // Run evaluation test
    const runBtn = screen.getByRole('button', { name: /Run Evaluation Test/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(mockTestMutate).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/Trigger Condition Met/i)).toBeInTheDocument();
      expect(screen.getByText(/Rule condition matched test payload/i)).toBeInTheDocument();
      expect(screen.getByText(/Eşleşen Alanlar:/i)).toBeInTheDocument();
    });
  });
});
