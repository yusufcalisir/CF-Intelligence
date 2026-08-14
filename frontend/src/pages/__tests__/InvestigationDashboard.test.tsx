import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import InvestigationDashboard from '../InvestigationDashboard';

import { apiClient } from '../../api/client';

const mockDashboardStats = {
  total_alerts: 154,
  critical_alerts: 12,
  high_alerts: 24,
  open_cases: 7,
  total_entities: 89,
  shared_intelligence_items: 5,
  graph_clusters: 4,
  active_scenarios: 3,
  cross_institution_matches: 14,
  active_banks: 3,
};

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
};

describe('InvestigationDashboard', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'get').mockImplementation(async (url: string) => {
      if (url.includes('/dashboard/stats')) {
        return { data: mockDashboardStats };
      }
      if (url.includes('/dashboard/intelligence-stats')) {
        return { data: { total_shared: 5, pending_approvals: 2, privacy_preserved_queries: 18 } };
      }
      return { data: [] };
    });
  });

  it('renders investigation dashboard overview and KPI sections', async () => {
    render(<InvestigationDashboard />, { wrapper: createWrapper() });

    expect(await screen.findByText(/Investigation Dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/AML Intelligence Control/i)).toBeInTheDocument();
    expect(await screen.findByText(/Total Alerts/i)).toBeInTheDocument();
    expect(await screen.findByText(/Critical Alerts/i)).toBeInTheDocument();
  });

  it('renders quick navigation links to scenarios and workbench', async () => {
    render(<InvestigationDashboard />, { wrapper: createWrapper() });

    expect(await screen.findByText(/Run Scenario/i)).toBeInTheDocument();
    const links = screen.getAllByRole('link');
    const hasWorkbench = links.some((l) => l.textContent?.includes('Workbench'));
    expect(hasWorkbench).toBe(true);
  });
});
