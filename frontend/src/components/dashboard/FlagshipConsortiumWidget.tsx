import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Link } from 'react-router-dom';

interface ScenarioSummary {
  id: string;
  name: string;
  isolated: number;
  federated: number;
  delta: number;
  topology: string;
}

const FLAGSHIP_SCENARIOS: ScenarioSummary[] = [
  {
    id: 'SCENARIO_1',
    name: 'Single-Bank Localized Fraud',
    isolated: 100.0,
    federated: 100.0,
    delta: 0.0,
    topology: 'Internal Structuring within Bank Alpha',
  },
  {
    id: 'SCENARIO_2',
    name: 'Two-Bank Layering Chain',
    isolated: 100.0,
    federated: 100.0,
    delta: 0.0,
    topology: 'Cross-Border Layering Alpha -> Beta',
  },
  {
    id: 'SCENARIO_3',
    name: 'Three-Bank Cyclic Laundering Ring',
    isolated: 66.7,
    federated: 100.0,
    delta: 33.3,
    topology: 'Cyclic Transfer (A -> B -> C -> A)',
  },
  {
    id: 'SCENARIO_4',
    name: 'Multi-Bank Smurfing to Cash-Out',
    isolated: 100.0,
    federated: 100.0,
    delta: 0.0,
    topology: 'Distributed Smurfing (A+B -> C Cashout)',
  },
  {
    id: 'SCENARIO_5',
    name: 'Non-IID Institutional Archetypes',
    isolated: 100.0,
    federated: 100.0,
    delta: 0.0,
    topology: 'Heterogeneous Volume & Class Imbalance',
  },
  {
    id: 'SCENARIO_6',
    name: 'Extreme Positive Rarity at Gamma',
    isolated: 100.0,
    federated: 100.0,
    delta: 0.0,
    topology: 'Rarity Regime at Regional Settlement Node',
  },
  {
    id: 'SCENARIO_7',
    name: 'Zero-Positive Cold-Start Transfer',
    isolated: 0.0,
    federated: 100.0,
    delta: 100.0,
    topology: 'New Onboarding Institution Rescue',
  },
];

