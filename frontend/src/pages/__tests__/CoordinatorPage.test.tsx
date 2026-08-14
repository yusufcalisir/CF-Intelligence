import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import CoordinatorPage from '../CoordinatorPage';

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

describe('CoordinatorPage', () => {
  it('renders coordinator dashboard title and client node statuses', () => {
    render(<CoordinatorPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Federated Coordinator Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/Dynamic client registry, live heartbeat monitoring/i)).toBeInTheDocument();
    expect(screen.getAllByText(/bank_alpha/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bank_beta/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bank_gamma/i).length).toBeGreaterThan(0);
  });

  it('renders hyperparameter negotiation controls and hardware specs', () => {
    render(<CoordinatorPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Base Batch Size/i)).toBeInTheDocument();
    expect(screen.getByText(/Refresh Registry/i)).toBeInTheDocument();
  });
});
