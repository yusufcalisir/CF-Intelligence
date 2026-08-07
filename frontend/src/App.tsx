import React, { useEffect, useState, lazy, Suspense } from 'react';
import { Navbar } from './components/Navbar';
import { SystemHealthStatus } from './types';
import { checkSystemHealth } from './services/api';

const GraphVisualizer = lazy(() => import('./components/GraphVisualizer').then(m => ({ default: m.GraphVisualizer })));
const CounterfactualWorkbench = lazy(() => import('./components/CounterfactualWorkbench').then(m => ({ default: m.CounterfactualWorkbench })));
const FLRoundRunner = lazy(() => import('./components/FLRoundRunner').then(m => ({ default: m.FLRoundRunner })));
const Predictor = lazy(() => import('./components/Predictor').then(m => ({ default: m.Predictor })));
const DriftAnalytics = lazy(() => import('./components/DriftAnalytics').then(m => ({ default: m.DriftAnalytics })));

export function App() {
  const [activeTab, setActiveTab] = useState('graph');
  const [selectedBank, setSelectedBank] = useState('all');
  const [health, setHealth] = useState<SystemHealthStatus | null>(null);

  useEffect(() => {
    checkSystemHealth().then(setHealth);
    const interval = setInterval(() => {
      checkSystemHealth().then(setHealth);
    }, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="min-h-screen bg-[#0B0F19] text-slate-100 flex flex-col font-sans">
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        selectedBank={selectedBank}
        setSelectedBank={setSelectedBank}
        health={health}
      />

      <main className="flex-1 max-w-7xl w-full mx-auto p-6">
        <Suspense fallback={
          <div className="flex items-center justify-center p-12 text-slate-400">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-400 mr-3"></div>
            <span>Yükleniyor...</span>
          </div>
        }>
          {activeTab === 'graph' && <GraphVisualizer selectedBank={selectedBank} />}
          {activeTab === 'counterfactual' && <CounterfactualWorkbench />}
          {activeTab === 'fl_runner' && <FLRoundRunner />}
          {activeTab === 'predict' && <Predictor />}
          {activeTab === 'drift' && <DriftAnalytics />}
        </Suspense>
      </main>

      <footer className="glass-card border-t border-slate-800/80 py-4 px-6 mt-12 text-center text-xs text-slate-500">
        Cross-Bank Privacy-Preserving Federated Fraud Intelligence Platform &copy; 2026. Built with React, Cytoscape.js, Differential Privacy & FedGNN.
      </footer>
    </div>
  );
}

export default App;
