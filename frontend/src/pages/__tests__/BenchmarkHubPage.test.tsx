import { describe, it, expect } from 'vitest';
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
    const paySimButton = screen.getAllByText(/PaySim Mobile Money/i)[0];
    if (paySimButton) {
      fireEvent.click(paySimButton);
    }
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

  it('calibrates dynamic daily clearing volume and sample size via sliders and preset tiers', () => {
    render(<BenchmarkHubPage />, { wrapper: createWrapper() });

    // Verify workload calibration header
    expect(screen.getByText(/Institutional Workload & Volume Calibration/i)).toBeInTheDocument();
    expect(screen.getByText(/Live Dynamic ROI Engine/i)).toBeInTheDocument();

    // Verify initial values
    expect(screen.getByText(/100,000 tx \/ day/i)).toBeInTheDocument();
    expect(screen.getByText(/10,000 records/i)).toBeInTheDocument();

    // Change volume slider
    const volumeSlider = screen.getByLabelText(/Daily Clearing Volume Slider/i);
    fireEvent.change(volumeSlider, { target: { value: '500000' } });
    expect(screen.getByText(/500,000 tx \/ day/i)).toBeInTheDocument();

    // Click 1M volume preset tier
    const preset1M = screen.getByLabelText(/Set daily volume to 1M/i);
    fireEvent.click(preset1M);
    expect(screen.getByText(/1,000,000 tx \/ day/i)).toBeInTheDocument();

    // Click 25K sample size preset tier
    const samplePreset25K = screen.getByLabelText(/Set sample size to 25K/i);
    fireEvent.click(samplePreset25K);
    expect(screen.getByText(/25,000 records/i)).toBeInTheDocument();

    // Click Reset Standards button
    const resetButton = screen.getByRole('button', { name: /Reset Workload Parameters/i });
    fireEvent.click(resetButton);
    expect(screen.getByText(/100,000 tx \/ day/i)).toBeInTheDocument();
    expect(screen.getByText(/10,000 records/i)).toBeInTheDocument();
  });

  it('renders live institutional alert fatigue & economic ROI impact panel with metric cards', () => {
    render(<BenchmarkHubPage />, { wrapper: createWrapper() });

    // Verify ROI & savings panel
    expect(screen.getByText(/Institutional Economic ROI & Operational Capacity Impact/i)).toBeInTheDocument();
    expect(screen.getByText(/Projected Financial & Labor Savings/i)).toBeInTheDocument();

    // Verify all 4 economic metric cards
    expect(screen.getByText(/Annual Net Economic Benefit/i)).toBeInTheDocument();
    expect(screen.getByText(/Daily False Positives Avoided/i)).toBeInTheDocument();
    expect(screen.getByText(/Analyst Labor Saved Annually/i)).toBeInTheDocument();
    expect(screen.getByText(/Daily Fraud Losses Prevented/i)).toBeInTheDocument();

    // Verify ROI multiple badge
    expect(screen.getByText(/ROI Multiple/i)).toBeInTheDocument();
  });
});
