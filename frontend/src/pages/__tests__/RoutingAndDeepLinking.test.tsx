import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import Layout from '../../components/layout/Layout';
import LandingPage from '../LandingPage';
import Dashboard from '../Dashboard';
import LiveOperationsView from '../LiveOperationsView';
import { BenchmarkHubPage } from '../BenchmarkHubPage';
import InvestigationDashboard from '../InvestigationDashboard';
import AlertsPage from '../AlertsPage';
import CasesPage from '../CasesPage';
import CaseDetailPage from '../CaseDetailPage';
import PoliciesPage from '../PoliciesPage';
import PsiPage from '../PsiPage';
import SecurityPage from '../SecurityPage';
import ObservabilityPage from '../ObservabilityPage';
import ScenariosPage from '../ScenariosPage';
import GraphPage from '../GraphPage';
import BankOnboardingPage from '../BankOnboardingPage';
import CoordinatorPage from '../CoordinatorPage';
import PrivacyDefensePage from '../PrivacyDefensePage';
import { apiClient } from '../../api/client';

// Hoisted Mock for Three.js WebGLRenderer in headless Vitest runner
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();

  class MockWebGLRenderer {
    domElement = document.createElement('canvas');
    shadowMap = { enabled: false, type: 0 };
    setSize() {}
    setPixelRatio() {}
    render() {}
    dispose() {}
    clear() {}
  }

  return {
    ...actual,
    WebGLRenderer: MockWebGLRenderer,
  };
});

const mockBanks = [
  {
    id: 'bank_a',
    name: 'Meridian National Bank',
    tier: 'tier_1',
    description: 'Tier 1 Global National Bank',
    default_transactions: 125000,
    default_fraud_ratio: 0.024,
    fraud_pattern: 'High-frequency cross-border structuring',
    characteristics: ['Tier 1 Global', 'High Volume'],
  },
  {
    id: 'bank_b',
    name: 'Apex Commercial Bank',
    tier: 'tier_2',
    description: 'Regional Commercial Bank',
    default_transactions: 85000,
    default_fraud_ratio: 0.018,
    fraud_pattern: 'Synthetic identity loans',
    characteristics: ['Commercial Core', 'Low Latency'],
  },
];

const mockBankDistributions = {
  banks: {
    bank_a: {
      amount_histogram: { bin_edges: [0, 500, 1000], counts: [1200, 450] },
      hourly_fraud_rate: { 0: 0.01, 1: 0.02 },
      merchant_risk: { categories: ['retail', 'crypto'], fraud_rates: [0.01, 0.08] },
    },
    bank_b: {
      amount_histogram: { bin_edges: [0, 500, 1000], counts: [900, 300] },
      hourly_fraud_rate: { 0: 0.01, 1: 0.01 },
      merchant_risk: { categories: ['retail', 'crypto'], fraud_rates: [0.02, 0.05] },
    },
  },
  divergence_summary: {
    amount_ks_statistic: { 'bank_a-bank_b': 0.12 },
    overall_non_iid_score: 0.78,
  },
};

const mockSecurityStatus = {
  mtls: {
    enabled: true,
    cert_valid: true,
    algorithm: 'ECDSA-P256-SHA256',
    expiry_days_remaining: 88,
    active_peers: 3,
  },
  oidc: {
    enabled: true,
    provider: 'Keycloak OIDC',
    session_valid: true,
    token_expiry_minutes: 45,
    authenticated_user: 'compliance_officer_01',
    user_roles: ['aml_investigator', 'model_auditor'],
  },
  vault: {
    seal_status: 'UNSEALED',
    initialized: true,
    active_keys: 12,
    last_rotated: '2026-08-14T00:00:00Z',
    rotation_interval_days: 30,
  },
  abac: {
    total_rules: 8,
    active_rules: 8,
    default_action: 'DENY',
    last_evaluated: '2026-08-14T10:00:00Z',
  },
  sgx: {
    enclave_active: true,
    mrenclave: 'a1b2c3d4e5f6',
    mrsigner: 'f6e5d4c3b2a1',
    isvprodid: 1,
    isvsvn: 1,
    attestation_status: 'VALID',
  },
};

