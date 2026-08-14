import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/layout/Layout';
import LandingPage from './pages/LandingPage';
import Dashboard from './pages/Dashboard';

// Lazy-loaded secondary platform modules for lightning fast initial load
const LiveOperationsView = lazy(() => import('./pages/LiveOperationsView'));
const BankOnboardingPage = lazy(() => import('./pages/BankOnboardingPage'));
const AlertsPage = lazy(() => import('./pages/AlertsPage'));
const CasesPage = lazy(() => import('./pages/CasesPage'));
const CaseDetailPage = lazy(() => import('./pages/CaseDetailPage'));
const ScenariosPage = lazy(() => import('./pages/ScenariosPage'));
const GraphPage = lazy(() => import('./pages/GraphPage'));
const InvestigationDashboard = lazy(() => import('./pages/InvestigationDashboard'));
const PoliciesPage = lazy(() => import('./pages/PoliciesPage'));
const PsiPage = lazy(() => import('./pages/PsiPage'));
const SecurityPage = lazy(() => import('./pages/SecurityPage'));
const ObservabilityPage = lazy(() => import('./pages/ObservabilityPage'));
const CoordinatorPage = lazy(() => import('./pages/CoordinatorPage'));
const PrivacyDefensePage = lazy(() => import('./pages/PrivacyDefensePage'));
const BenchmarkHubPage = lazy(() => import('./pages/BenchmarkHubPage'));

const PageFallback = () => (
  <div className="flex min-h-[60vh] items-center justify-center">
    <div className="flex flex-col items-center gap-3">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
      <span className="text-xs font-mono text-slate-400">Loading module...</span>
    </div>
  </div>
);

// Ensure QueryClient is always available even if main.tsx wrapping is lost during builds
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Enterprise SaaS Landing Page */}
          <Route path="/" element={<LandingPage />} />

          <Route element={<Layout />}>
            {/* Live Operations & FL Consortium */}
            <Route path="/dashboard" element={<Dashboard />} />
            <Route
              path="/operations"
              element={
                <Suspense fallback={<PageFallback />}>
                  <LiveOperationsView />
                </Suspense>
              }
            />
            <Route
              path="/operations/:id"
              element={
                <Suspense fallback={<PageFallback />}>
                  <LiveOperationsView />
                </Suspense>
              }
            />
            <Route
              path="/simulation/:id"
              element={
                <Suspense fallback={<PageFallback />}>
                  <LiveOperationsView />
                </Suspense>
              }
            />

            {/* Scientific Validation & Benchmarks */}
            <Route
              path="/benchmarks"
              element={
                <Suspense fallback={<PageFallback />}>
                  <BenchmarkHubPage />
                </Suspense>
              }
            />

            {/* Phase 2: AML Intelligence Platform */}
            <Route
              path="/investigation"
              element={
                <Suspense fallback={<PageFallback />}>
                  <InvestigationDashboard />
                </Suspense>
              }
            />
            <Route
              path="/alerts"
              element={
                <Suspense fallback={<PageFallback />}>
                  <AlertsPage />
                </Suspense>
              }
            />
            <Route
              path="/cases"
              element={
                <Suspense fallback={<PageFallback />}>
                  <CasesPage />
                </Suspense>
              }
            />
            <Route
              path="/cases/:caseId"
              element={
                <Suspense fallback={<PageFallback />}>
                  <CaseDetailPage />
                </Suspense>
              }
            />
            <Route
              path="/rules"
              element={
                <Suspense fallback={<PageFallback />}>
                  <PoliciesPage />
                </Suspense>
              }
            />
            <Route
              path="/psi"
              element={
                <Suspense fallback={<PageFallback />}>
                  <PsiPage />
                </Suspense>
              }
            />
            <Route
              path="/security"
              element={
                <Suspense fallback={<PageFallback />}>
                  <SecurityPage />
                </Suspense>
              }
            />
            <Route
              path="/observability"
              element={
                <Suspense fallback={<PageFallback />}>
                  <ObservabilityPage />
                </Suspense>
              }
            />
            <Route
              path="/scenarios"
              element={
                <Suspense fallback={<PageFallback />}>
                  <ScenariosPage />
                </Suspense>
              }
            />
            <Route
              path="/graph"
              element={
                <Suspense fallback={<PageFallback />}>
                  <GraphPage />
                </Suspense>
              }
            />

            {/* Enterprise Platform & Onboarding */}
            <Route
              path="/onboarding"
              element={
                <Suspense fallback={<PageFallback />}>
                  <BankOnboardingPage />
                </Suspense>
              }
            />
            <Route
              path="/coordinator"
              element={
                <Suspense fallback={<PageFallback />}>
                  <CoordinatorPage />
                </Suspense>
              }
            />
            <Route
              path="/privacy-defense"
              element={
                <Suspense fallback={<PageFallback />}>
                  <PrivacyDefensePage />
                </Suspense>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
