import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CaseDetailPage from '../CaseDetailPage';
import SecurityPage from '../SecurityPage';
import { switchActiveTenant, getActiveTenantId, setClientTenant } from '../../api/client';
import * as queries from '../../api/queries';
import type { Case } from '../../api/types';

const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

describe('Frontend Behavioral Correctness & Contract Verification', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  const baseMockCase: Case = {
    id: 'case_xyz_789',
    title: 'Suspicious Structuring Network Case',
    status: 'investigating',
    priority: 'p1_critical',
    assigned_to: 'analyst_01',
    alert_ids: ['ALT-001'],
    evidence_ids: [],
    notes: [],
    timeline: [
      {
        event_type: 'created',
        actor: 'system',
        timestamp: '2026-09-16T01:00:00Z',
        description: 'Case opened from high-risk AML alert',
        metadata: {},
      },
    ],
    created_at: '2026-09-16T01:00:00Z',
    updated_at: '2026-09-16T02:00:00Z',
    closed_at: null,
    total_risk_score: 0.94,
    duration_hours: 1.5,
    is_open: true,
    version: 4,
    timeline_hash: 'hash_abc_123',
    supervisor_signatures: [],
    supervisor_signature: null,
  };

  describe('Handoff A: Case Concurrency, Version Propagation & 409 Conflict Recovery', () => {
    it('propagates expected_status, expected_version, and expected_timeline_hash during status mutations', async () => {
      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: baseMockCase,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: [],
      } as any);

      const mockUpdateStatus = vi.fn().mockResolvedValue({
        ...baseMockCase,
        status: 'closed_confirmed',
        version: 5,
      });

      vi.spyOn(queries, 'useUpdateCaseStatus').mockReturnValue({
        mutateAsync: mockUpdateStatus,
        isPending: false,
      } as any);

      const queryClient = createTestQueryClient();
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/cases/case_xyz_789']}>
            <Routes>
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Enter supervisor signature for closure
      const sigInput = screen.getByPlaceholderText(/Secondary authorization key/i);
      fireEvent.change(sigInput, { target: { value: 'supervisor:chief_officer_1' } });

      // Click the closure button
      const closeButtons = screen.getAllByRole('button');
      const closeConfirmedBtn = closeButtons.find(
        (b) => b.textContent?.toLowerCase().includes('confirm fraud') || b.textContent?.toLowerCase().includes('closed (confirmed)')
      );
      expect(closeConfirmedBtn).toBeDefined();

      fireEvent.click(closeConfirmedBtn!);

      await waitFor(() => {
        expect(mockUpdateStatus).toHaveBeenCalledTimes(1);
        expect(mockUpdateStatus).toHaveBeenCalledWith(
          expect.objectContaining({
            caseId: 'case_xyz_789',
            status: 'closed_confirmed',
            actor: 'analyst',
            supervisor_signature: 'supervisor:chief_officer_1',
            expected_status: 'investigating',
            expected_version: 4,
            expected_timeline_hash: 'hash_abc_123',
          })
        );
      });
    });

    it('recovers from 409 Conflict: rolls back optimistic state, invalidates case cache, and displays truthful conflict feedback', async () => {
      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: baseMockCase,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: [],
      } as any);

      const conflictError = {
        response: {
          status: 409,
          data: {
            detail: 'Precondition failed: Expected case version 4, but current version is 5 (Stale Approval Invariant).',
          },
        },
      };

      const mockUpdateStatus = vi.fn().mockRejectedValue(conflictError);
      vi.spyOn(queries, 'useUpdateCaseStatus').mockReturnValue({
        mutateAsync: mockUpdateStatus,
        isPending: false,
      } as any);

      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/cases/case_xyz_789']}>
            <Routes>
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Provide supervisor key
      const sigInput = screen.getByPlaceholderText(/Secondary authorization key/i);
      fireEvent.change(sigInput, { target: { value: 'supervisor:chief_officer_1' } });

      const closeButtons = screen.getAllByRole('button');
      const closeConfirmedBtn = closeButtons.find(
        (b) => b.textContent?.toLowerCase().includes('confirm fraud') || b.textContent?.toLowerCase().includes('closed (confirmed)')
      );
      fireEvent.click(closeConfirmedBtn!);

      await waitFor(() => {
        // Must display the 409 conflict detail from backend
        expect(
          screen.getByText(/Precondition failed: Expected case version 4/i)
        ).toBeInTheDocument();
      });

      // Must have triggered cache invalidation to restore authoritative server state
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['case', 'case_xyz_789'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['cases'] });
    });
  });

  describe('Handoff B: SAR Report Eligibility Truthfulness', () => {
    it('disables FinCEN XML export for closed_false_positive cases with explicit explanatory tooltip', () => {
      const fpCase: Case = {
        ...baseMockCase,
        status: 'closed_false_positive',
        is_open: false,
        closed_at: '2026-09-16T03:00:00Z',
      };

      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: fpCase,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: [],
      } as any);

      const queryClient = createTestQueryClient();
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/cases/case_xyz_789']}>
            <Routes>
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      const exportBtn = screen.getByRole('button', { name: /Export FinCEN XML/i });
      expect(exportBtn).toBeDisabled();
      expect(exportBtn).toHaveAttribute(
        'title',
        'SAR filing prohibited: Case is resolved as False Positive'
      );
      expect(screen.getByText('Ineligible (FP)')).toBeInTheDocument();
    });

    it('disables FinCEN XML export for unreviewed open cases with 4-Eyes dual control notice', () => {
      const openCase: Case = {
        ...baseMockCase,
        status: 'open',
        is_open: true,
      };

      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: openCase,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: [],
      } as any);

      const queryClient = createTestQueryClient();
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/cases/case_xyz_789']}>
            <Routes>
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      const exportBtn = screen.getByRole('button', { name: /Export FinCEN XML/i });
      expect(exportBtn).toBeDisabled();
      expect(exportBtn).toHaveAttribute(
        'title',
        "Regulatory SAR XML requires 'Closed (Confirmed)' status under Four-Eyes dual control"
      );
      expect(screen.getByText('4-Eyes Reqd')).toBeInTheDocument();
    });

    it('enables FinCEN XML export for closed_confirmed cases', () => {
      const confirmedCase: Case = {
        ...baseMockCase,
        status: 'closed_confirmed',
        is_open: false,
        closed_at: '2026-09-16T03:00:00Z',
      };

      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: confirmedCase,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: [],
      } as any);

      const queryClient = createTestQueryClient();
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/cases/case_xyz_789']}>
            <Routes>
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      const exportBtn = screen.getByRole('button', { name: /Export FinCEN XML/i });
      expect(exportBtn).not.toBeDisabled();
      expect(exportBtn).toHaveAttribute(
        'title',
        'Compile and download validated FinCEN BSA SAR 2.0 XML'
      );
    });
  });

  describe('Tenant Isolation & Storage Lifecycle', () => {
    it('switchActiveTenant updates storage and clears queryClient cache immediately', () => {
      const queryClient = createTestQueryClient();
      localStorage.setItem('cfi_tenant_id', 'bank_alpha');

      // Pre-populate query client with Tenant A data
      queryClient.setQueryData(['case', 'case_alpha_01'], {
        id: 'case_alpha_01',
        title: 'Tenant Alpha Sensitive Case',
      });
      expect(queryClient.getQueryData(['case', 'case_alpha_01'])).toBeDefined();

      // Switch active tenant to bank_beta
      switchActiveTenant(queryClient, 'bank_beta');

      // Tenant storage must be updated
      expect(getActiveTenantId()).toBe('bank_beta');
      expect(localStorage.getItem('cfi_tenant_id')).toBe('bank_beta');

      // Query cache must be completely cleared
      expect(queryClient.getQueryData(['case', 'case_alpha_01'])).toBeUndefined();
    });

    it('setClientTenant handles clearing and setting tenant tokens', () => {
      setClientTenant('bank_gamma');
      expect(getActiveTenantId()).toBe('bank_gamma');

      setClientTenant(null);
      expect(getActiveTenantId()).toBe('default');
    });
  });

  describe('Handoff C: Webhook Gateway & SSRF Security', () => {
    it('displays SSRF rejection truthfully when registering private IP and never shows false success', async () => {
      vi.spyOn(queries, 'useSecurityStatus').mockReturnValue({
        data: {
          mtls: { enabled: true, ca_cn: 'Meridian Root CA', tls_version: 'TLSv1.3', peer_verification: 'VERIFIED', sample_cert: { cn: 'bank-node.meridian', sans: ['DNS:bank-node.meridian'], valid_until: '2027-01-01' } },
          oidc: { provider: 'Keycloak', issuer: 'https://auth.bank', client_id: 'cf-intelligence' },
          abac: { active_policies: 12, default_action: 'DENY', enforced_policies: ['POL-01: Cross-Bank Isolation'] },
          vault: { vault_url: 'http://localhost:8200', mount_point: 'secret/', sample_secret_source: 'VAULT_KV' },
          audit_chain: { length: 154, last_hash: 'abc' },
        },
        isLoading: false,
      } as any);
      vi.spyOn(queries, 'useAuditChain').mockReturnValue({ data: [], isLoading: false } as any);
      vi.spyOn(queries, 'useVaultSealStatus').mockReturnValue({ data: { sealed: false } } as any);
      vi.spyOn(queries, 'useZKVerifierStatus').mockReturnValue({ data: { initialized: true } } as any);

      vi.spyOn(queries, 'useWebhookSubscriptionsQuery').mockReturnValue({
        data: { subscriptions: [], total_count: 0 },
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useWebhookDeliveryLogsQuery').mockReturnValue({
        data: {
          deliveries: [
            {
              delivery_id: 'del_001_fail',
              target_url: 'https://bad-dns.internal/hook',
              event_type: 'ALERT_CREATED',
              status_code: null,
              success: false,
              attempt_count: 3,
              error_message: 'DNS resolution failed: name does not exist',
              timestamp: '2026-09-16T04:00:00Z',
            },
          ],
          total_count: 1,
        },
        isLoading: false,
      } as any);

      const mockRegisterWebhook = vi.fn().mockRejectedValue({
        response: {
          status: 400,
          data: {
            detail: 'SSRF validation failed: Target URL points to forbidden private network or loopback address.',
          },
        },
      });

      vi.spyOn(queries, 'useRegisterWebhookMutation').mockReturnValue({
        mutateAsync: mockRegisterWebhook,
        isPending: false,
      } as any);

      const queryClient = createTestQueryClient();
      render(
        <QueryClientProvider client={queryClient}>
          <SecurityPage />
        </QueryClientProvider>
      );

      // Click on Webhook tab
      const webhookTabBtn = screen.getByText(/Webhook Gateway & SSRF Security/i);
      fireEvent.click(webhookTabBtn);

      await waitFor(() => {
        expect(screen.getByText('Webhook Gateway & Real-Time Alert Dispatcher')).toBeInTheDocument();
      });

      // Verify the failed delivery log is shown as FAILED with diagnostic message
      expect(screen.getByText(/ERR FAILED/i)).toBeInTheDocument();
      expect(screen.getByText(/DNS resolution failed: name does not exist/i)).toBeInTheDocument();

      // Submit SSRF-invalid URL
      const urlInput = screen.getByPlaceholderText(/cfi-webhook/i);
      fireEvent.change(urlInput, { target: { value: 'https://192.168.1.50/hook' } });

      const registerBtn = screen.getByRole('button', { name: /Register Subscription/i });
      fireEvent.click(registerBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/SSRF validation failed: Target URL points to forbidden private network/i)
        ).toBeInTheDocument();
      });

      // False success must not be displayed
      expect(screen.queryByText(/Webhook registered successfully/i)).not.toBeInTheDocument();
    });
  });
});
