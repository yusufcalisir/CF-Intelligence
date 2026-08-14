import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { BenchmarkHubPage } from '../BenchmarkHubPage';

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

describe('BenchmarkHubPage', () => {
  it('renders benchmark hub title and dataset selector cards', () => {
    render(<BenchmarkHubPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Real-World Benchmarks & Design Partner Hub/i)).toBeInTheDocument();
    expect(screen.getAllByText(/PaySim Mobile Money/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/IEEE-CIS Fraud Detection/i)).toBeInTheDocument();
    expect(screen.getByText(/Elliptic Bitcoin AML/i)).toBeInTheDocument();
    expect(screen.getByText(/European Cardholders Credit Card/i)).toBeInTheDocument();
  });

  it('allows switching between all 4 Kaggle benchmark datasets', () => {
    render(<BenchmarkHubPage />, { wrapper: createWrapper() });

    // Click IEEE-CIS
    fireEvent.click(screen.getByText(/IEEE-CIS Fraud Detection/i));
    expect(screen.getByText(/Real-world e-commerce & payment card fraud transactions/i)).toBeInTheDocument();

    // Click Elliptic
    fireEvent.click(screen.getByText(/Elliptic Bitcoin AML/i));
    expect(screen.getByText(/203k\+ nodes, 234k\+ directed edges on Bitcoin blockchain/i)).toBeInTheDocument();

    // Click Credit Card
    fireEvent.click(screen.getByText(/European Cardholders Credit Card/i));
    expect(screen.getByText(/284k European transactions transformed via PCA/i)).toBeInTheDocument();

    // Click PaySim
    fireEvent.click(screen.getAllByText(/PaySim Mobile Money/i)[0]);
    expect(screen.getByText(/Derived from real M-Pesa mobile transaction logs/i)).toBeInTheDocument();
  });

  it('allows navigating between sub-tabs (Confusion Matrix, Data Fidelity, Pilot Sandbox)', () => {
    render(<BenchmarkHubPage />, { wrapper: createWrapper() });

    // Switch to Confusion & Cost
    fireEvent.click(screen.getByText(/Confusion & Cost/i));
    expect(screen.getByText(/Multi-Threshold Operational Decision Matrix/i)).toBeInTheDocument();

    // Switch to Distribution Fidelity
    fireEvent.click(screen.getByText(/Distribution Fidelity/i));
    expect(screen.getByText(/Statistical Fidelity & Distribution Shift Auditor/i)).toBeInTheDocument();

    // Switch to Design Partner Sandbox
    fireEvent.click(screen.getByText(/Design Partner Sandbox/i));
    expect(screen.getByText(/Zero-Raw-PII Ingestion & Regex Scanner/i)).toBeInTheDocument();
  });
});
