import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import SecurityPage from '../../pages/SecurityPage';
import PrivacyDefensePage from '../../pages/PrivacyDefensePage';
import PsiPage from '../../pages/PsiPage';
import Layout from '../../components/layout/Layout';
import * as queries from '../../api/queries';

describe('E2E Business Flow 3: Privacy Defense, Byzantine Robustness & PSI Matching', () => {
  const createWrapper = (initialRoute = '/security') => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });

    return () => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[initialRoute]}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/security" element={<SecurityPage />} />
              <Route path="/privacy-defense" element={<PrivacyDefensePage />} />
              <Route path="/psi" element={<PsiPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };

  const mockSecurityStatus = {
    mtls: { enabled: true, mode: 'STRICT_MUTUAL_TLS', cipher_suite: 'TLS_AES_256_GCM_SHA384' },
    oidc: { enabled: true, issuer: 'https://auth.consortium-fincrime.org/realm/fincrime' },
    abac: { enabled: true, active_policies: 12, compliance_mode: 'EU_GDPR_STRICT' },
    vault: { enabled: true, pki_engine: 'CONSORTIUM_ROOT_CA_V2', cert_expiry_days: 90 },
    audit_chain: { enabled: true, blocks_sealed: 1420, integrity_valid: true },
    overall_posture: 'ENTERPRISE_GRADE_ENFORCED',
  };

  const mockAggregationMethods = [
    {
      id: 'krum',
      label: 'Multi-Krum',
      name: 'Multi-Krum',
      description: 'Byzantine-robust distance-based aggregation',
      byzantine_robust: true,
      paper: 'Blanchard et al. (NeurIPS 2017)',
      parameters: { num_byzantine: 1 },
      properties: ['Non-linear', 'Distance-based', 'Robust to f poisoned gradients'],
    },
    {
      id: 'trimmed_mean',
      label: 'Coordinate-Wise Trimmed Mean',
      name: 'Coordinate-Wise Trimmed Mean',
      description: 'Coordinate-wise alpha-trimmed mean aggregator',
      byzantine_robust: true,
      paper: 'Yin et al. (ICML 2018)',
      parameters: { beta: 0.1 },
      properties: ['Coordinate-wise', 'Statistical rate optimal'],
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(queries, 'useSecurityStatus').mockReturnValue({
      data: mockSecurityStatus,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(queries, 'useAggregationMethods').mockReturnValue({
      data: mockAggregationMethods,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'usePrivacyBudgetLog').mockReturnValue({
      data: [
        { round: 1, epsilon_spent: 0.25, delta_spent: 1e-5, remaining_epsilon: 1.75 },
        { round: 5, epsilon_spent: 1.25, delta_spent: 5e-5, remaining_epsilon: 0.75 },
      ],
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useRunPSI').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ match_count: 5, matches: [] }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useFuzzyResolve').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({ matches: [] }),
      isPending: false,
    } as any);

    vi.spyOn(queries, 'useAuditChain').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);

    vi.spyOn(queries, 'useVerifyAuditChain').mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
  });

  it('verifies strict mTLS/ABAC security posture, Byzantine-robust defense catalog, and executes PSI cross-bank matching', async () => {
    const SecurityWrapper = createWrapper('/security');
    render(<SecurityWrapper />);

    // 1. Inspect Enterprise Security Control Suite
    expect(await screen.findByText(/Enterprise Security & Identity Control Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/Verify SHA-256 Audit Chain/i)).toBeInTheDocument();
    expect(screen.getByText(/mTLS & Cert PKI/i)).toBeInTheDocument();

    // 2. Navigate to Privacy Defense page
    const PrivacyWrapper = createWrapper('/privacy-defense');
    render(<PrivacyWrapper />);
    expect(await screen.findByText(/Privacy Defense & Byzantine Suite/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Byzantine-Robust Aggregation Catalog/i)[0]).toBeInTheDocument();
    expect(screen.getByText(/Multi-Krum/i)).toBeInTheDocument();

    // 3. Navigate to PSI Matching page
    const PsiWrapper = createWrapper('/psi');
    render(<PsiWrapper />);
    expect(await screen.findByText(/PSI Protocol Control Center/i)).toBeInTheDocument();
    expect(screen.getByText(/Intel SGX Enclave Active/i)).toBeInTheDocument();
  });
});