const mockDriftData = {
  overall_status: 'HEALTHY',
  max_psi: 0.042,
  concept_drift_psi: 0.035,
  feature_drifts: [
    {
      feature_name: 'amount',
      ks_statistic: 0.03,
      ks_pvalue: 0.85,
      wasserstein_distance: 0.02,
      psi_score: 0.042,
      status: 'NO_DRIFT',
      baseline_mean: 1200.5,
      current_mean: 1210.2,
      drift_magnitude: 0.01,
    },
  ],
  evaluated_at: '2026-08-14T12:00:00Z',
};

const mockDefenseMethods = [
  {
    id: 'dp_sgd',
    name: 'Differential Privacy (DP-SGD)',
    category: 'Gradient Obfuscation',
    protection_level: 'High',
    description: 'Adds calibrated Gaussian noise to clipped gradients.',
    default_config: { epsilon: 4.0, delta: 1e-5, clipping_norm: 1.0 },
    colluding_defense: true,
  },
];

const mockDashboardStats = {
  active_banks: 3,
  total_alerts: 42,
  critical_alerts: 5,
  open_cases: 7,
  privacy_budget_used_pct: 12.5,
  avg_inference_latency_ms: 4.8,
  system_auc_roc: 0.945,
};

const mockCaseDetail = {
  id: 'CASE-DEEP-01',
  title: 'Deep Link Investigation Case',
  status: 'investigating',
  priority: 'high',
  assigned_to: 'analyst_yusuf',
  alert_ids: ['ALT-001'],
  evidence_ids: [],
  notes: [
    {
      id: 'note_1',
      case_id: 'CASE-DEEP-01',
      author: 'supervisor_alice',
      content: 'High velocity observed across bank_alpha and bank_beta.',
      created_at: '2026-08-14T08:00:00Z',
    },
  ],
  timeline: [
    {
      id: 'evt_1',
      case_id: 'CASE-DEEP-01',
      event_type: 'created',
      description: 'Case auto-generated by ML ensemble alert',
      created_by: 'system',
      created_at: '2026-08-14T07:30:00Z',
    },
  ],
  created_at: '2026-08-14T07:30:00Z',
  updated_at: '2026-08-14T08:00:00Z',
  closed_at: null,
  total_risk_score: 840,
  duration_hours: 4.5,
  is_open: true,
};