export default function FlagshipConsortiumWidget() {
  const [selectedScenario, setSelectedScenario] = useState<string>('SCENARIO_3');
  const activeScenario: ScenarioSummary =
    FLAGSHIP_SCENARIOS.find((s) => s.id === selectedScenario) ?? (FLAGSHIP_SCENARIOS[2] as ScenarioSummary);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.55 }}
      className="p-6 rounded-2xl bg-[#090a1f]/85 border border-indigo-500/20 backdrop-blur-xl shadow-2xl space-y-6 mt-6 relative overflow-hidden"
    >
      {/* Background Ambient Glow */}
      <div className="absolute -top-24 -right-24 w-80 h-80 bg-indigo-600/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-24 -left-24 w-80 h-80 bg-emerald-600/10 rounded-full blur-3xl pointer-events-none" />

      {/* Header & Meta */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-white/8 pb-4">
        <div>
          <div className="flex items-center gap-2 mb-1.5 flex-wrap">
            <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold tracking-wider uppercase bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
              Flagship Empirical Benchmark
            </span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">
              CFI-CrossBank-01
            </span>
            <span className="text-[10px] font-mono text-slate-400 bg-white/5 px-2 py-0.5 rounded-full border border-white/5">
              3 Institutions · 7 Scenarios
            </span>
          </div>
          <h2 className="text-base sm:text-lg font-bold text-white tracking-tight flex items-center gap-2">
            <span>🏛️</span>
            <span>Multi-Bank Consortium Collaborative Advantage</span>
          </h2>
          <p className="text-xs text-slate-400 mt-1 max-w-3xl">
            Empirical validation of collaborative intelligence vs. isolated banking silos: detecting cross-institutional money laundering topologies invisible to single-bank visibility horizons.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Link
            to="/consortium"
            className="px-3.5 py-2 text-xs font-semibold rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white transition-all shadow-lg shadow-indigo-600/20 flex items-center gap-1.5 active:scale-95"
          >
            <span>Consortium Console</span>
            <span>→</span>
          </Link>
          <Link
            to="/scenarios"
            className="px-3 py-2 text-xs font-medium rounded-xl bg-white/5 hover:bg-white/10 text-slate-300 border border-white/10 transition-all flex items-center gap-1"
          >
            <span>Topologies</span>
          </Link>
        </div>
      </div>

      {/* Core Impact Highlights Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
        <div className="p-4 rounded-xl bg-slate-900/60 border border-white/5 hover:border-indigo-500/30 transition-all space-y-1">
          <div className="text-[10px] font-mono uppercase text-slate-400 font-semibold tracking-wider">
            Overall Detection Uplift
          </div>
          <div className="text-2xl font-black font-mono text-emerald-400 flex items-baseline gap-1.5">
            +19.05%
            <span className="text-xs font-normal text-slate-400">Δ over silos</span>
          </div>
          <div className="text-[11px] text-slate-400 font-mono">
            80.95% Isolated → <span className="text-emerald-300 font-bold">100.0% Federated</span>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-slate-900/60 border border-white/5 hover:border-teal-500/30 transition-all space-y-1">
          <div className="text-[10px] font-mono uppercase text-slate-400 font-semibold tracking-wider">
            Averted Fraud Volume (VaR)
          </div>
          <div className="text-2xl font-black font-mono text-teal-400 flex items-baseline gap-1.5">
            +$790,933
            <span className="text-xs font-normal text-slate-400">USD</span>
          </div>
          <div className="text-[11px] text-slate-400 font-mono">
            <span className="text-teal-300 font-bold">+42.14%</span> incremental capital preserved
          </div>
        </div>

        <div className="p-4 rounded-xl bg-slate-900/60 border border-white/5 hover:border-violet-500/30 transition-all space-y-1">
          <div className="text-[10px] font-mono uppercase text-slate-400 font-semibold tracking-wider">
            Cyclic Laundering Ring (SC-3)
          </div>
          <div className="text-2xl font-black font-mono text-violet-400 flex items-baseline gap-1.5">
            100.0%
            <span className="text-xs font-normal text-slate-400">vs 66.7%</span>
          </div>
          <div className="text-[11px] text-slate-400 font-mono">
            Cross-bank horizon reveals closed ring
          </div>
        </div>

        <div className="p-4 rounded-xl bg-slate-900/60 border border-white/5 hover:border-amber-500/30 transition-all space-y-1">
          <div className="text-[10px] font-mono uppercase text-slate-400 font-semibold tracking-wider">
            Cold-Start Bank Rescue (SC-7)
          </div>
          <div className="text-2xl font-black font-mono text-amber-400 flex items-baseline gap-1.5">
            0% → 100%
            <span className="text-xs font-normal text-slate-400">Instant</span>
          </div>
          <div className="text-[11px] text-slate-400 font-mono">
            Bank Gamma zero-positive prior transfer
          </div>
        </div>
      </div>

      {/* Interactive Scenario Inspection Matrix */}
      <div className="p-4 rounded-xl bg-slate-900/40 border border-white/5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
            <span>🔬</span>
            <span>Scenario-by-Scenario Horizon Analysis</span>
          </h3>
          <span className="text-[10px] font-mono text-slate-500">
            Click scenario to inspect partial observation vulnerability
          </span>
        </div>

        {/* Scenario Pill Selectors */}
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-thin scrollbar-thumb-white/10">
          {FLAGSHIP_SCENARIOS.map((sc) => {
            const isSelected = sc.id === selectedScenario;
            return (
              <button
                key={sc.id}
                onClick={() => setSelectedScenario(sc.id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-all shrink-0 flex items-center gap-1.5 ${
                  isSelected
                    ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                    : 'bg-white/5 hover:bg-white/10 text-slate-300 border border-white/5'
                }`}
              >
                <span>{sc.id.replace('_', ' ')}</span>
                {sc.delta > 0 && (
                  <span
                    className={`text-[9px] px-1 py-0.2 rounded font-bold ${
                      isSelected ? 'bg-white/20 text-white' : 'bg-emerald-500/20 text-emerald-400'
                    }`}
                  >
                    +{sc.delta.toFixed(0)}%
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Selected Scenario Detail Card */}
        <AnimatePresence mode="wait">
          <motion.div
            key={activeScenario.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            className="p-4 rounded-xl bg-slate-950/70 border border-white/8 space-y-3"
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-white/6 pb-2.5">
              <div>
                <span className="text-[10px] font-mono font-bold text-indigo-400 uppercase tracking-wider">
                  {activeScenario.id}
                </span>
                <h4 className="text-sm font-bold text-slate-100">{activeScenario.name}</h4>
              </div>
              <span className="text-[11px] font-mono text-slate-400 bg-white/5 px-2.5 py-1 rounded-md border border-white/5 self-start sm:self-auto">
                Topology: {activeScenario.topology}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div className="p-3 rounded-lg bg-white/3 border border-white/5 space-y-1">
                <span className="text-[10px] font-mono uppercase text-slate-400">Isolated Silo Detection</span>
                <div className="text-lg font-bold font-mono text-rose-400">
                  {activeScenario.isolated.toFixed(1)}%
                </div>
                <p className="text-[10px] text-slate-500">
                  {activeScenario.isolated < 100
                    ? 'Blind to non-local transaction hops beyond internal ledger.'
                    : 'Sufficient local feature footprint for detection.'}
                </p>
              </div>

              <div className="p-3 rounded-lg bg-white/3 border border-white/5 space-y-1">
                <span className="text-[10px] font-mono uppercase text-slate-400">Federated Consensus</span>
                <div className="text-lg font-bold font-mono text-emerald-400">
                  {activeScenario.federated.toFixed(1)}%
                </div>
                <p className="text-[10px] text-slate-500">
                  Consortium gradient sharing aggregates multi-hop graph topology without PII.
                </p>
              </div>

              <div className="p-3 rounded-lg bg-white/3 border border-white/5 space-y-1">
                <span className="text-[10px] font-mono uppercase text-slate-400">Consortium Uplift (Δ)</span>
                <div className="text-lg font-bold font-mono text-indigo-300">
                  +{activeScenario.delta.toFixed(1)}%
                </div>
                <p className="text-[10px] text-slate-500">
                  {activeScenario.delta > 0
                    ? 'Critical collaborative advantage preventing cross-border leakage.'
                    : 'Parity maintained with zero performance regression.'}
                </p>
              </div>
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Participating Institutions Status */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
        <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-900/40 border border-white/5">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/20 text-indigo-300 flex items-center justify-center font-bold text-xs shrink-0">
            A
          </div>
          <div className="min-w-0">
            <div className="text-xs font-bold text-slate-200 truncate">Bank Alpha (Retail)</div>
            <div className="text-[10px] font-mono text-slate-400 truncate">50% Consortium Vol · 5k Accts</div>
          </div>
        </div>

        <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-900/40 border border-white/5">
          <div className="w-8 h-8 rounded-lg bg-teal-500/20 text-teal-300 flex items-center justify-center font-bold text-xs shrink-0">
            B
          </div>
          <div className="min-w-0">
            <div className="text-xs font-bold text-slate-200 truncate">Bank Beta (Commercial)</div>
            <div className="text-[10px] font-mono text-slate-400 truncate">30% Consortium Vol · 3k Accts</div>
          </div>
        </div>

        <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-900/40 border border-white/5">
          <div className="w-8 h-8 rounded-lg bg-amber-500/20 text-amber-300 flex items-center justify-center font-bold text-xs shrink-0">
            C
          </div>
          <div className="min-w-0">
            <div className="text-xs font-bold text-slate-200 truncate">Bank Gamma (Cross-Border)</div>
            <div className="text-[10px] font-mono text-emerald-400 truncate">Cold-Start Transfer Rescued</div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
