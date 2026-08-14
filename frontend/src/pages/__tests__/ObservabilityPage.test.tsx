import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import ObservabilityPage from '../ObservabilityPage';

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

describe('ObservabilityPage', () => {
  it('renders observability header, drift tabs, and retrain trigger button', () => {
    render(<ObservabilityPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Enterprise Observability & Drift Monitoring/i)).toBeInTheDocument();
    expect(screen.getByText(/Trigger Automated Re-training/i)).toBeInTheDocument();
    expect(screen.getByText(/Simulate Severe Drift/i)).toBeInTheDocument();
  });

  it('allows switching between drift, calibration, alerts, and telemetry tabs', () => {
    render(<ObservabilityPage />, { wrapper: createWrapper() });

    // Switch to Calibration tab
    const calibTab = screen.getByText(/Calibration Curve/i);
    expect(calibTab).toBeInTheDocument();
    fireEvent.click(calibTab);

    // Switch to Telemetry tab
    const telemTab = screen.getByText(/Loki & OpenTelemetry/i);
    expect(telemTab).toBeInTheDocument();
    fireEvent.click(telemTab);
  });
});
