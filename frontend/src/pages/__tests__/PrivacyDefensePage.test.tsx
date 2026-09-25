import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import PrivacyDefensePage from '../PrivacyDefensePage';

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

describe('PrivacyDefensePage', () => {
  it('renders privacy defense header and attack audit modules', () => {
    render(<PrivacyDefensePage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Privacy Defense & Byzantine Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/Zero Raw PII Verified/i)).toBeInTheDocument();
    expect(screen.getByText(/Byzantine Defenses/i)).toBeInTheDocument();
    expect(screen.getByText(/Attack Audits/i)).toBeInTheDocument();
    expect(screen.getByText(/Privacy Budget Log/i)).toBeInTheDocument();
  });

  it('renders attack audits tab with MIA empirical simulator', async () => {
    render(<PrivacyDefensePage />, { wrapper: createWrapper() });

    const auditTabBtn = screen.getByRole('button', { name: /Attack Audits/i });
    fireEvent.click(auditTabBtn);

    expect(await screen.findByText(/Adversarial Privacy Attack Evaluators/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Membership Inference Attack \(MIA\)/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Membership Inference Attack \(MIA\) Empirical Simulator/i)).toBeInTheDocument();
    expect(screen.getByText(/Differential Privacy Noise Level/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run MIA Audit/i })).toBeInTheDocument();
  });

  it('renders privacy budget log tab with circuit breaker and bank RDP accountants', async () => {
    render(<PrivacyDefensePage />, { wrapper: createWrapper() });

    const budgetTabBtn = screen.getByRole('button', { name: /Privacy Budget Log/i });
    fireEvent.click(budgetTabBtn);

    expect(await screen.findByText(/Enterprise Privacy Budget Audit Log \(DP-SGD ε\)/i)).toBeInTheDocument();
    expect(screen.getByText(/CONSORTIUM SAFETY LOCK/i)).toBeInTheDocument();
    expect(screen.getByText(/Bank-Level Rényi DP Accountants/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Emergency Safety Freeze/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset All Budgets/i })).toBeInTheDocument();
  });
});

