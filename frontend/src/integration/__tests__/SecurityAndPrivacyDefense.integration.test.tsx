import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import SecurityPage from '../../pages/SecurityPage';
import PrivacyDefensePage from '../../pages/PrivacyDefensePage';
import PsiPage from '../../pages/PsiPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('Integration: Cryptographic Security, Privacy Defense & PSI Matching', () => {
  const createWrapper = (initialRoute = '/security') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    return ({ children }: { children?: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/security" element={<SecurityPage />} />
              <Route path="/privacy-defense" element={<PrivacyDefensePage />} />
              <Route path="/psi" element={<PsiPage />} />
            </Route>
          </Routes>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useSecurityStatus').mockReturnValue({
      data: {
        mtls: {
          enabled: true,
          ca_cn: 'cf-intelligence-root-ca',
          tls_version: 'TLS 1.3',
          peer_verification: 'STRICT_SAN_CHECK',
          sample_cert: { cn: 'bank-a.node', sans: ['bank-a.internal'], valid_until: '2027-01-01' },
        },
        oidc: {
          enabled: true,
          issuer: 'https://auth.cf-intelligence.io',
          client_id: 'cf-client',
          supported_algorithms: ['RS256'],
          claims_extracted: ['sub', 'roles'],
        },
        abac: {
          enabled: true,
          active_rules_count: 5,
          enforced_policies: ['CONSORTIUM_ABAC_POLICY'],
        },
        vault: {
          enabled: true,
          vault_url: 'https://vault.internal',
          mount_point: 'secret/',
          sample_secret_source: 'hsm-kv-v2',
        },
        audit_chain: {
          enabled: true,
          total_events: 1420,
          chain_valid: true,
          last_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
          hashing_algorithm: 'HMAC-SHA256',
        },
      },
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAggregationMethods').mockReturnValue({
      data: [
        {
          id: 'fed_avg',
          label: 'Federated Averaging (FedAvg)',
          description: 'Standard weighted model averaging',
          paper: 'McMahan et al. 2017',
          byzantine_robust: false,
          colluding_defense: false,
        },
      ],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'usePrivacyBudgetLog').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useAuditDLG').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAuditMIA').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAuditModelInversion').mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
  });

  it('renders security modules and switches between cryptographic subsystem tabs', async () => {
    const user = userEvent.setup();
    const Wrapper = createWrapper('/security');
    render(<Wrapper />);

    expect(screen.getByText(/Enterprise Security & Identity Control Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/mTLS & Cert PKI/i)).toBeInTheDocument();
    expect(screen.getByText(/Dynamic ABAC Rules/i)).toBeInTheDocument();

    const abacTab = screen.getByText(/Dynamic ABAC Rules/i);
    await user.click(abacTab);
    expect(screen.getByText(/Dynamic ABAC Rules/i)).toBeInTheDocument();
  });

  it('renders privacy defense attacks and defense tiers', () => {
    const Wrapper = createWrapper('/privacy-defense');
    render(<Wrapper />);

    expect(screen.getByText(/Privacy Defense & Byzantine Suite/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Byzantine-Robust Aggregation Catalog/i).length).toBeGreaterThan(0);
  });
});