const renderAppWithRoute = (initialRoute: string) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialRoute]}>
        <Routes>
          {/* Public Landing Page */}
          <Route path="/" element={<LandingPage />} />

          {/* Platform Routes with Layout */}
          <Route element={<Layout />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/operations" element={<LiveOperationsView />} />
            <Route path="/operations/:id" element={<LiveOperationsView />} />
            <Route path="/simulation/:id" element={<LiveOperationsView />} />
            <Route path="/benchmarks" element={<BenchmarkHubPage />} />
            <Route path="/investigation" element={<InvestigationDashboard />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/cases" element={<CasesPage />} />
            <Route path="/cases/:caseId" element={<CaseDetailPage />} />
            <Route path="/rules" element={<PoliciesPage />} />
            <Route path="/policies" element={<PoliciesPage />} />
            <Route path="/psi" element={<PsiPage />} />
            <Route path="/security" element={<SecurityPage />} />
            <Route path="/observability" element={<ObservabilityPage />} />
            <Route path="/scenarios" element={<ScenariosPage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/onboarding" element={<BankOnboardingPage />} />
            <Route path="/coordinator" element={<CoordinatorPage />} />
            <Route path="/privacy-defense" element={<PrivacyDefensePage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('Routing, Deep Linking & Parameter Resolution Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'get').mockImplementation(async (url) => {
      if (url.includes('/evidence')) {
        return { data: [] };
      }
      if (url.includes('/api/v1/cases/CASE-DEEP-01')) {
        return { data: mockCaseDetail };
      }
      if (url.includes('/api/v1/banks/distributions')) {
        return { data: mockBankDistributions };
      }
      if (url.includes('/api/v1/banks')) {
        return { data: mockBanks };
      }
      if (url.includes('/api/v1/intelligence/stats')) {
        return { data: mockDashboardStats };
      }
      if (url.includes('/api/v1/alerts')) {
        return { data: [] };
      }
      if (url.includes('/api/v1/cases')) {
        return { data: [] };
      }
      if (url.includes('/api/v1/rules')) {
        return { data: [] };
      }
      if (url.includes('/api/v1/security/status') || url.includes('/api/v1/security')) {
        return { data: mockSecurityStatus };
      }
      if (url.includes('/api/v1/monitoring/drift') || url.includes('/api/v1/observability/drift')) {
        return { data: mockDriftData };
      }
      if (url.includes('/api/v1/privacy-defense/methods') || url.includes('/api/v1/privacy-defense')) {
        return { data: mockDefenseMethods };
      }
      if (url.includes('/api/v1/simulations')) {
        return { data: [] };
      }
      if (url.includes('/api/v1/graph/stats')) {
        return { data: { total_nodes: 120, total_edges: 340, suspicious_subgraphs: 4 } };
      }
      return { data: {} };
    });
  });

  // ── 1. Public Landing Page Routing ──────────────────────────────────────────
  describe('1. Public Route Resolution', () => {
    it('renders the Enterprise Landing Page on root path "/"', async () => {
      renderAppWithRoute('/');
      expect(screen.getAllByText(/CF-Intelligence/i).length).toBeGreaterThan(0);
      expect(screen.getByRole('button', { name: /Launch Live Platform Demo/i })).toBeInTheDocument();
    });
  });

  // ── 2. Deep Linking to Core Functional Views ─────────────────────────────────
  describe('2. Deep Linking to Core Views', () => {
    it('deep links directly to Dashboard ("/dashboard")', async () => {
      renderAppWithRoute('/dashboard');
      await waitFor(() => {
        expect(screen.getAllByText(/Collaborative Fraud Intelligence/i).length).toBeGreaterThan(0);
        expect(screen.getByText(/Participating Institutions/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Benchmarks ("/benchmarks")', async () => {
      renderAppWithRoute('/benchmarks');
      await waitFor(() => {
        expect(screen.getByText(/Real-World Benchmarks & Design Partner Hub/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Case Management ("/cases")', async () => {
      renderAppWithRoute('/cases');
      await waitFor(() => {
        expect(screen.getByText(/Case Management/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Rule Policies ("/policies" and alias "/rules")', async () => {
      const { unmount } = renderAppWithRoute('/policies');
      await waitFor(() => {
        expect(screen.getByText(/Policy Rules & Decisions/i) || screen.getByText(/Dynamic Rule Tester/i)).toBeInTheDocument();
      });
      unmount();

      renderAppWithRoute('/rules');
      await waitFor(() => {
        expect(screen.getByText(/Policy Rules & Decisions/i) || screen.getByText(/Dynamic Rule Tester/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Security & ABAC Controls ("/security")', async () => {
      renderAppWithRoute('/security');
      await waitFor(() => {
        expect(screen.getByText(/Enterprise Security & Identity Control Suite/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Observability & Drift Monitoring ("/observability")', async () => {
      renderAppWithRoute('/observability');
      await waitFor(() => {
        expect(screen.getByText(/Enterprise Observability & Drift Monitoring/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Private Set Intersection ("/psi")', async () => {
      renderAppWithRoute('/psi');
      await waitFor(() => {
        expect(screen.getByText(/Private Set Intersection \(PSI\)/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Bank Consortium Onboarding ("/onboarding")', async () => {
      renderAppWithRoute('/onboarding');
      await waitFor(() => {
        expect(screen.getByText(/Bank Node Onboarding Wizard/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Coordinator Dynamic Scaling ("/coordinator")', async () => {
      renderAppWithRoute('/coordinator');
      await waitFor(() => {
        expect(screen.getByText(/Federated Coordinator Suite/i)).toBeInTheDocument();
      });
    });

    it('deep links directly to Privacy Defense Audit ("/privacy-defense")', async () => {
      renderAppWithRoute('/privacy-defense');
      await waitFor(() => {
        expect(screen.getByText(/Privacy Defense & Byzantine Suite/i)).toBeInTheDocument();
      });
    });
  });

  // ── 3. Route Parameters Resolution ──────────────────────────────────────────
  describe('3. Dynamic Route Parameters Resolution', () => {
    it('extracts :caseId parameter from URL and loads corresponding case detail view ("/cases/CASE-DEEP-01")', async () => {
      renderAppWithRoute('/cases/CASE-DEEP-01');

      await waitFor(() => {
        expect(screen.getByText('Deep Link Investigation Case')).toBeInTheDocument();
        expect(screen.getByText(/CASE-DEE/i)).toBeInTheDocument();
      });
    });
  });

  // ── 4. Query Parameters Handling & Synchronization ──────────────────────────
  describe('4. Query Parameters Handling & URL State Sync', () => {
    const QueryParamInspector = () => {
      const [searchParams, setSearchParams] = useSearchParams();
      const tab = searchParams.get('tab') || 'default';
      const filter = searchParams.get('filter') || 'all';

      return (
        <div data-testid="param-inspector">
          <span data-testid="current-tab">{tab}</span>
          <span data-testid="current-filter">{filter}</span>
          <button
            onClick={() => setSearchParams({ tab: 'non_iid', filter: 'critical' })}
            data-testid="update-params-btn"
          >
            Update Params
          </button>
        </div>
      );
    };

    it('parses URL query parameters and updates search params interactively', async () => {
      render(
        <MemoryRouter initialEntries={['/benchmarks?tab=byzantine&filter=f1_score']}>
          <QueryParamInspector />
        </MemoryRouter>
      );

      expect(screen.getByTestId('current-tab').textContent).toBe('byzantine');
      expect(screen.getByTestId('current-filter').textContent).toBe('f1_score');

      fireEvent.click(screen.getByTestId('update-params-btn'));

      expect(screen.getByTestId('current-tab').textContent).toBe('non_iid');
      expect(screen.getByTestId('current-filter').textContent).toBe('critical');
    });
  });

  // ── 5. History Navigation & Active Route Transitions ────────────────────────
  describe('5. History Navigation, Back/Forward & Layout Transitions', () => {
    const NavigationTester = () => {
      const navigate = useNavigate();
      const location = useLocation();

      return (
        <div>
          <span data-testid="active-pathname">{location.pathname}</span>
          <button onClick={() => navigate('/cases')} data-testid="nav-cases-btn">
            Go To Cases
          </button>
          <button onClick={() => navigate('/alerts')} data-testid="nav-alerts-btn">
            Go To Alerts
          </button>
          <button onClick={() => navigate(-1)} data-testid="nav-back-btn">
            Go Back
          </button>
          <button onClick={() => navigate(1)} data-testid="nav-forward-btn">
            Go Forward
          </button>
        </div>
      );
    };

    it('handles forward, backward, and programmatic route navigation deterministically', async () => {
      render(
        <MemoryRouter initialEntries={['/dashboard']}>
          <NavigationTester />
        </MemoryRouter>
      );

      expect(screen.getByTestId('active-pathname').textContent).toBe('/dashboard');

      // Navigate to /cases
      fireEvent.click(screen.getByTestId('nav-cases-btn'));
      expect(screen.getByTestId('active-pathname').textContent).toBe('/cases');

      // Navigate to /alerts
      fireEvent.click(screen.getByTestId('nav-alerts-btn'));
      expect(screen.getByTestId('active-pathname').textContent).toBe('/alerts');

      // Go Back to /cases
      fireEvent.click(screen.getByTestId('nav-back-btn'));
      expect(screen.getByTestId('active-pathname').textContent).toBe('/cases');

      // Go Back to /dashboard
      fireEvent.click(screen.getByTestId('nav-back-btn'));
      expect(screen.getByTestId('active-pathname').textContent).toBe('/dashboard');

      // Go Forward to /cases
      fireEvent.click(screen.getByTestId('nav-forward-btn'));
      expect(screen.getByTestId('active-pathname').textContent).toBe('/cases');
    });
  });
});
