import React, { useEffect, useState } from 'react';
import { Navbar } from './components/Navbar';
import { GraphVisualizer } from './components/GraphVisualizer';
import { CounterfactualWorkbench } from './components/CounterfactualWorkbench';
import { FLRoundRunner } from './components/FLRoundRunner';
import { Predictor } from './components/Predictor';
import { DriftAnalytics } from './components/DriftAnalytics';
import { SystemHealthStatus } from './types';
import { checkSystemHealth } from './services/api';

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
        {activeTab === 'graph' && <GraphVisualizer selectedBank={selectedBank} />}
        {activeTab === 'counterfactual' && <CounterfactualWorkbench />}
        {activeTab === 'fl_runner' && <FLRoundRunner />}
        {activeTab === 'predict' && <Predictor />}
        {activeTab === 'drift' && <DriftAnalytics />}
      </main>

      <footer className="glass-card border-t border-slate-800/80 py-4 px-6 mt-12 text-center text-xs text-slate-500">
        Cross-Bank Privacy-Preserving Federated Fraud Intelligence Platform &copy; 2026. Built with React, Cytoscape.js, Differential Privacy & FedGNN.
      </footer>
    </div>
  );
}

export default App;
