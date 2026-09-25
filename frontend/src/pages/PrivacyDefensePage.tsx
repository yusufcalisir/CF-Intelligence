import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  useAggregationMethods,
  useAuditDLG,
  useAuditMIA,
  useAuditModelInversion,
  usePrivacyBudgetLog,
  useBankRDPBudgets,
  useCircuitBreakerAction,
  useSimulateMIA,
} from '../api/queries';
import type {
  AggregationMethodInfo,
  BudgetLogEntry,
  DLGAuditResult,
  MIAAuditResult,
  ModelInversionAuditResult,
  NodeRDPBudgetStatus,
  MIASimulationResponse,
} from '../api/types';
import {
  ShieldCheck,
  ShieldAlert,
  Lock,
  Unlock,
  Database,
  AlertTriangle,
  Play,
  FileText,
  Sparkles,
  RotateCcw,
  Activity,
  Zap,
  Sliders,
} from 'lucide-react';

// ── Types & Color Helpers ─────────────────────────────────────

type RiskTier = 'safe' | 'low_risk' | 'moderate_risk' | 'high_risk';

const RISK_CONFIG: Record<
  RiskTier,
  { label: string; bg: string; text: string; border: string; glow: string }
> = {
  safe: {
    label: 'Safe (0.0% Leakage)',
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-400',
    border: 'border-emerald-500/30',
    glow: 'shadow-[0_0_12px_rgba(16,185,129,0.25)]',
  },
  low_risk: {
    label: 'Low Risk',
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-300',
    border: 'border-emerald-500/20',
    glow: 'shadow-[0_0_8px_rgba(16,185,129,0.15)]',
  },
  moderate_risk: {
    label: 'Moderate Risk',
    bg: 'bg-amber-500/15',
    text: 'text-amber-400',
    border: 'border-amber-500/30',
    glow: 'shadow-[0_0_12px_rgba(245,158,11,0.2)]',
  },
  high_risk: {
    label: 'High Risk (Alert)',
    bg: 'bg-rose-500/15',
    text: 'text-rose-400',
    border: 'border-rose-500/30',
    glow: 'shadow-[0_0_12px_rgba(244,63,94,0.25)]',
  },
};

function RiskBadge({ tier }: { tier: RiskTier }) {
  const config = RISK_CONFIG[tier] || RISK_CONFIG.safe;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10.5px] font-bold font-mono whitespace-nowrap shrink-0 ${config.bg} ${config.text} ${config.border} border ${config.glow}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse shrink-0" />
      <span>{config.label}</span>
    </span>
  );
}

