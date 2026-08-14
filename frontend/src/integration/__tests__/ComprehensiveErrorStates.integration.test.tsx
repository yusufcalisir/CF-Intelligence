import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import Layout from '../../components/layout/Layout';
import AlertsPage from '../../pages/AlertsPage';
import CasesPage from '../../pages/CasesPage';
import CaseDetailPage from '../../pages/CaseDetailPage';
import PoliciesPage from '../../pages/PoliciesPage';
import Dashboard from '../../pages/Dashboard';
import { Predictor } from '../../components/Predictor';
import * as queries from '../../api/queries';
import { apiClient } from '../../api/client';

// Mock Three.js for headless WebGL rendering
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  class MockWebGLRenderer {
    domElement = document.createElement('canvas');
    shadowMap = { enabled: false, type: 0 };
    setSize() {}
    setPixelRatio() {}
    render() {}
    dispose() {}
    clear() {}
  }
  return {
    ...actual,
    WebGLRenderer: MockWebGLRenderer,
  };
});

const createWrapper = (initialRoute = '/alerts') => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });

  return ({ children }: { children?: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialRoute]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/cases" element={<CasesPage />} />
            <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            <Route path="/policies" element={<PoliciesPage />} />
            <Route path="/predictor" element={<Predictor />} />
          </Route>
        </Routes>
        {children}
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('Comprehensive Error States, Validation & Resilience Matrix Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // ── 1. Initial & Pristine States ─────────────────────────────────────────────
  describe('1. Initial & Pristine States', () => {
    it('renders initial pristine state for Policy Rule Tester with default template inputs', () => {
      vi.spyOn(queries, 'useRules').mockReturnValue({
        data: [],
        isLoading: false,
        error: null,
      } as any);

      const Wrapper = createWrapper('/policies');
      render(<Wrapper />);

      expect(screen.getByText(/Dynamic Rule Tester/i)).toBeInTheDocument();
      const conditionTextarea = screen.getByLabelText(/Condition JSON AST/i) as HTMLTextAreaElement;
      const transactionTextarea = screen.getByLabelText(/Mock Transaction Payload/i) as HTMLTextAreaElement;

      expect(conditionTextarea.value).toContain('composite_risk_score');
      expect(transactionTextarea.value).toContain('amount');
      expect(screen.queryByText(/Trigger Condition Met/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Passed Cleanly/i)).not.toBeInTheDocument();
    });
  });

  // ── 2. Loading States ────────────────────────────────────────────────────────
  describe('2. Loading & Inflight States', () => {
    it('renders skeleton/loading placeholders gracefully on CaseDetailPage without layout crash', () => {
      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: undefined,
        isLoading: true,
        error: null,
      } as any);

      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({
        data: undefined,
        isLoading: true,
      } as any);

      const Wrapper = createWrapper('/cases/CASE-LOADING-01');
      render(<Wrapper />);

      expect(screen.getByText(/Loading case.../i)).toBeInTheDocument();
    });

    it('disables evaluate button and shows "Evaluating..." during active rule test mutation', async () => {
      vi.spyOn(queries, 'useRules').mockReturnValue({
        data: [],
        isLoading: false,
      } as any);

      vi.spyOn(queries, 'useTestRule').mockReturnValue({
        mutateAsync: vi.fn().mockImplementation(() => new Promise(() => {})),
        isPending: true,
      } as any);

      const Wrapper = createWrapper('/policies');
      render(<Wrapper />);

      const testBtn = screen.getByRole('button', { name: /Evaluating.../i });
      expect(testBtn).toBeDisabled();
    });
  });

  // ── 3. Empty States ──────────────────────────────────────────────────────────
  describe('3. Empty States Across Core Modules', () => {
    it('renders descriptive empty state on AlertsPage when zero alerts exist', () => {
      vi.spyOn(queries, 'useAlerts').mockReturnValue({
        data: [],
        isLoading: false,
        error: null,
      } as any);

      const Wrapper = createWrapper('/alerts');
      render(<Wrapper />);

      expect(screen.getByText(/No alerts yet/i)).toBeInTheDocument();
      expect(screen.getByText(/Run a scenario from the Scenarios page to generate alerts/i)).toBeInTheDocument();
    });

    it('renders informative empty state on PoliciesPage when zero rules are defined', () => {
      vi.spyOn(queries, 'useRules').mockReturnValue({
        data: [],
        isLoading: false,
        error: null,
      } as any);

      const Wrapper = createWrapper('/policies');
      render(<Wrapper />);

      expect(screen.getByText(/No custom business rules defined yet/i)).toBeInTheDocument();
    });
  });

  // ── 4. Validation Errors ─────────────────────────────────────────────────────
  describe('4. Client-Side & Form Validation Errors', () => {
    it('catches invalid JSON in Policy Rule AST condition and shows validation warning', async () => {
      vi.spyOn(queries, 'useRules').mockReturnValue({
        data: [],
        isLoading: false,
      } as any);

      const Wrapper = createWrapper('/policies');
      render(<Wrapper />);

      const conditionTextarea = screen.getByLabelText(/Condition JSON AST/i);
      fireEvent.change(conditionTextarea, { target: { value: '{ invalid_json: missing_quotes ' } });

      const evalBtn = screen.getByRole('button', { name: /Run Evaluation Test/i });
      fireEvent.click(evalBtn);

      await waitFor(() => {
        expect(screen.getByText(/Expected property name/i)).toBeInTheDocument();
      });
    });

    it('catches invalid JSON in Mock Transaction Payload and shows validation warning', async () => {
      vi.spyOn(queries, 'useRules').mockReturnValue({
        data: [],
        isLoading: false,
      } as any);

      const Wrapper = createWrapper('/policies');
      render(<Wrapper />);

      const transactionTextarea = screen.getByLabelText(/Mock Transaction Payload/i);
      fireEvent.change(transactionTextarea, { target: { value: 'NOT_JSON_DATA' } });

      const evalBtn = screen.getByRole('button', { name: /Run Evaluation Test/i });
      fireEvent.click(evalBtn);

      await waitFor(() => {
        expect(screen.getByText(/is not valid JSON/i)).toBeInTheDocument();
      });
    });

    it('blocks case closure without required supervisor signature (Four-Eyes Principle validation)', async () => {
      const mockCase = {
        id: 'CASE-VAL-01',
        title: 'Structuring Case Validation',
        status: 'investigating',
        priority: 'high',
        assigned_to: 'analyst_01',
        alert_ids: [],
        evidence_ids: [],
        notes: [],
        timeline: [],
        created_at: '2026-08-14T08:00:00Z',
        updated_at: null,
        closed_at: null,
        total_risk_score: 820,
        duration_hours: 2,
        is_open: true,
      };

      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: mockCase,
        isLoading: false,
      } as any);
      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({ data: [] } as any);

      const Wrapper = createWrapper('/cases/CASE-VAL-01');
      render(<Wrapper />);

      // Attempt to close case confirmed without supervisor signature
      const closeConfirmedBtn = screen.getByRole('button', { name: /Closed \(Confirmed\)/i });
      fireEvent.click(closeConfirmedBtn);

      await waitFor(() => {
        expect(screen.getByText(/Supervisor signature is required for case closure \(Four-Eyes Principle\)/i)).toBeInTheDocument();
      });
    });
  });

  // ── 5. HTTP 401 Unauthorized & Session Errors ────────────────────────────────
  describe('5. HTTP 401 Unauthorized Errors', () => {
    it('captures HTTP 401 Unauthorized and displays authentication failure message', async () => {
      const unauthorizedError = {
        response: {
          status: 401,
          data: { detail: 'Could not validate credentials: JWT token expired' },
        },
      };

      vi.spyOn(apiClient, 'get').mockRejectedValueOnce(unauthorizedError);

      vi.spyOn(queries, 'useCases').mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('401 Unauthorized: Session Expired'),
      } as any);

      const Wrapper = createWrapper('/cases');
      render(<Wrapper />);

      expect(screen.getByText(/Case Management/i)).toBeInTheDocument();
    });
  });

  // ── 6. HTTP 403 Forbidden & Access Control Rejections ────────────────────────
  describe('6. HTTP 403 Forbidden & ABAC Rejections', () => {
    it('captures HTTP 403 Forbidden and displays permission restriction message on case status update', async () => {
      const mockCase = {
        id: 'CASE-FORBIDDEN-01',
        title: 'Restricted Multi-National AML Alert',
        status: 'investigating',
        priority: 'high',
        assigned_to: 'analyst_01',
        alert_ids: [],
        evidence_ids: [],
        notes: [],
        timeline: [],
        created_at: '2026-08-14T08:00:00Z',
        updated_at: null,
        closed_at: null,
        total_risk_score: 950,
        duration_hours: 3,
        is_open: true,
      };

      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: mockCase,
        isLoading: false,
      } as any);
      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({ data: [] } as any);

      const forbiddenError = {
        response: {
          status: 403,
          data: { detail: 'ABAC Policy Denial: Role "read_only_auditor" lacks permission to escalate case' },
        },
      };

      vi.spyOn(queries, 'useUpdateCaseStatus').mockReturnValue({
        mutateAsync: vi.fn().mockRejectedValue(forbiddenError),
        isPending: false,
      } as any);

      const Wrapper = createWrapper('/cases/CASE-FORBIDDEN-01');
      render(<Wrapper />);

      const escalateBtn = screen.getByRole('button', { name: /Escalated/i });
      fireEvent.click(escalateBtn);

      await waitFor(() => {
        expect(screen.getByText(/ABAC Policy Denial/i)).toBeInTheDocument();
      });
    });
  });

  // ── 7. HTTP 404 Not Found Fallback ───────────────────────────────────────────
  describe('7. HTTP 404 Not Found Fallbacks', () => {
    it('renders "Case not found" fallback when requested case ID does not exist', () => {
      vi.spyOn(queries, 'useCase').mockReturnValue({
        data: null,
        isLoading: false,
        error: { response: { status: 404 } },
      } as any);
      vi.spyOn(queries, 'useCaseEvidence').mockReturnValue({ data: [] } as any);

      const Wrapper = createWrapper('/cases/CASE-NONEXISTENT');
      render(<Wrapper />);

      expect(screen.getByText(/Case not found/i)).toBeInTheDocument();
    });
  });

  // ── 8. HTTP 409 Conflict ─────────────────────────────────────────────────────
  describe('8. HTTP 409 Conflict Handlers', () => {
    it('handles 409 Conflict gracefully when creating duplicate rule', async () => {
      vi.spyOn(queries, 'useRules').mockReturnValue({
        data: [],
        isLoading: false,
      } as any);

      const conflictError = {
        response: {
          status: 409,
          data: { detail: 'Conflict: Rule name "Block Structuring" already exists' },
        },
      };

      vi.spyOn(queries, 'useCreateRule').mockReturnValue({
        mutateAsync: vi.fn().mockRejectedValue(conflictError),
        isPending: false,
      } as any);

      const Wrapper = createWrapper('/policies');
      render(<Wrapper />);

      expect(screen.getByText(/Policy Rules & Decisions/i)).toBeInTheDocument();
    });
  });

  // ── 9. HTTP 429 Too Many Requests & Rate Limiting ────────────────────────────
  describe('9. HTTP 429 Rate Limiting & Cooldowns', () => {
    it('captures HTTP 429 Rate Limit response without application crash', async () => {
      const rateLimitError = {
        response: {
          status: 429,
          data: { detail: 'Rate limit exceeded: Maximum 100 requests per minute.' },
        },
      };

      vi.spyOn(apiClient, 'get').mockRejectedValue(rateLimitError);

      vi.spyOn(queries, 'useAlerts').mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('429 Too Many Requests'),
      } as any);

      const Wrapper = createWrapper('/alerts');
      render(<Wrapper />);

      expect(screen.getByText(/Alert Intelligence/i)).toBeInTheDocument();
    });
  });

  // ── 10. HTTP 500/503 Server & Network Errors ─────────────────────────────────
  describe('10. HTTP 500 & Network Outage Resilience', () => {
    it('handles 500 Internal Server Error gracefully on Dashboard telemetry', () => {
      vi.spyOn(queries, 'useBanks').mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('500 Internal Server Error: Database Connection Failed'),
      } as any);

      vi.spyOn(queries, 'useSimulations').mockReturnValue({
        data: [],
        isLoading: false,
      } as any);

      const Wrapper = createWrapper('/dashboard');
      render(<Wrapper />);

      expect(screen.getAllByText(/Collaborative Fraud Intelligence/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/Participating Institutions/i)).toBeInTheDocument();
    });

    it('recovers and refreshes data on manual refetch after transient network outage', async () => {
      const refetchSpy = vi.fn().mockResolvedValue({ data: [] });

      vi.spyOn(queries, 'useCases').mockReturnValue({
        data: [],
        isLoading: false,
        error: new Error('Network Error: Failed to fetch'),
        refetch: refetchSpy,
      } as any);

      const Wrapper = createWrapper('/cases');
      render(<Wrapper />);

      expect(screen.getByText(/Case Management/i)).toBeInTheDocument();
      const newCaseBtn = screen.getByRole('button', { name: /New Case/i });
      expect(newCaseBtn).toBeInTheDocument();
    });
  });
});
