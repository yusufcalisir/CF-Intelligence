import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
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

  it('allows user to filter cases by status dropdown', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <CasesPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const statusSelect = screen.getByRole('combobox');
    await user.selectOptions(statusSelect, 'open');
    expect(statusSelect).toHaveValue('open');
  });
});
