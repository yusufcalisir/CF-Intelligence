import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import CasesPage from '../CasesPage';
import * as queries from '../../api/queries';

describe('CasesPage Integration Test Suite', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const mockCases = [
    {
      id: 'case_101',
      title: 'Consortium Structuring Scheme Alpha',
      priority: 'p1_critical',
      status: 'open',
      created_at: '2026-08-14T09:00:00Z',
      assigned_to: 'Financial Crime Lead',
      alerts_count: 4,
    },
    {
      id: 'case_102',
      title: 'High Velocity Synthetic Identity Probe',
      priority: 'p2_high',
      status: 'under_review',
      created_at: '2026-08-14T11:30:00Z',
      assigned_to: 'AML Compliance Officer',
      alerts_count: 2,
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    window.history.replaceState({}, '', '/cases');
    vi.spyOn(queries, 'useCases').mockReturnValue({
      data: mockCases,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCreateCase').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ id: 'case_new_999' }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useUpdateAlertStatus').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ id: 'alt_001', status: 'escalated' }),
      isPending: false,
    } as any);
  });

  it('renders case management header, status filter, and active case cards', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Case Management/i)).toBeInTheDocument();
    expect(screen.getByText(/Consortium Structuring Scheme Alpha/i)).toBeInTheDocument();
    expect(screen.getByText(/High Velocity Synthetic Identity Probe/i)).toBeInTheDocument();
    expect(screen.getByText(/2 cases/i)).toBeInTheDocument();
  });

  it('opens new case modal and creates a case on user submit', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const newCaseBtn = screen.getByRole('button', { name: /\+ New Case/i });
    await user.click(newCaseBtn);

    expect(screen.getByText(/New Investigation Case/i)).toBeInTheDocument();

    const titleInput = screen.getByPlaceholderText(/Suspicious transaction cluster at Meridian\.\.\./i);
    await user.type(titleInput, 'Cross-Border Smurfing Ring 404');

    const submitBtn = screen.getByRole('button', { name: /^Create Case$/i });
    await user.click(submitBtn);

    expect(screen.getByText(/Case Management/i)).toBeInTheDocument();
  });

  it('allows user to filter cases by status dropdown and persists to sessionStorage', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const statusSelect = screen.getByLabelText(/Status:/i);
    await user.selectOptions(statusSelect, 'open');
    expect(statusSelect).toHaveValue('open');
    expect(sessionStorage.getItem('cfi_cases_status_filter')).toBe('open');
  });

  it('allows user to filter cases by priority dropdown and persists to sessionStorage', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const prioritySelect = screen.getByLabelText(/Priority:/i);
    await user.selectOptions(prioritySelect, 'p1_critical');
    expect(prioritySelect).toHaveValue('p1_critical');
    expect(sessionStorage.getItem('cfi_cases_priority_filter')).toBe('p1_critical');
  });

  it('filters cases in real-time via text search input and allows clearing search', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const searchInput = screen.getByPlaceholderText(/Search case, ID, lead\.\.\./i);
    await user.type(searchInput, 'Structuring');

    // Only case_101 matches 'Structuring'
    expect(screen.getByText(/Consortium Structuring Scheme Alpha/i)).toBeInTheDocument();
    expect(screen.queryByText(/High Velocity Synthetic Identity Probe/i)).not.toBeInTheDocument();
    expect(screen.getByText(/1 case \(of 2\)/i)).toBeInTheDocument();

    // Clear search with the X button
    const clearSearchBtn = screen.getByLabelText(/Clear search/i);
    await user.click(clearSearchBtn);

    expect(screen.getByText(/Consortium Structuring Scheme Alpha/i)).toBeInTheDocument();
    expect(screen.getByText(/High Velocity Synthetic Identity Probe/i)).toBeInTheDocument();
    expect(screen.getByText(/2 cases/i)).toBeInTheDocument();
  });

  it('initializes filters directly from URL search parameters', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/cases?status=open&priority=p1_critical&q=Alpha']}>
          <CasesPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const statusSelect = screen.getByLabelText(/Status:/i);
    const prioritySelect = screen.getByLabelText(/Priority:/i);
    const searchInput = screen.getByPlaceholderText(/Search case, ID, lead\.\.\./i);

    expect(statusSelect).toHaveValue('open');
    expect(prioritySelect).toHaveValue('p1_critical');
    expect(searchInput).toHaveValue('Alpha');
    expect(screen.getByText(/Consortium Structuring Scheme Alpha/i)).toBeInTheDocument();
    expect(screen.queryByText(/High Velocity Synthetic Identity Probe/i)).not.toBeInTheDocument();
  });

  it('restores filters from sessionStorage when URL parameters are not set', () => {
    sessionStorage.setItem('cfi_cases_status_filter', 'pending_review');
    sessionStorage.setItem('cfi_cases_priority_filter', 'p2_high');
    sessionStorage.setItem('cfi_cases_search_query', 'Synthetic');

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const statusSelect = screen.getByLabelText(/Status:/i);
    const prioritySelect = screen.getByLabelText(/Priority:/i);
    const searchInput = screen.getByPlaceholderText(/Search case, ID, lead\.\.\./i);

    expect(statusSelect).toHaveValue('pending_review');
    expect(prioritySelect).toHaveValue('p2_high');
    expect(searchInput).toHaveValue('Synthetic');
  });

  it('renders dedicated filter empty state with Clear Status Filter button', async () => {
    const user = userEvent.setup();

    vi.spyOn(queries, 'useCases').mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const statusSelect = screen.getByLabelText(/Status:/i);
    await user.selectOptions(statusSelect, 'closed_false_positive');

    expect(screen.getByText(/No cases matching filter criteria/i)).toBeInTheDocument();
    const clearBtn = screen.getByRole('button', { name: /Clear Status Filter/i });
    expect(clearBtn).toBeInTheDocument();

    await user.click(clearBtn);
    expect(statusSelect).toHaveValue('');
  });

  it('resets all filters and storage when Clear Filters button is clicked', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const statusSelect = screen.getByLabelText(/Status:/i);
    const prioritySelect = screen.getByLabelText(/Priority:/i);
    const searchInput = screen.getByPlaceholderText(/Search case, ID, lead\.\.\./i);

    await user.selectOptions(statusSelect, 'open');
    await user.selectOptions(prioritySelect, 'p1_critical');
    await user.type(searchInput, 'Alpha');

    const clearAllBtn = screen.getByRole('button', { name: /Clear Filters/i });
    expect(clearAllBtn).toBeInTheDocument();
    await user.click(clearAllBtn);

    expect(statusSelect).toHaveValue('');
    expect(prioritySelect).toHaveValue('');
    expect(searchInput).toHaveValue('');
    expect(sessionStorage.getItem('cfi_cases_status_filter')).toBeNull();
    expect(sessionStorage.getItem('cfi_cases_priority_filter')).toBeNull();
    expect(sessionStorage.getItem('cfi_cases_search_query')).toBeNull();
  });

  it('renders authentic empty state when no cases exist in the database', () => {
    vi.spyOn(queries, 'useCases').mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/No cases yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Create a case to start tracking/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /\+ Create First Case/i })).toBeInTheDocument();
  });

  it('handles inbound alert escalation deep link with pre-filled title and alert badge', async () => {
    const user = userEvent.setup();
    const createCaseMock = vi.fn().mockResolvedValue({ id: 'case_new_escalated' });
    const updateAlertStatusMock = vi.fn().mockResolvedValue({ id: 'alt_999', status: 'escalated' });

    vi.spyOn(queries, 'useCreateCase').mockReturnValue({
      mutateAsync: createCaseMock,
      isPending: false,
    } as any);
    vi.spyOn(queries, 'useUpdateAlertStatus').mockReturnValue({
      mutateAsync: updateAlertStatusMock,
      isPending: false,
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/cases?create=true&from_alert=alt_999&priority=p1_critical&title=AML+High+Risk+Investigation&risk_score=875.5']}>
          <CasesPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    // Verify modal is open and has inbound alert badge
    const modal = screen.getByRole('dialog');
    expect(within(modal).getByText(/Escalating from Inbound Alert/i)).toBeInTheDocument();
    expect(within(modal).getByText('alt_999')).toBeInTheDocument();
    expect(within(modal).getByText(/Risk: 875.5 \/ 1000/i)).toBeInTheDocument();

    // Verify pre-filled inputs
    const titleInput = within(modal).getByLabelText(/Title/i);
    expect(titleInput).toHaveValue('AML High Risk Investigation');
    const prioritySelect = within(modal).getByLabelText(/Priority/i);
    expect(prioritySelect).toHaveValue('p1_critical');
    const alertIdInput = within(modal).getByLabelText(/Linked Alert ID/i);
    expect(alertIdInput).toHaveValue('alt_999');

    // Submit case creation
    const submitBtn = within(modal).getByRole('button', { name: /Create Case/i });
    await user.click(submitBtn);

    // Verify createCase was invoked with alert_ids and total_risk_score
    expect(createCaseMock).toHaveBeenCalledTimes(1);
    expect(createCaseMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'AML High Risk Investigation',
        priority: 'p1_critical',
        alert_ids: ['alt_999'],
        total_risk_score: 875.5,
      })
    );

    // Verify alert status was patched
    expect(updateAlertStatusMock).toHaveBeenCalledTimes(1);
    expect(updateAlertStatusMock).toHaveBeenCalledWith(
      expect.objectContaining({
        alertId: 'alt_999',
        payload: expect.objectContaining({ status: 'escalated' }),
      })
    );
  });
});
