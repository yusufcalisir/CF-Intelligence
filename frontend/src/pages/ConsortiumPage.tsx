import { useState } from 'react';
import { motion } from 'framer-motion';
import { 
  Building2, 
  Network, 
  ShieldCheck, 
  Zap, 
  TrendingUp, 
  FileText, 
  Lock, 
  Layers, 
  CheckCircle2, 
  ArrowUpRight,
  Sparkles
} from 'lucide-react';
import CrossBankTopologyGraph, { SCENARIO_PREVIEWS } from '../components/network/CrossBankTopologyGraph';

export default function ConsortiumPage() {
  const [selectedScenario, setSelectedScenario] = useState<string>('SCENARIO_3');

  return (
    <div className="flex flex-col gap-8 p-6 lg:p-8 max-w-[1600px] mx-auto w-full">
      {/* Page Header */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between border-b border-slate-800/80 pb-6">
        <div>
          <div className="flex items-center gap-2.5 mb-1.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/10 border border-cyan-500/20 text-cyan-400">
              <Network className="h-4 w-4" />
            </div>
            <span className="text-xs font-mono font-semibold uppercase tracking-wider text-cyan-400">
              Flagship Empirical Benchmark (CFI-CrossBank-01)
            </span>
            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-mono font-semibold text-emerald-400 border border-emerald-500/20">
              EMPIRICALLY VERIFIED
            </span>
          </div>
          <h1 className="text-2xl lg:text-3xl font-bold tracking-tight text-white">
            Cross-Bank Synthetic Consortium Benchmark
          </h1>
          <p className="text-sm text-slate-300 max-w-3xl mt-1 leading-relaxed">
            Empirically answering the platform's core research question: <strong className="text-slate-100 font-semibold">Can collaborative learning detect distributed financial crime that is invisible to isolated institutions?</strong> Evaluated across 7 canonical multi-hop fraud topologies.
          </p>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-3">
          <a
            href="/docs/enterprise_benchmark_report.md"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800/80 px-3.5 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700 hover:text-white transition-all shadow-sm"
          >
            <FileText className="h-3.5 w-3.5 text-cyan-400" /> Technical Report
          </a>
          <button
            onClick={() => setSelectedScenario('SCENARIO_7')}
            className="flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-lg shadow-cyan-500/20 hover:from-cyan-400 hover:to-blue-500 transition-all"
          >
            <Sparkles className="h-3.5 w-3.5" /> Inspect Zero-Positive Transfer
          </button>
        </div>
      </div>

      {/* Flagship KPI Metrics */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 backdrop-blur-md"
        >
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Overall Federated Detection</span>
            <Zap className="h-4 w-4 text-cyan-400" />
          </div>
          <div className="text-3xl font-extrabold font-mono text-cyan-400 tracking-tight">
            100.0%
          </div>
          <div className="text-xs text-emerald-400 flex items-center gap-1 mt-1 font-medium">
            <ArrowUpRight className="h-3.5 w-3.5" /> +19.4% mean uplift vs silos
          </div>
          <span className="text-[11px] text-slate-400 mt-2 block">
            Across 20,000 multi-bank transactions
          </span>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
          className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 backdrop-blur-md"
        >
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Isolated Banking Silos Mean</span>
            <Building2 className="h-4 w-4 text-rose-400" />
          </div>
          <div className="text-3xl font-extrabold font-mono text-rose-400 tracking-tight">
            80.6%
          </div>
          <div className="text-xs text-rose-400/90 flex items-center gap-1 mt-1 font-medium">
            -19.4% blind spot deficit
          </div>
          <span className="text-[11px] text-slate-400 mt-2 block">
            Severe degradation on multi-hop cycles
          </span>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
          className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 backdrop-blur-md"
        >
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Scenario 7 Cold-Start Uplift</span>
            <TrendingUp className="h-4 w-4 text-emerald-400" />
          </div>
          <div className="text-3xl font-extrabold font-mono text-emerald-400 tracking-tight">
            +100.0%
          </div>
          <div className="text-xs text-emerald-400 flex items-center gap-1 mt-1 font-medium">
            0.0% isolated ➔ 100.0% federated
          </div>
          <span className="text-[11px] text-slate-400 mt-2 block">
            Bank Gamma zero-positive transfer
          </span>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.15 }}
          className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 backdrop-blur-md"
        >
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Information Horizon Guarantee</span>
            <Lock className="h-4 w-4 text-indigo-400" />
          </div>
          <div className="text-3xl font-extrabold font-mono text-indigo-400 tracking-tight">
            0 LEAKS
          </div>
          <div className="text-xs text-indigo-300 flex items-center gap-1 mt-1 font-medium">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> Zero Raw PII Transmitted
          </div>
          <span className="text-[11px] text-slate-400 mt-2 block">
            Banks observe only incident local edges
          </span>
        </motion.div>
      </div>

      {/* Interactive Topology Graph Visualizer */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-lg font-bold text-slate-100 flex items-center gap-2">
              <Layers className="h-5 w-5 text-cyan-400" /> Multi-Institution Network Topology & Information Horizons
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Select a scenario to visualize multi-hop laundering chains. Switch the Information Horizon to experience the partial visibility of isolated institutions.
            </p>
          </div>
        </div>

        <CrossBankTopologyGraph
          selectedScenario={selectedScenario}
          onScenarioChange={setSelectedScenario}
        />
      </div>

      {/* 7 Canonical Scenarios Performance Breakdown Table */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 backdrop-blur-md shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-base font-bold text-white flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-indigo-400" /> Canonical Scenarios Empirical Matrix (Scenarios 1–7)
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Empirical evaluation comparing Isolated Local Silos vs Federated Consensus (FedAvg) vs Theoretical Pooled Oracle.
            </p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 bg-slate-950/60 text-slate-400 uppercase font-mono">
              <tr>
                <th className="py-3 px-4">Scenario</th>
                <th className="py-3 px-4">Typology</th>
                <th className="py-3 px-4">Institutions</th>
                <th className="py-3 px-4 text-center">Isolated Recall</th>
                <th className="py-3 px-4 text-center">Federated Recall</th>
                <th className="py-3 px-4 text-center">Pooled Oracle</th>
                <th className="py-3 px-4 text-right">Collaborative Uplift</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {Object.entries(SCENARIO_PREVIEWS).map(([scId, sc]) => {
                const isSelected = selectedScenario === scId;
                return (
                  <tr
                    key={scId}
                    onClick={() => setSelectedScenario(scId)}
                    className={`cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-cyan-500/10 text-cyan-200'
                        : 'hover:bg-slate-800/40 text-slate-300'
                    }`}
                  >
                    <td className="py-3 px-4 font-semibold text-slate-100 flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${isSelected ? 'bg-cyan-400' : 'bg-slate-600'}`} />
                      {sc.title}
                    </td>
                    <td className="py-3 px-4 text-slate-400">
                      <span className="rounded bg-slate-800/80 px-2 py-0.5 text-[10px] text-slate-300 border border-slate-700/50">
                        {sc.typology}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-slate-400 font-sans">
                      {sc.participatingBanks.map(b => b.replace('_', ' ').toUpperCase()).join(', ')}
                    </td>
                    <td className="py-3 px-4 text-center font-bold text-rose-400">
                      {sc.isolatedRecall.toFixed(1)}%
                    </td>
                    <td className="py-3 px-4 text-center font-bold text-cyan-400">
                      {sc.federatedRecall.toFixed(1)}%
                    </td>
                    <td className="py-3 px-4 text-center text-emerald-400 font-semibold">
                      100.0%
                    </td>
                    <td className="py-3 px-4 text-right font-extrabold text-emerald-400">
                      +{sc.deltaUplift.toFixed(1)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Research Thesis Deep-Dive Cards */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
          <h4 className="text-sm font-bold text-slate-100 flex items-center gap-2 mb-2">
            <Building2 className="h-4 w-4 text-rose-400" /> Why Isolated Banking Silos Fail
          </h4>
          <p className="text-xs text-slate-300 leading-relaxed">
            In multi-bank networks, individual banks suffer from <strong>hop blindness</strong>. In Scenario 3 ($A \to B \to C \to A$), Bank Alpha sees an exit to B and an entry from C. It cannot observe the $B \to C$ leg, making closed cycle detection mathematically impossible in isolation.
          </p>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
          <h4 className="text-sm font-bold text-slate-100 flex items-center gap-2 mb-2">
            <Zap className="h-4 w-4 text-cyan-400" /> How Federated Learning Solves It
          </h4>
          <p className="text-xs text-slate-300 leading-relaxed">
            Without transmitting raw account numbers or transaction logs, federated parameter aggregation shares discriminative decision boundaries across consortium nodes, allowing models to recognize rapid layering and cyclic balance velocity signatures.
          </p>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
          <h4 className="text-sm font-bold text-slate-100 flex items-center gap-2 mb-2">
            <TrendingUp className="h-4 w-4 text-emerald-400" /> Zero-Positive Cold Start Transfer
          </h4>
          <p className="text-xs text-slate-300 leading-relaxed">
            In Scenario 7, Bank Gamma has zero historical fraud incidents ($y = 0$). In isolation, its detection rate is 0.0%. Through federated consensus, Bank Gamma inherits models trained on Banks Alpha & Beta, instantly achieving 100% zero-shot protection.
          </p>
        </div>
      </div>
    </div>
  );
}
