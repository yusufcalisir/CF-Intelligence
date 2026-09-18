import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

  it('switches between Auto, Live Edge Daemons, and Sandbox Demonstration modes', async () => {
    render(<CoordinatorPage />, { wrapper: createWrapper() });

    // Mode selector buttons should exist
    const autoBtn = screen.getByRole('button', { name: /^Auto$/i });
    const liveBtn = screen.getByRole('button', { name: /Live/i });
    const sandboxBtn = screen.getByRole('button', { name: /Sandbox/i });

    expect(autoBtn).toBeInTheDocument();
    expect(liveBtn).toBeInTheDocument();
    expect(sandboxBtn).toBeInTheDocument();

    // Click Live mode (with no live clients registered in query, displays awaiting edge nodes card)
    fireEvent.click(liveBtn);
    expect(screen.getByText(/Awaiting Live Edge Nodes/i)).toBeInTheDocument();
    expect(screen.getByText(/0\.0\.0\.0:50051/i)).toBeInTheDocument();
    expect(screen.getByText(/Start Bank Edge Node CLI/i)).toBeInTheDocument();

    // Click Sandbox mode: returns to simulated nodes
    fireEvent.click(sandboxBtn);
    expect(screen.getByText(/Sandbox Consortium Demonstration Nodes/i)).toBeInTheDocument();
    expect(screen.getAllByText(/bank_alpha/i).length).toBeGreaterThan(0);
  });
});
