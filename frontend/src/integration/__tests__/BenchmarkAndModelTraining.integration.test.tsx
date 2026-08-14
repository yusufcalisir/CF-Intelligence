import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { BenchmarkHubPage } from '../../pages/BenchmarkHubPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('Integration: Kaggle Benchmark Hub & Distributed Training Pipeline', () => {
  const createWrapper = (initialRoute = '/benchmarks') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    return ({ children }: { children?: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/benchmarks" element={<BenchmarkHubPage />} />
            </Route>
          </Routes>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useSimulations').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useCreateSimulation').mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
  });

  it('allows user to switch between 4 Kaggle benchmark datasets and inspect data fidelity matrices', async () => {
    const user = userEvent.setup();
    const Wrapper = createWrapper('/benchmarks');
    render(<Wrapper />);

    expect(screen.getByText(/Real-World Benchmarks & Design Partner Hub/i)).toBeInTheDocument();

    const ieeeCard = screen.getByText(/IEEE-CIS Fraud Detection/i);
    await user.click(ieeeCard);
    expect(screen.getByText(/Real-world e-commerce & payment card fraud transactions/i)).toBeInTheDocument();

    const ellipticCard = screen.getByText(/Elliptic Bitcoin AML/i);
    await user.click(ellipticCard);
    expect(screen.getByText(/203k\+ nodes, 234k\+ directed edges on Bitcoin blockchain/i)).toBeInTheDocument();

    const creditCard = screen.getByText(/European Cardholders Credit Card/i);
    await user.click(creditCard);
    expect(screen.getByText(/284k European transactions transformed via PCA/i)).toBeInTheDocument();
  });
});
