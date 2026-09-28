import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import SecurityPage from '../SecurityPage';
import * as queries from '../../api/queries';

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
  beforeEach(() => {
    vi.spyOn(queries, 'useSecurityStatus').mockReturnValue({
      data: {
        mtls: { enabled: true, ca_cn: 'Meridian Root CA', tls_version: 'TLSv1.3', peer_verification: 'VERIFIED', sample_cert: { cn: 'bank-node.meridian', sans: ['DNS:bank-node.meridian'], valid_until: '2027-01-01' } },
        oidc: { provider: 'Keycloak', issuer: 'https://auth.bank', client_id: 'cf-intelligence' },
        abac: { active_policies: 12, default_action: 'DENY', enforced_policies: ['POL-01: Cross-Bank Isolation'] },
        vault: { vault_url: 'http://localhost:8200', mount_point: 'secret/', sample_secret_source: 'VAULT_KV' },
        audit_chain: { length: 154, last_hash: 'abc' },
      },
      isLoading: false,
    } as any);
  });
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
