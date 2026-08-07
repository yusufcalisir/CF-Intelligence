import React from 'react';
import { Activity, ShieldAlert, Cpu, Network, Sliders, Server, Building2 } from 'lucide-react';
import { SystemHealthStatus } from '../types';

interface NavbarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  selectedBank: string;
  setSelectedBank: (bank: string) => void;
  health: SystemHealthStatus | null;
}

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  setActiveTab,
  selectedBank,
  setSelectedBank,
  health,
}) => {
  const tabs = [
    { id: 'graph', label: 'Graph Fraud Visualizer', icon: Network },
    { id: 'counterfactual', label: 'Counterfactual Workbench', icon: Sliders },
    { id: 'fl_runner', label: 'Live FL Round Runner', icon: Cpu },
    { id: 'predict', label: 'Real-Time Prediction', icon: Activity },
    { id: 'drift', label: 'Model Drift & XAI', icon: ShieldAlert },
  ];

  const banks = [
    { id: 'all', name: 'Consortium Hub (Global)' },
    { id: 'bank_a', name: 'Bank A (JPMorgan Chase)' },
    { id: 'bank_b', name: 'Bank B (Bank of America)' },
    { id: 'bank_c', name: 'Bank C (Wells Fargo)' },
  ];

  const isOnline = health && health.status === 'healthy';

  return (
    <header className="sticky top-0 z-50 glass-card border-b border-slate-800/80 px-6 py-3">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
        {/* Brand Logo & Title */}
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-purple-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
            <Server className="h-6 w-6 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-bold text-lg tracking-tight bg-gradient-to-r from-cyan-400 via-sky-300 to-purple-400 bg-clip-text text-transparent">
                Cross-Bank FL Fraud Intelligence
              </h1>
              <span className="text-[10px] uppercase tracking-widest px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-semibold">
                v1.4.2
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Privacy-Preserving Federated Graph Neural Networks & Differential Privacy
            </p>
          </div>
        </div>

        {/* Tenant Selector & System Health Badge */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 bg-slate-900/80 border border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-300">
            <Building2 className="h-4 w-4 text-cyan-400" />
            <select
              value={selectedBank}
              onChange={(e) => setSelectedBank(e.target.value)}
              className="bg-transparent text-slate-200 outline-none cursor-pointer"
            >
              {banks.map((b) => (
                <option key={b.id} value={b.id} className="bg-slate-900 text-slate-200">
                  {b.name}
                </option>
              ))}
            </select>
          </div>

          <div
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs border ${
              isOnline
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                : 'bg-amber-500/10 text-amber-400 border-amber-500/20'
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                isOnline ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
              }`}
            />
            <span className="font-mono font-medium uppercase">
              {isOnline ? 'Backend Online' : 'Local Standalone'}
            </span>
          </div>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="max-w-7xl mx-auto flex items-center gap-2 mt-4 pt-3 border-t border-slate-800/60 overflow-x-auto">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium transition-all duration-200 whitespace-nowrap ${
                isActive
                  ? 'bg-gradient-to-r from-cyan-500/20 to-purple-500/20 text-cyan-300 border border-cyan-500/30 shadow-lg shadow-cyan-500/10'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              }`}
            >
              <Icon className={`h-4 w-4 ${isActive ? 'text-cyan-400' : 'text-slate-400'}`} />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>
    </header>
  );
};
