import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PilotOperationsPage from '../PilotOperationsPage';
import type { PilotStatusResponse } from '../../api/types';

// Mock pilots list hook
const mockUsePilotsList = vi.fn();
vi.mock('../../api/pilots', () => ({
  usePilotsList: () => mockUsePilotsList(),
  useProvisionPilot: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));

const mockPilots: PilotStatusResponse[] = [
  {
    pilot_id: 'pilot-alpha-001',
    revision: 1,
    consortium_id: 'consortium-main',
    status: 'ACTIVE',
    status_reason: 'Operating under agreed quota.',
    scope_digest: 'digest_alpha_1234567890abcdef',
    is_fully_approved: true,
    passed: true,
    queries_executed: 15,
    queries_denied: 1,
    queries_total_quota: 100,
    participating_institutions: ['bank_a', 'bank_b'],
    allowed_purposes: ['AML_INVESTIGATION'],
    allowed_jurisdictions: ['EU'],
    operational_grant_id: 'grant-001',
    starts_at: '2026-01-01T00:00:00Z',
    expires_at: '2026-12-31T23:59:59Z',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    preflight_checks: [],
    approvals: {},
    permitted_actions: ['pause', 'export_dossier'],
    missing_approvals: [],
    has_expired: false,
  },
  {
    pilot_id: 'pilot-beta-002',
    revision: 1,
    consortium_id: 'consortium-main',
    status: 'DRAFT',
    status_reason: 'Draft state awaiting preflight.',
    scope_digest: 'digest_beta_0987654321fedcba',
    is_fully_approved: false,
    passed: false,
    queries_executed: 0,
    queries_denied: 0,
    queries_total_quota: 50,
    participating_institutions: ['bank_a', 'bank_c'],
    allowed_purposes: ['FRAUD_DETECTION'],
    allowed_jurisdictions: ['US'],
    operational_grant_id: 'grant-002',
    starts_at: '2026-02-01T00:00:00Z',
    expires_at: '2026-03-01T23:59:59Z',
    created_at: '2026-02-01T00:00:00Z',
    updated_at: '2026-02-01T00:00:00Z',
    preflight_checks: [],
    approvals: {},
    permitted_actions: ['preflight', 'approve', 'abort'],
    missing_approvals: ['bank_a', 'bank_c', 'OPERATOR'],
    has_expired: false,
  },
];

const renderComponent = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <PilotOperationsPage />
      </BrowserRouter>
    </QueryClientProvider>
  );
};

describe('PilotOperationsPage Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pilot operations header, KPI cards, and pilot cards', () => {
    mockUsePilotsList.mockReturnValue({
      data: mockPilots,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });

    renderComponent();

    // Verify Title & Subtitle
    expect(screen.getByText('Consortium Pilot Operations')).toBeInTheDocument();
    expect(
      screen.getByText(/Controlled two-bank pilot execution, authorization verification/i)
    ).toBeInTheDocument();

    // Verify KPI Metrics
    expect(screen.getByText('Registered consortium agreements')).toBeInTheDocument();
    expect(screen.getByText('Executing live query traffic')).toBeInTheDocument();

    // Verify Pilot Cards are rendered
    expect(screen.getByText('pilot-alpha-001')).toBeInTheDocument();
    expect(screen.getByText('pilot-beta-002')).toBeInTheDocument();
    expect(screen.getByText('Operational Active')).toBeInTheDocument();
    expect(screen.getAllByText('Draft').length).toBeGreaterThanOrEqual(1);
  });

  it('renders honest empty state when user institution participates in zero pilots', () => {
    mockUsePilotsList.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });

    renderComponent();

    expect(screen.getByText('No Registered Consortium Pilots')).toBeInTheDocument();
    expect(
      screen.getByText(/Your institution is not currently participating in any active or drafted pilots/i)
    ).toBeInTheDocument();
    expect(screen.getByText('Provision First Pilot')).toBeInTheDocument();
  });

  it('filters pilots by search input', () => {
    mockUsePilotsList.mockReturnValue({
      data: mockPilots,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });

    renderComponent();

    const searchInput = screen.getByPlaceholderText(/Search by pilot ID/i);
    fireEvent.change(searchInput, { target: { value: 'beta' } });

    // pilot-beta-002 matches, pilot-alpha-001 should not appear
    expect(screen.getByText('pilot-beta-002')).toBeInTheDocument();
    expect(screen.queryByText('pilot-alpha-001')).not.toBeInTheDocument();
  });

  it('filters pilots by status dropdown', () => {
    mockUsePilotsList.mockReturnValue({
      data: mockPilots,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });

    renderComponent();

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'DRAFT' } });

    expect(screen.getByText('pilot-beta-002')).toBeInTheDocument();
    expect(screen.queryByText('pilot-alpha-001')).not.toBeInTheDocument();
  });

  it('opens provision modal when provision button is clicked', () => {
    mockUsePilotsList.mockReturnValue({
      data: mockPilots,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    });

    renderComponent();

    const provisionBtn = screen.getByRole('button', { name: /Provision Pilot/i });
    fireEvent.click(provisionBtn);

    expect(screen.getByText('Provision Governed Two-Bank Controlled Pilot')).toBeInTheDocument();
    expect(screen.getByText(/Participating Institution A/i)).toBeInTheDocument();
    expect(screen.getByText(/Participating Institution B/i)).toBeInTheDocument();
  });
});
