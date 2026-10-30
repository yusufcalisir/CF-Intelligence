import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PilotDetailPage from '../PilotDetailPage';
import type { PilotStatusResponse, PilotTelemetrySummary } from '../../api/types';

// Mock pilots hooks
const mockUsePilotStatus = vi.fn();
const mockUsePilotTelemetry = vi.fn();
const mockUsePilotDossier = vi.fn();

vi.mock('../../api/pilots', () => ({
  usePilotStatus: () => mockUsePilotStatus(),
  usePilotTelemetry: () => mockUsePilotTelemetry(),
  usePilotDossier: () => mockUsePilotDossier(),
  useRunPilotPreflight: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApprovePilot: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useActivatePilot: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePausePilot: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResumePilot: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortPilot: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCompletePilot: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const mockActivePilot: PilotStatusResponse = {
  pilot_id: 'pilot-alpha-001',
  revision: 1,
  consortium_id: 'consortium-main',
  status: 'ACTIVE',
  status_reason: 'Active and operational under verified scope.',
  scope_digest: '9f8e7d6c5b4a3120fedcba0987654321',
  is_fully_approved: true,
  passed: true,
  queries_executed: 12,
  queries_denied: 0,
  queries_total_quota: 100,
  participating_institutions: ['bank_a', 'bank_b'],
  allowed_purposes: ['AML_INVESTIGATION', 'COUNTER_FRAUD'],
  allowed_jurisdictions: ['EU', 'US'],
  operational_grant_id: 'grant-ffiec-001',
  starts_at: '2026-01-01T00:00:00Z',
  expires_at: '2026-12-31T23:59:59Z',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
  preflight_checks: [
    {
      check_name: 'INSTITUTION_MEMBERSHIP',
      passed: true,
      details: 'bank_a: ACTIVE; bank_b: ACTIVE',
      checked_at: '2026-01-01T00:00:00Z',
    },
    {
      check_name: 'OPERATIONAL_GRANT_VALID',
      passed: true,
      details: 'Grant active and verified',
      checked_at: '2026-01-01T00:00:00Z',
    },
  ],
  approvals: {
    bank_a: {
      approver_id: 'user_admin_a',
      approver_institution: 'bank_a',
      approver_role: 'admin',
      scope_digest: '9f8e7d6c5b4a3120fedcba0987654321',
      approved_at: '2026-01-01T01:00:00Z',
      signature: 'sig_ed25519_bank_a',
    },
    bank_b: {
      approver_id: 'user_admin_b',
      approver_institution: 'bank_b',
      approver_role: 'admin',
      scope_digest: '9f8e7d6c5b4a3120fedcba0987654321',
      approved_at: '2026-01-01T01:05:00Z',
      signature: 'sig_ed25519_bank_b',
    },
    operator: {
      approver_id: 'operator_chief',
      approver_institution: 'OPERATOR',
      approver_role: 'operator',
      scope_digest: '9f8e7d6c5b4a3120fedcba0987654321',
      approved_at: '2026-01-01T01:10:00Z',
    },
  },
  permitted_actions: ['pause', 'export_dossier'],
  missing_approvals: [],
  has_expired: false,
};

const mockTelemetryNoLatency: PilotTelemetrySummary = {
  pilot_id: 'pilot-alpha-001',
  status: 'ACTIVE',
  queries_executed: 0,
  queries_denied: 0,
  queries_total: 0,
  queries_successful: 0,
  queries_failed: 0,
  matches_found: 0,
  queries_by_institution: { bank_a: 0, bank_b: 0 },
  query_quota_total: 100,
  quota_remaining: 100,
  mean_latency_ms: null,
  p95_latency_ms: null,
  latency_sample_count: 0,
  has_latency_observations: false,
  institution_query_counts: { bank_a: 0, bank_b: 0 },
  last_operation_at: null,
  bilateral_authorization_valid: true,
};

const renderComponent = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/pilots/pilot-alpha-001']}>
        <Routes>
          <Route path="/pilots/:pilotId" element={<PilotDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('PilotDetailPage Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pilot detail header, status badge, scope strip, and navigation tabs', () => {
    mockUsePilotStatus.mockReturnValue({
      data: mockActivePilot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotTelemetry.mockReturnValue({
      data: mockTelemetryNoLatency,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotDossier.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    });

    renderComponent();

    // Verify Title & Status
    expect(screen.getByText('pilot-alpha-001')).toBeInTheDocument();
    expect(screen.getByText('Operational Active')).toBeInTheDocument();
    expect(screen.getAllByText('consortium-main').length).toBeGreaterThanOrEqual(1);

    // Verify Tabs
    expect(screen.getByText('Scope & Configuration')).toBeInTheDocument();
    expect(screen.getByText('Preflight Readiness')).toBeInTheDocument();
    expect(screen.getByText('Institutional Approvals')).toBeInTheDocument();
    expect(screen.getByText('Operational Controls')).toBeInTheDocument();
    expect(screen.getByText('Telemetry & Execution')).toBeInTheDocument();
    expect(screen.getByText('Audit & Dossier')).toBeInTheDocument();
  });

  it('switches to Preflight Readiness tab and renders preflight items', () => {
    mockUsePilotStatus.mockReturnValue({
      data: mockActivePilot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotTelemetry.mockReturnValue({
      data: mockTelemetryNoLatency,
      isLoading: false,
      error: null,
    });
    mockUsePilotDossier.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    });

    renderComponent();

    // Click Preflight tab
    const preflightTab = screen.getByText('Preflight Readiness');
    fireEvent.click(preflightTab);

    expect(screen.getByText('INSTITUTION_MEMBERSHIP')).toBeInTheDocument();
    expect(screen.getByText('OPERATIONAL_GRANT_VALID')).toBeInTheDocument();
    expect(screen.getByText(/bank_a: ACTIVE; bank_b: ACTIVE/i)).toBeInTheDocument();
  });

  it('switches to Approvals tab and renders Bank A, Bank B, and Operator cards', () => {
    const pilotWithUppercaseOperator: PilotStatusResponse = {
      ...mockActivePilot,
      approvals: {
        bank_a: {
          approver_id: 'user_admin_a',
          approver_institution: 'bank_a',
          approver_role: 'admin',
          scope_digest: '9f8e7d6c5b4a3120fedcba0987654321',
          approved_at: '2026-01-01T01:00:00Z',
          signature: 'sig_ed25519_bank_a',
        },
        bank_b: {
          approver_id: 'user_admin_b',
          approver_institution: 'bank_b',
          approver_role: 'admin',
          scope_digest: '9f8e7d6c5b4a3120fedcba0987654321',
          approved_at: '2026-01-01T01:05:00Z',
          signature: 'sig_ed25519_bank_b',
        },
        OPERATOR: {
          approver_id: 'operator_chief',
          approver_institution: 'OPERATOR',
          approver_role: 'operator',
          scope_digest: '9f8e7d6c5b4a3120fedcba0987654321',
          approved_at: '2026-01-01T01:10:00Z',
        },
      },
    };

    mockUsePilotStatus.mockReturnValue({
      data: pilotWithUppercaseOperator,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotTelemetry.mockReturnValue({
      data: mockTelemetryNoLatency,
      isLoading: false,
      error: null,
    });
    mockUsePilotDossier.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    });

    renderComponent();

    // Click Approvals tab
    const approvalsTab = screen.getByText('Institutional Approvals');
    fireEvent.click(approvalsTab);

    expect(screen.getByText('BANK_A')).toBeInTheDocument();
    expect(screen.getByText('BANK_B')).toBeInTheDocument();
    expect(screen.getByText('Consortium Operator')).toBeInTheDocument();
    // All 3 entities rendered as APPROVED (no false AWAITING)
    expect(screen.getAllByText('APPROVED').length).toBe(3);
    expect(screen.queryByText('AWAITING')).not.toBeInTheDocument();
  });

  it('renders restricted approval form when user is authorized for specific institution', () => {
    const awaitingPilot: PilotStatusResponse = {
      ...mockActivePilot,
      status: 'AWAITING_APPROVAL',
      is_fully_approved: false,
      approvals: {},
      permitted_actions: ['approve'],
      approvable_institutions: ['bank_a'],
      caller_approver_id: 'user_admin_a',
      canonical_signing_challenge: 'PILOT_APPROVAL:pilot-alpha-001:1:9f8e7d6c5b4a3120fedcba0987654321:bank_a:user_admin_a',
    };

    mockUsePilotStatus.mockReturnValue({
      data: awaitingPilot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotTelemetry.mockReturnValue({
      data: mockTelemetryNoLatency,
      isLoading: false,
      error: null,
    });
    mockUsePilotDossier.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    });

    renderComponent();

    const approvalsTab = screen.getByText('Institutional Approvals');
    fireEvent.click(approvalsTab);

    // Form is rendered
    expect(screen.getByText('Submit Authorized Institutional Approval')).toBeInTheDocument();
    // In dropdown, only BANK_A option is present (no OPERATOR or BANK_B options)
    expect(screen.getAllByText('BANK_A').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('CONSORTIUM OPERATOR')).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'BANK_B' })).not.toBeInTheDocument();
    // Canonical challenge is exposed
    expect(screen.getByText(/PILOT_APPROVAL:pilot-alpha-001:1:9f8e7d6c5b4a3120fedcba0987654321:bank_a:user_admin_a/i)).toBeInTheDocument();
  });

  it('displays honest empty state for latency observations in Telemetry tab', () => {
    mockUsePilotStatus.mockReturnValue({
      data: mockActivePilot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotTelemetry.mockReturnValue({
      data: mockTelemetryNoLatency,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotDossier.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    });

    renderComponent();

    // Click Telemetry tab
    const telemetryTab = screen.getByText('Telemetry & Execution');
    fireEvent.click(telemetryTab);

    // Gate E: Must report honest unavailable / 0 samples, NOT fake 0.0 ms
    expect(screen.getAllByText(/Unavailable \(0 samples\)/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('12 / 100 queries')).toBeInTheDocument();
  });

  it('opens Emergency Action modal in Controls tab with mandatory reason input', () => {
    mockUsePilotStatus.mockReturnValue({
      data: mockActivePilot,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });
    mockUsePilotTelemetry.mockReturnValue({
      data: mockTelemetryNoLatency,
      isLoading: false,
      error: null,
    });
    mockUsePilotDossier.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    });

    renderComponent();

    // Click Controls tab
    const controlsTab = screen.getByText('Operational Controls');
    fireEvent.click(controlsTab);

    // Verify Pause button is active
    const pauseBtn = screen.getByRole('button', { name: /Emergency Pause/i });
    fireEvent.click(pauseBtn);

    // Modal appears with mandatory operational reason
    expect(screen.getByText('Trigger Emergency Operational Pause')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Document the regulatory, operational, or compliance reason/i)).toBeInTheDocument();
  });
});
