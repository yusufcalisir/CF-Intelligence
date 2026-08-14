import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import Dashboard from '../../pages/Dashboard';
import AlertsPage from '../../pages/AlertsPage';
import CasesPage from '../../pages/CasesPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('Integration: Error Handling, Offline Fallbacks & Resilience', () => {
  const createWrapper = (initialRoute = '/alerts') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    return ({ children }: { children?: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/alerts" element={<AlertsPage />} />
              <Route path="/cases" element={<CasesPage />} />
            </Route>
          </Routes>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useDriftAnalysis').mockReturnValue({
      data: {
        is_drift_detected: false,
        max_psi: 0.02,
        features: [],
      },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useCreateSimulation').mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
  });

  it('renders clean empty state on AlertsPage when API returns empty list', () => {
    vi.spyOn(queries, 'useAlerts').mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);

    const Wrapper = createWrapper('/alerts');
    render(<Wrapper />);

    expect(screen.getByText(/Alert Intelligence/i)).toBeInTheDocument();
    expect(screen.getByText(/No alerts yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Run a scenario from the Scenarios page to generate alerts/i)).toBeInTheDocument();
  });

  it('renders clean empty state on CasesPage when no active SAR cases exist', () => {
    vi.spyOn(queries, 'useCases').mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    const Wrapper = createWrapper('/cases');
    render(<Wrapper />);

    expect(screen.getByText(/Case Management/i)).toBeInTheDocument();
    expect(screen.getByText(/No cases yet/i)).toBeInTheDocument();
  });

  it('handles loading states gracefully without layout shift or crash', () => {
    vi.spyOn(queries, 'useBanks').mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as any);

    vi.spyOn(queries, 'useSimulations').mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as any);

    const Wrapper = createWrapper('/dashboard');
    render(<Wrapper />);

    expect(screen.getAllByText(/Collaborative Fraud Intelligence/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Participating Institutions/i)).toBeInTheDocument();
  });
});
