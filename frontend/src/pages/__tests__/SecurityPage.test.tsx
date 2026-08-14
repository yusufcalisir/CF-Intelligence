import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import SecurityPage from '../SecurityPage';

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

describe('SecurityPage', () => {
  it('renders security modules header and security posture summary', async () => {
    render(<SecurityPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/Enterprise Security & Identity Control Suite/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/All Modules/i)).toBeInTheDocument();
    expect(screen.getByText(/Verify SHA-256 Audit Chain/i)).toBeInTheDocument();
  });

  it('renders and switches between security module tabs', async () => {
    render(<SecurityPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/mTLS & Cert PKI/i)).toBeInTheDocument();
    });

    // Click zk-SNARK
    fireEvent.click(screen.getByText(/zk-SNARK Attestation/i));
    await waitFor(() => {
      expect(screen.getByText(/Groth16 zk-SNARK Model Weight Attestation/i)).toBeInTheDocument();
    });

    // Click Dynamic ABAC
    fireEvent.click(screen.getByText(/Dynamic ABAC Rules/i));
    await waitFor(() => {
      expect(screen.getByText(/Interactive ABAC Policy Simulator/i)).toBeInTheDocument();
    });
  });
});
