import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/layout/Layout';
import Dashboard from './pages/Dashboard';
import LiveOperationsView from './pages/LiveOperationsView';
import BankOnboardingPage from './pages/BankOnboardingPage';
import AlertsPage from './pages/AlertsPage';
import CasesPage from './pages/CasesPage';
import CaseDetailPage from './pages/CaseDetailPage';
import ScenariosPage from './pages/ScenariosPage';
import GraphPage from './pages/GraphPage';
import InvestigationDashboard from './pages/InvestigationDashboard';
import PoliciesPage from './pages/PoliciesPage';
import PsiPage from './pages/PsiPage';
import SecurityPage from './pages/SecurityPage';
import ObservabilityPage from './pages/ObservabilityPage';
import CoordinatorPage from './pages/CoordinatorPage';
import PrivacyDefensePage from './pages/PrivacyDefensePage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          {/* Live Operations & FL Consortium */}
          <Route path="/" element={<Dashboard />} />
          <Route path="/operations" element={<LiveOperationsView />} />
          <Route path="/operations/:id" element={<LiveOperationsView />} />
          <Route path="/simulation/:id" element={<LiveOperationsView />} />

          {/* Phase 2: AML Intelligence Platform */}
          <Route path="/investigation" element={<InvestigationDashboard />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/cases" element={<CasesPage />} />
          <Route path="/cases/:caseId" element={<CaseDetailPage />} />
          <Route path="/rules" element={<PoliciesPage />} />
          <Route path="/psi" element={<PsiPage />} />
          <Route path="/security" element={<SecurityPage />} />
          <Route path="/observability" element={<ObservabilityPage />} />
          <Route path="/scenarios" element={<ScenariosPage />} />
          <Route path="/graph" element={<GraphPage />} />

          {/* Enterprise Platform & Onboarding */}
          <Route path="/onboarding" element={<BankOnboardingPage />} />
          <Route path="/coordinator" element={<CoordinatorPage />} />
          <Route path="/privacy-defense" element={<PrivacyDefensePage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