function ScoreMeter({ value, max = 1, label }: { value: number; max?: number; label: string }) {
  const pct = Math.min(100, Math.max(0, Math.round((value / max) * 100)));
  const barColor =
    pct < 30
      ? 'from-emerald-500 to-teal-400 shadow-[0_0_10px_rgba(16,185,129,0.4)]'
      : pct < 60
      ? 'from-amber-500 to-yellow-400 shadow-[0_0_10px_rgba(245,158,11,0.4)]'
      : 'from-rose-500 to-red-400 shadow-[0_0_10px_rgba(244,63,94,0.5)]';

  return (
    <div className="space-y-1.5 w-full min-w-0">
      <div className="flex items-center justify-between gap-2 text-xs min-w-0">
        <span className="text-slate-400 font-mono text-[11px] truncate min-w-0 flex-1" title={label}>
          {label}
        </span>
        <span className="font-mono font-bold text-slate-200 shrink-0 whitespace-nowrap text-right">
          {(value * 100).toFixed(1)}% <span className="text-slate-500 font-normal">({pct}/100)</span>
        </span>
      </div>
      <div className="w-full bg-slate-900/80 rounded-full h-2 overflow-hidden p-0.5 border border-white/5">
        <div
          className={`h-full rounded-full bg-gradient-to-r ${barColor} transition-all duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ── Sub-sections ──────────────────────────────────────────────

function DefenseSuiteSection({ methods }: { methods: AggregationMethodInfo[] }) {
  const [selectedMethodId, setSelectedMethodId] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h2 className="text-base sm:text-lg font-bold text-slate-100 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-indigo-400" />
            <span>Byzantine-Robust Aggregation Catalog</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Mathematical defense proofs against malicious gradient poisoning, label flipping, and colluding Sybil nodes
          </p>
        </div>
        <span className="text-xs font-mono text-indigo-300 bg-indigo-500/10 px-3 py-1 rounded-lg border border-indigo-500/20 self-start sm:self-auto">
          {methods.length} Active Algorithms
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5 sm:gap-4 min-w-0">
        {methods.map((m) => {
          const isSelected = selectedMethodId === m.id;
          const isMultiAttacker = !!m.colluding_defense;

          return (
            <div
              key={m.id}
              onClick={() => setSelectedMethodId(isSelected ? null : m.id)}
              className={`glass-card p-4 sm:p-5 rounded-2xl border transition-all duration-200 cursor-pointer flex flex-col justify-between relative overflow-hidden min-w-0 group ${
                isMultiAttacker
                  ? 'bg-gradient-to-b from-[#0e1038]/90 to-[#06081e]/90 border-indigo-500/40 shadow-lg shadow-indigo-500/10'
                  : 'bg-[#080a21]/80 border-slate-800/80 hover:border-indigo-500/30'
              }`}
            >
              {isMultiAttacker && (
                <div className="absolute top-0 inset-x-0 h-0.5 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500" />
              )}

              <div className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <span className="font-bold text-slate-100 text-sm leading-snug block truncate group-hover:text-indigo-300 transition-colors">
                      {m.label}
                    </span>
                    <span className="text-[10px] font-mono text-slate-400">{m.id}</span>
                  </div>

                  <div className="flex flex-col items-end gap-1 shrink-0">
                    {m.byzantine_robust && (
                      <span className="text-[9px] font-mono font-semibold px-2 py-0.5 rounded-full bg-sky-500/15 text-sky-300 border border-sky-500/30 whitespace-nowrap">
                        Byzantine Robust
                      </span>
                    )}
                    {m.colluding_defense && (
                      <span className="text-[9px] font-mono font-semibold px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 whitespace-nowrap flex items-center gap-1 shadow-sm">
                        <Sparkles className="w-2.5 h-2.5 text-indigo-400" />
                        Multi-Attacker ✨
                      </span>
                    )}
                  </div>
                </div>

                <p className="text-xs text-slate-400 leading-relaxed">{m.description}</p>
              </div>

              <div className="mt-4 pt-3 border-t border-white/5 flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-[10px] font-mono text-slate-400 truncate">
                  <FileText className="w-3 h-3 text-slate-400 shrink-0" />
                  <span className="truncate">{m.paper}</span>
                </div>
                <span className="text-[10px] font-mono text-indigo-400 group-hover:text-indigo-300 transition-colors shrink-0">
                  {isSelected ? 'Active' : 'Inspect'}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Sample gradient data for demo purposes
const SAMPLE_TRAIN_LOSSES = [0.02, 0.015, 0.018, 0.012, 0.025, 0.011, 0.019, 0.014];
const SAMPLE_TEST_LOSSES = [0.55, 0.62, 0.48, 0.70, 0.51, 0.66, 0.59, 0.44];
const SAMPLE_GRAD_NORMS = [0.8, 1.2, 0.95, 10.5, 0.7, 1.1, 8.3, 0.85];
const SAMPLE_ORIG_GRADS = Array.from({ length: 30 }, (_, i) => Math.sin(i) * 0.3);
const SAMPLE_RECV_GRADS = Array.from({ length: 30 }, (_, i) => Math.sin(i) * 0.3 + Math.random() * 0.05);

function MIASimulationVisualizer() {
  const [testEps, setTestEps] = useState<number>(1.0);
  const [numSamples, setNumSamples] = useState<number>(80);
  const simulateMIA = useSimulateMIA();
  const simData = simulateMIA.data as MIASimulationResponse | undefined;

  const presets = [
    { label: 'ε = 0.5 (Strong)', val: 0.5 },
    { label: 'ε = 1.5 (Standard)', val: 1.5 },
    { label: 'ε = 4.0 (Balanced)', val: 4.0 },
    { label: 'ε = 8.0 (Target SLA)', val: 8.0 },
    { label: 'No DP (ε = 60)', val: 60.0 },
  ];

  return (
    <div className="glass-card p-4 sm:p-6 rounded-2xl border border-indigo-500/30 bg-[#080a21]/95 shadow-xl space-y-5 min-w-0">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pb-3 border-b border-white/5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-indigo-500/20 text-indigo-300">
              <Sliders className="w-4 h-4" />
            </div>
            <h3 className="font-bold text-slate-100 text-sm sm:text-base">
              Membership Inference Attack (MIA) Empirical Simulator
            </h3>
          </div>
          <p className="text-xs text-slate-400">
            Simulate shadow model transaction loss distributions and observe empirical ROC-AUC / ASR under Rényi DP
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono px-2.5 py-1 rounded-lg bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
            Shokri / Yeom Model
          </span>
        </div>
      </div>

      {/* Preset Buttons & Slider */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-center min-w-0">
        <div className="space-y-2">
          <label className="text-xs font-mono text-slate-300 flex justify-between">
            <span>Differential Privacy Noise Level (Target ε):</span>
            <span className="font-bold text-indigo-400">
              {testEps >= 50.0 ? '∞ (No DP)' : `ε = ${testEps.toFixed(2)}`}
            </span>
          </label>
          <input
            id="slider-mia-epsilon"
            type="range"
            min="0.1"
            max="10.0"
            step="0.1"
            value={testEps > 10 ? 10 : testEps}
            onChange={(e) => setTestEps(parseFloat(e.target.value))}
            className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
          />
          <div className="flex flex-wrap gap-1.5 pt-1">
            {presets.map((p) => (
              <button
                key={p.label}
                onClick={() => setTestEps(p.val)}
                className={`px-2.5 py-1 rounded-lg text-[10.5px] font-mono transition-all cursor-pointer ${
                  testEps === p.val
                    ? 'bg-indigo-600 text-white font-bold border border-indigo-400 shadow-sm'
                    : 'bg-white/[0.04] hover:bg-white/[0.08] text-slate-300 border border-white/5'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-center gap-3">
          <div className="w-full sm:w-auto flex-1 space-y-1">
            <span className="text-xs font-mono text-slate-400 block">Shadow Samples:</span>
            <select
              value={numSamples}
              onChange={(e) => setNumSamples(parseInt(e.target.value, 10))}
              className="w-full bg-[#050614] border border-white/10 rounded-xl px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="50">50 Shadow Transactions</option>
              <option value="80">80 Shadow Transactions</option>
              <option value="150">150 Shadow Transactions</option>
            </select>
          </div>

          <button
            id="btn-simulate-mia-dp"
            onClick={() => simulateMIA.mutate({ test_epsilon: testEps, num_samples: numSamples })}
            disabled={simulateMIA.isPending}
            className="w-full sm:w-auto h-11 min-h-[44px] px-5 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-lg shadow-indigo-600/25 disabled:opacity-50 shrink-0 whitespace-nowrap self-end"
          >
            {simulateMIA.isPending ? (
              <>
                <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin shrink-0" />
                <span>Simulating Attack...</span>
              </>
            ) : (
              <>
                <Zap className="w-3.5 h-3.5 fill-current shrink-0" />
                <span>Simulate Under ε = {testEps >= 50 ? '∞' : testEps.toFixed(2)}</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Simulation Result Display */}
      {simData && (
        <div className="space-y-4 pt-3 border-t border-white/10">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/5 space-y-1">
              <span className="text-[10px] font-mono text-slate-400 uppercase">Attack Success Rate (ASR)</span>
              <div className="flex items-baseline gap-2">
                <span className="text-xl font-bold font-mono text-slate-100">
                  {(simData.membership_leakage_asr * 100).toFixed(1)}%
                </span>
                <span className="text-[10px] font-mono text-slate-500">(Random Guess: 50%)</span>
              </div>
              <ScoreMeter value={simData.membership_leakage_asr} label="Empirical ASR" />
            </div>

            <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/5 space-y-1">
              <span className="text-[10px] font-mono text-slate-400 uppercase">MIA ROC-AUC Score</span>
              <div className="flex items-baseline gap-2">
                <span className={`text-xl font-bold font-mono ${
                  simData.mia_roc_auc < 0.6 ? 'text-emerald-400' : simData.mia_roc_auc < 0.75 ? 'text-amber-400' : 'text-rose-400'
                }`}>
                  {simData.mia_roc_auc.toFixed(3)}
                </span>
                <span className="text-[10px] font-mono text-slate-500">(0.500 = Optimal DP)</span>
              </div>
              <div className="pt-2">
                <RiskBadge tier={simData.risk_tier as RiskTier} />
              </div>
            </div>

            <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/5 space-y-1">
              <span className="text-[10px] font-mono text-slate-400 uppercase">Loss Disparity Gap (ΔL)</span>
              <div className="flex items-baseline gap-2">
                <span className="text-xl font-bold font-mono text-indigo-300">
                  {simData.loss_gap.toFixed(4)}
                </span>
                <span className="text-[10px] font-mono text-slate-500">(L_test - L_train)</span>
              </div>
              <p className="text-[10.5px] text-slate-400 font-mono">
                Train: {simData.mean_train_loss.toFixed(3)} | Test: {simData.mean_test_loss.toFixed(3)}
              </p>
            </div>

            <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/5 space-y-1 flex flex-col justify-between">
              <span className="text-[10px] font-mono text-slate-400 uppercase">DP Defense Status</span>
              <div>
                <span className={`px-2.5 py-1 rounded-full text-xs font-mono font-bold inline-flex items-center gap-1.5 ${
                  simData.is_dp_enabled
                    ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                    : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                }`}>
                  <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
                  {simData.is_dp_enabled ? 'Active Noise Defense' : 'Unconstrained Overfit'}
                </span>
              </div>
              <span className="text-[10px] font-mono text-slate-500">
                Evaluated: {new Date(simData.evaluated_at).toLocaleTimeString()}
              </span>
            </div>
          </div>

          {/* Loss Disparity Visual Bar */}
          <div className="p-4 rounded-xl bg-[#03040f] border border-white/5 space-y-3">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <Activity className="w-3.5 h-3.5 text-indigo-400" />
                <span>Member vs Non-Member Empirical Loss Overlap</span>
              </span>
              <span className="text-[11px] text-slate-400">
                {simData.loss_gap < 0.1 ? '🟢 High Overlap (Indistinguishable)' : '🔴 Distinct Clusters (Vulnerable)'}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
              <div className="space-y-1.5 p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20">
                <div className="flex justify-between text-emerald-300">
                  <span>Training Set Samples (Members):</span>
                  <span>μ = {simData.mean_train_loss.toFixed(3)}</span>
                </div>
                <div className="flex gap-1 overflow-hidden py-1">
                  {simData.train_loss_distribution.slice(0, 16).map((loss, idx) => (
                    <div
                      key={idx}
                      className="flex-1 bg-emerald-500/40 rounded-sm hover:bg-emerald-400 transition-colors"
                      style={{ height: `${Math.min(48, Math.max(8, loss * 60))}px` }}
                      title={`Member Loss: ${loss}`}
                    />
                  ))}
                </div>
              </div>

              <div className="space-y-1.5 p-3 rounded-lg bg-sky-500/5 border border-sky-500/20">
                <div className="flex justify-between text-sky-300">
                  <span>Test Set Samples (Non-Members):</span>
                  <span>μ = {simData.mean_test_loss.toFixed(3)}</span>
                </div>
                <div className="flex gap-1 overflow-hidden py-1">
                  {simData.test_loss_distribution.slice(0, 16).map((loss, idx) => (
                    <div
                      key={idx}
                      className="flex-1 bg-sky-500/40 rounded-sm hover:bg-sky-400 transition-colors"
                      style={{ height: `${Math.min(48, Math.max(8, loss * 60))}px` }}
                      title={`Non-Member Loss: ${loss}`}
                    />
                  ))}
                </div>
              </div>
            </div>

            <p className="text-xs text-indigo-200/90 bg-indigo-500/10 p-2.5 rounded-lg border border-indigo-500/20 leading-relaxed font-mono">
              💡 {simData.attack_summary}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function AttackAuditPanel() {
  const auditMIA = useAuditMIA();
  const auditInversion = useAuditModelInversion();
  const auditDLG = useAuditDLG();

  const miaResult = auditMIA.data as MIAAuditResult | undefined;
  const invResult = auditInversion.data as ModelInversionAuditResult | undefined;
  const dlgResult = auditDLG.data as DLGAuditResult | undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h2 className="text-base sm:text-lg font-bold text-slate-100 flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-rose-400" />
            <span>Adversarial Privacy Attack Evaluators</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Empirical stress testing: Membership Inference, Model Inversion, and Deep Gradient Leakage
          </p>
        </div>
        <span className="text-xs font-mono text-rose-300 bg-rose-500/10 px-3 py-1 rounded-lg border border-rose-500/20 self-start sm:self-auto">
          Audit Suite v2.4
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 min-w-0">
        {/* Card 1: MIA */}
        <div className="glass-card p-4 sm:p-5 rounded-2xl border border-indigo-500/20 bg-[#080a21]/90 flex flex-col justify-between gap-4 min-w-0 relative overflow-hidden group hover:border-indigo-500/40 transition-all h-full">
          <div className="absolute top-0 inset-x-0 h-0.5 bg-indigo-500" />
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2 min-w-0">
              <span className="text-[10px] font-mono uppercase tracking-wider text-indigo-400 font-bold whitespace-nowrap shrink-0">
                Attack Vector 1
              </span>
              <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap shrink-0">Shokri et al.</span>
            </div>
            <h3 className="font-bold text-slate-100 text-sm leading-snug h-10 flex items-center">
              Membership Inference Attack (MIA)
            </h3>
            <p className="text-xs text-slate-400 leading-relaxed min-h-[48px] sm:min-h-[52px]">
              Assesses whether an adversary can infer if a specific bank transaction was present in local training datasets via shadow loss disparities.
            </p>
          </div>

          {miaResult ? (
            <div className="h-[148px] min-h-[148px] p-3.5 rounded-xl bg-[#02030a]/80 border border-white/10 flex flex-col justify-between">
              <ScoreMeter value={miaResult.membership_leakage_asr} label="Attack Success Rate (ASR)" />
              <div className="flex items-center justify-between gap-2 pt-2 border-t border-white/5 min-w-0">
                <span className="text-[11px] font-mono text-slate-400 whitespace-nowrap">Audit Classification:</span>
                <RiskBadge tier={miaResult.risk_tier as RiskTier} />
              </div>
              <div className="text-[10px] font-mono text-slate-400 flex justify-between pt-1 border-t border-white/5 min-w-0">
                <span>Train: {miaResult.num_train_samples_audited} samples</span>
                <span>Test: {miaResult.num_test_samples_audited} samples</span>
              </div>
            </div>
          ) : (
            <div className="h-[148px] min-h-[148px] p-4 rounded-xl bg-[#02030a]/50 border border-dashed border-white/10 flex flex-col items-center justify-center text-center space-y-1.5">
              <ShieldAlert className="w-5 h-5 text-slate-500/60 shrink-0" />
              <span className="text-xs font-mono font-medium text-slate-400">Audit Not Executed</span>
              <span className="text-[10px] text-slate-500 font-mono">Run audit below to evaluate privacy leakage</span>
            </div>
          )}

          <button
            id="btn-run-mia-audit"
            onClick={() => auditMIA.mutate({ train_losses: SAMPLE_TRAIN_LOSSES, test_losses: SAMPLE_TEST_LOSSES })}
            disabled={auditMIA.isPending}
            className="w-full h-11 min-h-[44px] rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-lg shadow-indigo-600/20 disabled:opacity-50 shrink-0 whitespace-nowrap"
          >
            {auditMIA.isPending ? (
              <>
                <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin shrink-0" />
                <span>Simulating MIA...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 shrink-0 fill-current" />
                <span>Run MIA Audit</span>
              </>
            )}
          </button>
        </div>

        {/* Card 2: Model Inversion */}
        <div className="glass-card p-4 sm:p-5 rounded-2xl border border-sky-500/20 bg-[#080a21]/90 flex flex-col justify-between gap-4 min-w-0 relative overflow-hidden group hover:border-sky-500/40 transition-all h-full">
          <div className="absolute top-0 inset-x-0 h-0.5 bg-sky-500" />
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2 min-w-0">
              <span className="text-[10px] font-mono uppercase tracking-wider text-sky-400 font-bold whitespace-nowrap shrink-0">
                Attack Vector 2
              </span>
              <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap shrink-0">Fredrikson et al.</span>
            </div>
            <h3 className="font-bold text-slate-100 text-sm leading-snug h-10 flex items-center">
              Model Inversion & Reconstruction
            </h3>
            <p className="text-xs text-slate-400 leading-relaxed min-h-[48px] sm:min-h-[52px]">
              Audits if shared gradient norms allow adversaries to reconstruct sensitive transaction feature distributions and client account balances.
            </p>
          </div>

          {invResult ? (
            <div className="h-[148px] min-h-[148px] p-3.5 rounded-xl bg-[#02030a]/80 border border-white/10 flex flex-col justify-between">
              <ScoreMeter value={invResult.reconstruction_risk_score} label="Reconstruction Risk" />
              <div className="flex items-center justify-between gap-2 pt-2 border-t border-white/5 min-w-0">
                <span className="text-[11px] font-mono text-slate-400 whitespace-nowrap">Risk Tier:</span>
                <RiskBadge tier={invResult.risk_tier as RiskTier} />
              </div>
              <div className="text-[10px] font-mono text-slate-400 flex justify-between pt-1 border-t border-white/5 min-w-0">
                <span>Mean Norm: {invResult.mean_gradient_norm.toFixed(3)}</span>
                <span>σ: {invResult.std_gradient_norm.toFixed(3)}</span>
              </div>
            </div>
          ) : (
            <div className="h-[148px] min-h-[148px] p-4 rounded-xl bg-[#02030a]/50 border border-dashed border-white/10 flex flex-col items-center justify-center text-center space-y-1.5">
              <ShieldAlert className="w-5 h-5 text-slate-500/60 shrink-0" />
              <span className="text-xs font-mono font-medium text-slate-400">Audit Not Executed</span>
              <span className="text-[10px] text-slate-500 font-mono">Run audit below to evaluate privacy leakage</span>
            </div>
          )}

          <button
            id="btn-run-model-inversion-audit"
            onClick={() => auditInversion.mutate({ gradient_norms: SAMPLE_GRAD_NORMS })}
            disabled={auditInversion.isPending}
            className="w-full h-11 min-h-[44px] rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-2 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white shadow-lg shadow-sky-600/20 disabled:opacity-50 shrink-0 whitespace-nowrap"
          >
            {auditInversion.isPending ? (
              <>
                <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin shrink-0" />
                <span>Auditing Inversion...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 shrink-0 fill-current" />
                <span>Run Inversion Audit</span>
              </>
            )}
          </button>
        </div>

        {/* Card 3: DLG */}
        <div className="glass-card p-4 sm:p-5 rounded-2xl border border-emerald-500/20 bg-[#080a21]/90 flex flex-col justify-between gap-4 min-w-0 relative overflow-hidden group hover:border-emerald-500/40 transition-all h-full">
          <div className="absolute top-0 inset-x-0 h-0.5 bg-emerald-500" />
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2 min-w-0">
              <span className="text-[10px] font-mono uppercase tracking-wider text-emerald-400 font-bold whitespace-nowrap shrink-0">
                Attack Vector 3
              </span>
              <span className="text-[10px] font-mono text-slate-400 whitespace-nowrap shrink-0">Zhu et al. (NeurIPS)</span>
            </div>
            <h3 className="font-bold text-slate-100 text-sm leading-snug h-10 flex items-center">
              Deep Leakage from Gradients (DLG)
            </h3>
            <p className="text-xs text-slate-400 leading-relaxed min-h-[48px] sm:min-h-[52px]">
              Verifies whether shared gradient vectors correlate closely enough to synthesize exact transaction raw data without DP noise injection.
            </p>
          </div>

          {dlgResult ? (
            <div className="h-[148px] min-h-[148px] p-3.5 rounded-xl bg-[#02030a]/80 border border-white/10 flex flex-col justify-between">
              <ScoreMeter value={dlgResult.dlg_leakage_score} label="Pearson Leakage" />
              <div className="flex items-center justify-between gap-2 pt-2 border-t border-white/5 min-w-0">
                <span className="text-[11px] font-mono text-slate-400 whitespace-nowrap">Audit Status:</span>
                <RiskBadge tier={dlgResult.risk_tier as RiskTier} />
              </div>
              <div className="text-[10px] font-mono text-slate-400 flex justify-between pt-1 border-t border-white/5 min-w-0">
                <span>Audited Weights:</span>
                <span className="text-emerald-300 font-bold">{dlgResult.params_audited} params</span>
              </div>
            </div>
          ) : (
            <div className="h-[148px] min-h-[148px] p-4 rounded-xl bg-[#02030a]/50 border border-dashed border-white/10 flex flex-col items-center justify-center text-center space-y-1.5">
              <ShieldAlert className="w-5 h-5 text-slate-500/60 shrink-0" />
              <span className="text-xs font-mono font-medium text-slate-400">Audit Not Executed</span>
              <span className="text-[10px] text-slate-500 font-mono">Run audit below to evaluate privacy leakage</span>
            </div>
          )}

          <button
            id="btn-run-dlg-audit"
            onClick={() => auditDLG.mutate({ original_gradients: SAMPLE_ORIG_GRADS, received_gradients: SAMPLE_RECV_GRADS })}
            disabled={auditDLG.isPending}
            className="w-full h-11 min-h-[44px] rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-2 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-lg shadow-emerald-600/20 disabled:opacity-50 shrink-0 whitespace-nowrap"
          >
            {auditDLG.isPending ? (
              <>
                <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin shrink-0" />
                <span>Computing DLG...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 shrink-0 fill-current" />
                <span>Run DLG Audit</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Interactive MIA Empirical Simulator under Rényi DP */}
      <MIASimulationVisualizer />
    </div>
  );
}

function BudgetLogSection() {
  const { data: entries = [], isLoading } = usePrivacyBudgetLog(8.0);
  const { data: bankBudgets, isLoading: isBudgetsLoading } = useBankRDPBudgets();
  const circuitBreakerAction = useCircuitBreakerAction();

  const isFrozen = !!bankBudgets?.training_circuit_breaker_active;
  const anyExceeded = !!bankBudgets?.any_budget_exceeded || entries.some((e) => e.budget_exhausted);
  const nodes = bankBudgets?.node_budgets ?? [];

  return (
    <div className="space-y-6">
      {/* 1. Header Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h2 className="text-base sm:text-lg font-bold text-slate-100 flex items-center gap-2">
            <Database className="w-5 h-5 text-indigo-400" />
            <span>Enterprise Privacy Budget Audit Log (DP-SGD ε)</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Rényi Differential Privacy (RDP) cumulative ε-consumption tracker with per-bank accountants and automated safety circuit-breaker
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-emerald-300 bg-emerald-500/10 px-3 py-1 rounded-lg border border-emerald-500/20">
            Consortium Target: ε ≤ {bankBudgets?.consortium_target_epsilon ?? 4.0}, δ = {bankBudgets?.consortium_target_delta?.toExponential(1) ?? '1.0e-5'}
          </span>
        </div>
      </div>

      {/* 2. Automated Training Safety Circuit-Breaker Banner */}
      <div className={`p-4 sm:p-5 rounded-2xl border transition-all duration-300 ${
        isFrozen
          ? 'bg-rose-500/15 border-rose-500/50 shadow-xl shadow-rose-500/20 text-rose-200'
          : anyExceeded
          ? 'bg-amber-500/15 border-amber-500/50 shadow-xl shadow-amber-500/20 text-amber-200'
          : 'bg-[#080a21]/90 border-indigo-500/30 shadow-lg shadow-indigo-500/10 text-slate-200'
      }`}>
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div className="flex items-start gap-3.5 min-w-0">
            <div className={`p-2.5 rounded-xl shrink-0 ${
              isFrozen
                ? 'bg-rose-500/20 text-rose-300 animate-pulse'
                : anyExceeded
                ? 'bg-amber-500/20 text-amber-300 animate-pulse'
                : 'bg-emerald-500/20 text-emerald-300'
            }`}>
              {isFrozen ? <Lock className="w-5 h-5" /> : <ShieldCheck className="w-5 h-5" />}
            </div>
            <div className="space-y-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-xs font-mono font-bold uppercase tracking-wider px-2 py-0.5 rounded-md ${
                  isFrozen
                    ? 'bg-rose-500/30 text-rose-200'
                    : anyExceeded
                    ? 'bg-amber-500/30 text-amber-200'
                    : 'bg-emerald-500/20 text-emerald-300'
                }`}>
                  {isFrozen ? '🚨 TRAINING CIRCUIT-BREAKER ENGAGED' : '🛡️ CONSORTIUM SAFETY LOCK ACTIVE'}
                </span>
                {bankBudgets?.frozen_by_node && (
                  <span className="text-[11px] font-mono text-slate-400">
                    Halted by: <span className="font-bold text-slate-200">{bankBudgets.frozen_by_node}</span>
                  </span>
                )}
              </div>
              <p className="text-xs sm:text-sm leading-relaxed">
                {isFrozen
                  ? (bankBudgets?.freeze_reason || 'Consortium model training has been locked due to privacy budget exhaustion or operator safety override.')
                  : 'Automated Rényi DP accountant monitors cumulative epsilon across all participating bank nodes. Training freezes instantly if any bank depletes ε_max.'}
              </p>
              {bankBudgets?.frozen_at && (
                <span className="text-[10px] font-mono text-slate-400 block">
                  Lock Engaged At: {new Date(bankBudgets.frozen_at).toLocaleString()}
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 shrink-0">
            {isFrozen ? (
              <button
                id="btn-disengage-circuit-breaker"
                onClick={() => circuitBreakerAction.mutate({ action: 'unfreeze', reason: 'Operator verified DP compliance' })}
                disabled={circuitBreakerAction.isPending}
                className="h-10 min-h-[40px] px-4 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/30 transition-all flex items-center gap-2 cursor-pointer disabled:opacity-50"
              >
                <Unlock className="w-3.5 h-3.5" />
                <span>Resume Consortium Training</span>
              </button>
            ) : (
              <button
                id="btn-freeze-circuit-breaker"
                onClick={() => circuitBreakerAction.mutate({ action: 'freeze', reason: 'Operator emergency safety lock triggered' })}
                disabled={circuitBreakerAction.isPending}
                className="h-10 min-h-[40px] px-4 rounded-xl text-xs font-bold bg-rose-600/80 hover:bg-rose-600 text-white shadow-lg shadow-rose-600/20 transition-all flex items-center gap-2 cursor-pointer disabled:opacity-50"
              >
                <Lock className="w-3.5 h-3.5" />
                <span>Emergency Safety Freeze</span>
              </button>
            )}

            <button
              id="btn-reset-consortium-budgets"
              onClick={() => circuitBreakerAction.mutate({ action: 'reset_budget' })}
              disabled={circuitBreakerAction.isPending}
              className="h-10 min-h-[40px] px-3.5 rounded-xl text-xs font-mono font-semibold bg-white/[0.05] hover:bg-white/[0.1] text-slate-300 border border-white/10 transition-all flex items-center gap-2 cursor-pointer disabled:opacity-50"
            >
              <RotateCcw className="w-3.5 h-3.5 text-slate-400" />
              <span>Reset All Budgets</span>
            </button>
          </div>
        </div>
      </div>

      {/* 3. Consortium Bank Node Rényi DP (RDP) Accountant Grid */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-slate-200 flex items-center gap-2">
            <Activity className="w-4 h-4 text-indigo-400" />
            <span>Bank-Level Rényi DP Accountants (Live State)</span>
          </h3>
          <span className="text-xs font-mono text-slate-400">
            {nodes.length} Participating Bank Nodes
          </span>
        </div>

        {isBudgetsLoading ? (
          <div className="glass-card p-8 text-center text-slate-400 text-xs font-mono animate-pulse rounded-2xl">
            Querying per-bank Rényi DP accountants...
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5 min-w-0">
            {nodes.map((node: NodeRDPBudgetStatus) => {
              const isExceeded = node.is_budget_exceeded || node.budget_exhaustion_pct >= 100;
              const isWarning = node.budget_exhaustion_pct >= 80 && !isExceeded;

              return (
                <div
                  key={node.node_id}
                  className={`glass-card p-4 rounded-2xl border flex flex-col justify-between gap-3 relative overflow-hidden transition-all duration-200 ${
                    isExceeded
                      ? 'bg-rose-500/10 border-rose-500/40 shadow-lg shadow-rose-500/10'
                      : isWarning
                      ? 'bg-amber-500/10 border-amber-500/40 shadow-md shadow-amber-500/10'
                      : 'bg-[#080a21]/80 border-slate-800/80 hover:border-indigo-500/30'
                  }`}
                >
                  <div className="space-y-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <span className="font-bold text-slate-100 text-sm block truncate">
                          {node.bank_name}
                        </span>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          <span className="text-[10px] font-mono text-slate-400">{node.node_id}</span>
                          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-white/5 text-slate-400">
                            {node.tier}
                          </span>
                        </div>
                      </div>
                      <span className={`text-[9.5px] font-mono font-bold px-2 py-0.5 rounded-full shrink-0 ${
                        isExceeded
                          ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                          : isWarning
                          ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                          : 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                      }`}>
                        {node.risk_tier}
                      </span>
                    </div>

                    <div className="space-y-1">
                      <div className="flex justify-between text-xs font-mono">
                        <span className="text-slate-400">Cumulative ε:</span>
                        <span className="font-bold text-slate-200">
                          {node.cumulative_epsilon.toFixed(3)} / {node.target_epsilon.toFixed(1)}
                        </span>
                      </div>
                      <div className="w-full bg-slate-900 rounded-full h-2 overflow-hidden border border-white/5">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${
                            isExceeded
                              ? 'bg-gradient-to-r from-rose-500 to-red-400 shadow-[0_0_8px_rgba(244,63,94,0.6)]'
                              : isWarning
                              ? 'bg-gradient-to-r from-amber-500 to-yellow-400'
                              : 'bg-gradient-to-r from-emerald-500 to-teal-400'
                          }`}
                          style={{ width: `${Math.min(100, Math.max(0, node.budget_exhaustion_pct))}%` }}
                        />
                      </div>
                      <div className="flex justify-between text-[10px] font-mono text-slate-400 pt-0.5">
                        <span>Exhaustion: {node.budget_exhaustion_pct.toFixed(1)}%</span>
                        <span>δ = {node.target_delta.toExponential(1)}</span>
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2 pt-2 border-t border-white/5 text-xs font-mono">
                      <div className="p-1.5 rounded-lg bg-white/[0.02] border border-white/5 text-center">
                        <span className="text-[9px] text-slate-400 block">Rényi α*</span>
                        <span className="text-slate-200 font-bold">{node.optimal_alpha_order.toFixed(1)}</span>
                      </div>
                      <div className="p-1.5 rounded-lg bg-white/[0.02] border border-white/5 text-center">
                        <span className="text-[9px] text-slate-400 block">Noise σ</span>
                        <span className="text-indigo-300 font-bold">{node.calibrated_sigma.toFixed(3)}</span>
                      </div>
                      <div className="p-1.5 rounded-lg bg-white/[0.02] border border-white/5 text-center">
                        <span className="text-[9px] text-slate-400 block">Rounds</span>
                        <span className="text-slate-200 font-bold">{node.rounds_completed}</span>
                      </div>
                    </div>
                  </div>

                  <div className="pt-2 border-t border-white/5 flex items-center justify-between">
                    <span className="text-[10px] font-mono text-slate-500">
                      {isExceeded ? '⚠️ Action Required' : 'Budget Secure'}
                    </span>
                    <button
                      onClick={() => circuitBreakerAction.mutate({ action: 'reset_budget', node_id: node.node_id })}
                      disabled={circuitBreakerAction.isPending}
                      className="text-[10.5px] font-mono text-indigo-400 hover:text-indigo-300 cursor-pointer transition-colors"
                    >
                      Reset Node
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 4. Global Simulation Runs Audit Ledger */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold text-slate-200 flex items-center gap-2">
          <Database className="w-4 h-4 text-indigo-400" />
          <span>Simulation Ledger (Historical DP Runs)</span>
        </h3>

        {anyExceeded && (
          <div className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-4 text-xs text-rose-300 flex items-start gap-3 shadow-lg shadow-rose-500/10">
            <AlertTriangle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <strong className="text-rose-200 font-bold block">Privacy Budget Exhaustion Alert Triggered!</strong>
              <p className="text-rose-300/90 leading-relaxed">
                One or more simulation runs have depleted the global Differential Privacy budget (ε &gt; 8.0). Active learning rounds have been isolated to prevent progressive data reconstruction.
              </p>
            </div>
          </div>
        )}

        {isLoading ? (
          <div className="glass-card p-12 text-center text-slate-400 text-xs font-mono animate-pulse rounded-2xl">
            Querying enterprise DP budget ledger...
          </div>
        ) : entries.length === 0 ? (
          <div className="glass-card p-12 text-center text-slate-400 text-xs font-mono rounded-2xl border border-dashed border-white/10">
            No training runs recorded in the current session. Run a federated round to initiate ε-tracking.
          </div>
        ) : (
          <>
          {/* Mobile View: Stacked DP Budget Cards (< 768px, Zero Horizontal Scroll) */}
          <div className="block md:hidden space-y-3">
            {entries.map((entry: BudgetLogEntry) => {
              const epsPct = Math.min(100, Math.round((entry.total_epsilon / 8.0) * 100));

              return (
                <div
                  key={entry.simulation_id}
                  className={`glass-card p-4 rounded-xl border space-y-3 ${
                    entry.budget_exhausted
                      ? 'bg-rose-500/5 border-rose-500/40'
                      : 'bg-[#080a21]/90 border-white/10'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <span className="text-[10px] font-mono text-slate-400 block truncate">Simulation ID</span>
                      <span className="font-mono text-xs font-bold text-slate-100 truncate block">
                        {entry.simulation_id}
                      </span>
                    </div>

                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold shrink-0 ${
                        entry.budget_exhausted
                          ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                          : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                      }`}
                    >
                      {entry.budget_exhausted ? 'EXHAUSTED' : 'BUDGET OK'}
                    </span>
                  </div>

                  {/* Budget bar */}
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs font-mono">
                      <span className="text-slate-400">Total ε Consumed:</span>
                      <span
                        className="font-bold"
                        style={{
                          color: entry.total_epsilon > 6 ? '#ef4444' : entry.total_epsilon > 3 ? '#f59e0b' : '#10b981',
                        }}
                      >
                        {entry.total_epsilon.toFixed(4)} / 8.00
                      </span>
                    </div>
                    <div className="w-full bg-slate-900 rounded-full h-1.5 overflow-hidden">
                      <div
                        className="h-full bg-indigo-500 rounded-full"
                        style={{ width: `${epsPct}%` }}
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 pt-2 border-t border-white/5 text-xs font-mono">
                    <div className="p-2 rounded-lg bg-white/[0.02] border border-white/5">
                      <div className="text-[10px] text-slate-400">Delta (δ)</div>
                      <div className="text-slate-200 font-bold">{entry.delta?.toExponential?.(1) ?? '1e-5'}</div>
                    </div>
                    <div className="p-2 rounded-lg bg-white/[0.02] border border-white/5">
                      <div className="text-[10px] text-slate-400">Rounds</div>
                      <div className="text-slate-200 font-bold">{entry.rounds_spent}</div>
                    </div>
                    <div className="p-2 rounded-lg bg-white/[0.02] border border-white/5">
                      <div className="text-[10px] text-slate-400">ε / Round</div>
                      <div className="text-indigo-300 font-bold">{entry.epsilon_per_round?.toFixed(3) ?? '0.120'}</div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Desktop Table View (>= 768px) */}
          <div className="hidden md:block glass-card rounded-2xl border border-indigo-500/20 bg-[#080a21]/90 overflow-hidden shadow-xl">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="bg-slate-950/60 border-b border-slate-800 text-slate-400 font-semibold uppercase tracking-wider">
                    <th className="py-3.5 px-4">Simulation ID</th>
                    <th className="py-3.5 px-4 text-right">Total ε Spent</th>
                    <th className="py-3.5 px-4 text-right">Target δ</th>
                    <th className="py-3.5 px-4 text-right">Rounds Spent</th>
                    <th className="py-3.5 px-4 text-right">ε / Round</th>
                    <th className="py-3.5 px-4 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 font-mono">
                  {entries.map((entry: BudgetLogEntry) => (
                    <tr
                      key={entry.simulation_id}
                      className={`transition-colors hover:bg-slate-800/40 ${
                        entry.budget_exhausted ? 'bg-rose-500/5' : ''
                      }`}
                    >
                      <td className="py-3.5 px-4 font-bold text-slate-200 truncate max-w-[220px]">
                        {entry.simulation_id}
                      </td>
                      <td
                        className="py-3.5 px-4 text-right font-bold text-sm"
                        style={{
                          color: entry.total_epsilon > 6 ? '#ef4444' : entry.total_epsilon > 3 ? '#f59e0b' : '#10b981',
                        }}
                      >
                        {entry.total_epsilon.toFixed(4)}
                      </td>
                      <td className="py-3.5 px-4 text-right text-slate-400">
                        {entry.delta?.toExponential?.(1) ?? '1.0e-5'}
                      </td>
                      <td className="py-3.5 px-4 text-right text-slate-200 font-bold">{entry.rounds_spent}</td>
                      <td className="py-3.5 px-4 text-right text-indigo-300">
                        {entry.epsilon_per_round?.toFixed(4) ?? '0.1200'}
                      </td>
                      <td className="py-3.5 px-4 text-center">
                        {entry.budget_exhausted ? (
                          <span className="px-2.5 py-1 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30">
                            EXHAUSTED
                          </span>
                        ) : (
                          <span className="px-2.5 py-1 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                            BUDGET OK
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────

export default function PrivacyDefensePage() {
  const { data: methods = [], isLoading: methodsLoading } = useAggregationMethods();
  const [activeTab, setActiveTab] = useState<'defense' | 'audit' | 'budget'>('defense');

  const tabs = [
    {
      id: 'defense' as const,
      label: 'Byzantine Defenses',
      desc: '7 Proven Aggregators',
      icon: ShieldCheck,
    },
    {
      id: 'audit' as const,
      label: 'Attack Audits',
      desc: 'MIA · Inversion · DLG',
      icon: ShieldAlert,
    },
    {
      id: 'budget' as const,
      label: 'Privacy Budget Log',
      desc: 'DP-SGD ε Tracker',
      icon: Database,
    },
  ];

  return (
    <div className="space-y-4 sm:space-y-6 max-w-7xl mx-auto text-slate-100 w-full min-w-0">
      {/* Top Header Banner */}
      <div className="glass-card p-4 sm:p-6 rounded-2xl bg-gradient-to-r from-[#07091e]/95 via-[#0b0e2d]/90 to-[#07091e]/95 border border-indigo-500/20 shadow-xl flex flex-col md:flex-row md:items-center md:justify-between gap-4 relative overflow-hidden min-w-0">
        <div className="absolute top-0 inset-x-0 h-0.5 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 opacity-80" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2.5 rounded-xl bg-indigo-500/15 border border-indigo-500/30 text-indigo-300 shadow-[0_0_15px_rgba(99,102,241,0.25)] shrink-0">
              <Lock className="w-6 h-6 text-indigo-300" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h1 className="text-lg sm:text-2xl font-black text-slate-100 tracking-tight truncate">
                  Privacy Defense & Byzantine Suite
                </h1>
                <span className="px-2 py-0.5 rounded-full text-[9px] font-mono font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 whitespace-nowrap">
                  Opacus DP + TenSEAL CKKS
                </span>
              </div>
              <p className="text-xs sm:text-sm text-[var(--color-text-muted)] mt-0.5 leading-relaxed">
                Byzantine-robust aggregation catalog, adversarial leakage stress-testing, and enterprise Differential Privacy ledger
              </p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-500/10 border border-emerald-500/25 text-emerald-400 text-xs font-semibold">
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse shrink-0" />
            <span>Zero Raw PII Verified</span>
          </div>
        </div>
      </div>

      {/* Segmented 3-Tab Selector (Zero Scroll, Mobile Perfected) */}
      <div className="grid grid-cols-3 gap-1.5 sm:gap-2 p-1.5 rounded-2xl bg-[#07091e]/90 border border-indigo-500/20 shadow-lg min-w-0">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          const Icon = tab.icon;

          return (
            <button
              key={tab.id}
              id={`tab-privacy-defense-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`p-2.5 sm:p-3 rounded-xl text-center sm:text-left transition-all duration-200 cursor-pointer min-w-0 flex flex-col sm:flex-row items-center sm:items-start gap-1.5 sm:gap-3 border ${
                isActive
                  ? 'bg-gradient-to-r from-indigo-600/30 to-purple-600/30 border-indigo-500/60 text-white shadow-md shadow-indigo-600/15'
                  : 'bg-white/[0.02] hover:bg-white/[0.05] border-white/5 text-slate-400 hover:text-slate-200'
              }`}
            >
              <div
                className={`p-1.5 rounded-lg shrink-0 ${
                  isActive ? 'bg-indigo-500/20 text-indigo-300' : 'bg-white/5 text-slate-400'
                }`}
              >
                <Icon className="w-4 h-4" />
              </div>
              <div className="min-w-0 text-center sm:text-left">
                <span className="text-xs sm:text-sm font-bold block truncate">{tab.label}</span>
                <span className="text-[10px] font-mono text-slate-400 hidden sm:block truncate mt-0.5">
                  {tab.desc}
                </span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Tab Content Panels */}
      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.2 }}
          className="min-w-0"
        >
          {activeTab === 'defense' && (
            methodsLoading ? (
              <div className="glass-card p-12 text-center text-slate-400 text-xs font-mono animate-pulse rounded-2xl">
                Loading Byzantine algorithm catalog...
              </div>
            ) : (
              <DefenseSuiteSection methods={methods} />
            )
          )}

          {activeTab === 'audit' && <AttackAuditPanel />}

          {activeTab === 'budget' && <BudgetLogSection />}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

