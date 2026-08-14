import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import PoliciesPage from '../PoliciesPage';
import * as queries from '../../api/queries';

describe('PoliciesPage (Rule Engine & AML Policies) Test Suite', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const mockRules = [
    {
      id: 'rule_01',
      rule_name: 'High Risk Jurisdiction Velocity Cap',
      action: 'BLOCK_TRANSACTION',
      condition: { and: [{ field: 'composite_risk_score', operator: '>=', value: 830 }] },
      is_active: true,
      priority: 10,
    },
    {
      id: 'rule_02',
      rule_name: 'Rapid Multi-Card Trial Flag',
      action: 'REQUIRE_STEP_UP_AUTH',
      condition: { and: [{ field: 'velocity', operator: '>=', value: 5.0 }] },
      is_active: false,
      priority: 20,
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useRules').mockReturnValue({
      data: mockRules,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useCreateRule').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ id: 'rule_new_99' }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useUpdateRule').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ id: 'rule_01' }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useDeleteRule').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ id: 'rule_01' }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useTestRule').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ matches: true, message: 'Rule condition matched test payload' }),
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
});
