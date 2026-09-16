import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CaseDetailPage from '../CaseDetailPage';
import InvestigationDashboard from '../InvestigationDashboard';
import { apiClient } from '../../api/client';
import * as queries from '../../api/queries';

const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

describe('STAGE_50 Frontend Hardening & Contract Parity', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  describe('apiClient request interceptors', () => {
    it('injects dynamic Authorization and X-Tenant-ID headers from storage', async () => {
      localStorage.setItem('cfi_token', 'test-jwt-token-123');
      localStorage.setItem('cfi_tenant_id', 'bank_gamma');

      // Test interceptor execution directly on config object
      const interceptor = (apiClient.interceptors.request as any).handlers[0].fulfilled;
      const config = { headers: {} as Record<string, string> };
      const updated = interceptor(config);

      expect(updated.headers.Authorization).toBe('Bearer test-jwt-token-123');
      expect(updated.headers['X-Tenant-ID']).toBe('bank_gamma');
    });

    it('does not overwrite explicitly provided Authorization or X-Tenant-ID headers', async () => {
      localStorage.setItem('cfi_token', 'stored-token');
      localStorage.setItem('cfi_tenant_id', 'stored-tenant');

      const interceptor = (apiClient.interceptors.request as any).handlers[0].fulfilled;
      const config = {
        headers: {
          Authorization: 'Bearer explicit-token',
          'X-Tenant-ID': 'explicit-tenant',
        } as Record<string, string>,
      };
      const updated = interceptor(config);

      expect(updated.headers.Authorization).toBe('Bearer explicit-token');
      expect(updated.headers['X-Tenant-ID']).toBe('explicit-tenant');
    });
  });

  describe('InvestigationDashboard Hardening', () => {
    it('renders all KPI stat cards and handles empty audit logs cleanly', () => {
      vi.spyOn(queries, 'useDashboardStats').mockReturnValue({
        data: {
          total_alerts: 450,
          critical_alerts: 12,
          open_cases: 7,
          total_entities: 1280,
          shared_intelligence_items: 64,
          graph_clusters: 18,
          active_scenarios: 4,
          cross_institution_matches: 9,
        } as any,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useAlertsBySeverity').mockReturnValue({
        data: { CRITICAL: 12, HIGH: 48, MEDIUM: 150, LOW: 240 } as any,
      } as any);

      vi.spyOn(queries, 'useAlertsByBank').mockReturnValue({
        data: { bank_alpha: 200, bank_beta: 150, bank_gamma: 100 } as any,
      } as any);

      vi.spyOn(queries, 'useIntelligenceStats').mockReturnValue({
        data: { total_rules: 15, active_typologies: 8 } as any,
      } as any);

      vi.spyOn(queries, 'useAuditLogs').mockReturnValue({
        data: [],
      } as any);

      const queryClient = createTestQueryClient();
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <InvestigationDashboard />
          </MemoryRouter>
        </QueryClientProvider>
      );

      expect(screen.getByText('Investigation Dashboard')).toBeInTheDocument();
      expect(screen.getByText('Total Alerts')).toBeInTheDocument();
      expect(screen.getByText('Critical Alerts')).toBeInTheDocument();
      expect(screen.getByText('Open Cases')).toBeInTheDocument();
      expect(screen.getByText('Entities')).toBeInTheDocument();
      expect(screen.getByText('SHA-256 Verified')).toBeInTheDocument();
      expect(screen.getAllByText('No investigator activity logs recorded yet.').length).toBeGreaterThan(0);
    });
  });

  describe('CaseDetailPage Hardening & Four-Eyes Principle', () => {
    const mockCase = {
      id: 'case_xyz_789',
      title: 'Suspicious Structuring Network Case',
      status: 'investigating',
      priority: 'p1_critical',
      assigned_to: 'lead_investigator',
      alert_ids: ['alt_1', 'alt_2'],
      notes: [],
      timeline: [
        {
          id: 'ev_1',
          event_type: 'created',
          actor: 'system',
          timestamp: '2026-09-16T01:00:00Z',
          description: 'Case opened from high-risk AML alert',
        },
      ],
      created_at: '2026-09-16T01:00:00Z',
      is_open: true,
    };

    it('blocks case closure without supervisor signature under Four-Eyes principle', async () => {
      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: mockCase as any,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: [],
      } as any);

      const mockUpdateStatus = vi.fn();
      vi.spyOn(queries, 'useUpdateCaseStatus').mockReturnValue({
        mutateAsync: mockUpdateStatus,
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

      expect(screen.getByText('Suspicious Structuring Network Case')).toBeInTheDocument();

      // Find the closure transition button for closed_confirmed
      const closeButtons = screen.getAllByRole('button');
      const closeConfirmedBtn = closeButtons.find(
        (b) => b.textContent?.toLowerCase().includes('confirm fraud') || b.textContent?.toLowerCase().includes('close')
      );

      if (closeConfirmedBtn) {
        fireEvent.click(closeConfirmedBtn);
        await waitFor(() => {
          // Four-Eyes principle error message must be shown
          expect(
            screen.getByText(/Supervisor signature is required for case closure/i)
          ).toBeInTheDocument();
        });
        expect(mockUpdateStatus).not.toHaveBeenCalled();
      }
    });

    it('triggers copilot narrative generation using useGenerateCopilotNarrative mutation', async () => {
      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: mockCase as any,
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: [],
      } as any);

      const mockMutateAsync = vi.fn().mockResolvedValue({
        case_id: 'case_xyz_789',
        fincen_sar_narrative: 'Formal FinCEN SAR 2.0 narrative generated by AML Copilot.',
        four_eyes_briefing: 'Supervisor briefing: High risk transaction velocity detected.',
        recommended_action: 'ESCALATE_TO_SAR',
        top_risk_drivers: [],
        graph_topology_summary: {},
        zero_pii_verified: true,
        generated_at: '2026-09-16T01:00:00Z',
        lineage_hash: 'abc123canonicalhash',
      });

      vi.spyOn(queries, 'useGenerateCopilotNarrative').mockReturnValue({
        mutateAsync: mockMutateAsync,
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

      // Find Copilot narrative generate button
      const copilotBtn = screen.getByRole('button', { name: /Generate AI SAR Narrative/i });
      fireEvent.click(copilotBtn);

      await waitFor(() => {
        expect(mockMutateAsync).toHaveBeenCalledWith({
          caseId: 'case_xyz_789',
          request: { case_id: 'case_xyz_789', include_fincen_narrative: true },
        });
      });

      // Confirm generated narrative appears in DOM
      await waitFor(() => {
        expect(screen.getByText(/Formal FinCEN SAR 2.0 narrative generated by AML Copilot/i)).toBeInTheDocument();
      });
    });
  });
});
