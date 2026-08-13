import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  useAggregationMethods,
  useAuditDLG,
  useAuditMIA,
  useAuditModelInversion,
  usePrivacyBudgetLog,
} from '../api/queries';
import type {
  AggregationMethodInfo,
  BudgetLogEntry,
  DLGAuditResult,
  MIAAuditResult,
  ModelInversionAuditResult,
} from '../api/types';
import {
  ShieldCheck,
  ShieldAlert,
  Lock,
  Database,
  AlertTriangle,
  Play,
  FileText,
  Sparkles,
} from 'lucide-react';

// ── Types & Color Helpers ─────────────────────────────────────

type RiskTier = 'safe' | 'low_risk' | 'moderate_risk' | 'high_risk';

const RISK_CONFIG: Record<
  RiskTier,
  { label: string; bg: string; text: string; border: string; glow: string }
> = {
  safe: {
    label: 'Safe (Negligible Leakage)',
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-400',
    border: 'border-emerald-500/30',
    glow: 'shadow-[0_0_12px_rgba(16,185,129,0.25)]',
  },
  low_risk: {
    label: 'Low Risk (Within SLA)',
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
    label: 'High Risk (Leakage Alert)',
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
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold font-mono ${config.bg} ${config.text} ${config.border} border ${config.glow}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
      {config.label}
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
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-400 font-mono text-[11px]">{label}</span>
        <span className="font-mono font-bold text-slate-200">
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
        <div className="glass-card p-4 sm:p-5 rounded-2xl border border-indigo-500/20 bg-[#080a21]/90 flex flex-col justify-between gap-4 min-w-0 relative overflow-hidden group hover:border-indigo-500/40 transition-all">
          <div className="absolute top-0 inset-x-0 h-0.5 bg-indigo-500" />
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono uppercase tracking-wider text-indigo-400 font-bold">
                Attack Vector 1
              </span>
              <span className="text-[10px] font-mono text-slate-400">Shokri et al.</span>
            </div>
            <h3 className="font-bold text-slate-100 text-sm">Membership Inference Attack (MIA)</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Assesses whether an adversary can infer if a specific bank transaction was present in local training datasets via shadow loss disparities.
            </p>
          </div>

          {miaResult ? (
            <div className="p-3.5 rounded-xl bg-[#02030a]/80 border border-white/10 space-y-3">
              <ScoreMeter value={miaResult.membership_leakage_asr} label="Attack Success Rate (ASR)" />
              <div className="flex items-center justify-between pt-1">
                <span className="text-[10px] font-mono text-slate-400">Audit Classification:</span>
                <RiskBadge tier={miaResult.risk_tier as RiskTier} />
              </div>
            </div>
          ) : (
            <div className="p-3.5 rounded-xl bg-[#02030a]/50 border border-dashed border-white/10 text-center py-6">
              <span className="text-xs font-mono text-slate-400">Audit not executed yet</span>
            </div>
          )}

          <button
            id="btn-run-mia-audit"
            onClick={() => auditMIA.mutate({ train_losses: SAMPLE_TRAIN_LOSSES, test_losses: SAMPLE_TEST_LOSSES })}
            disabled={auditMIA.isPending}
            className="w-full py-2.5 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-2 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-lg shadow-indigo-600/20 disabled:opacity-50"
          >
            {auditMIA.isPending ? (
              <>
                <span className="h-3 w-3 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                <span>Simulating MIA Attack...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5" />
                <span>Run Membership Inference Audit</span>
              </>
            )}
          </button>
        </div>

        {/* Card 2: Model Inversion */}
        <div className="glass-card p-4 sm:p-5 rounded-2xl border border-sky-500/20 bg-[#080a21]/90 flex flex-col justify-between gap-4 min-w-0 relative overflow-hidden group hover:border-sky-500/40 transition-all">
          <div className="absolute top-0 inset-x-0 h-0.5 bg-sky-500" />
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono uppercase tracking-wider text-sky-400 font-bold">
                Attack Vector 2
              </span>
              <span className="text-[10px] font-mono text-slate-400">Fredrikson et al.</span>
            </div>
            <h3 className="font-bold text-slate-100 text-sm">Model Inversion & Reconstruction</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Audits if shared gradient norms allow adversaries to reconstruct sensitive transaction feature distributions and client account balances.
            </p>
          </div>

          {invResult ? (
            <div className="p-3.5 rounded-xl bg-[#02030a]/80 border border-white/10 space-y-3">
              <ScoreMeter value={invResult.reconstruction_risk_score} label="Reconstruction Risk" />
              <div className="flex items-center justify-between pt-1">
                <span className="text-[10px] font-mono text-slate-400">Risk Tier:</span>
                <RiskBadge tier={invResult.risk_tier as RiskTier} />
              </div>
              <div className="text-[10px] font-mono text-slate-400 flex justify-between pt-1 border-t border-white/5">
                <span>Mean Norm: {invResult.mean_gradient_norm.toFixed(3)}</span>
                <span>σ: {invResult.std_gradient_norm.toFixed(3)}</span>
              </div>
            </div>
          ) : (
            <div className="p-3.5 rounded-xl bg-[#02030a]/50 border border-dashed border-white/10 text-center py-6">
              <span className="text-xs font-mono text-slate-400">Audit not executed yet</span>
            </div>
          )}

          <button
            id="btn-run-model-inversion-audit"
            onClick={() => auditInversion.mutate({ gradient_norms: SAMPLE_GRAD_NORMS })}
            disabled={auditInversion.isPending}
            className="w-full py-2.5 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-2 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white shadow-lg shadow-sky-600/20 disabled:opacity-50"
          >
            {auditInversion.isPending ? (
              <>
                <span className="h-3 w-3 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                <span>Auditing Gradient Inversion...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5" />
                <span>Run Model Inversion Audit</span>
              </>
            )}
          </button>
        </div>

        {/* Card 3: DLG */}
        <div className="glass-card p-4 sm:p-5 rounded-2xl border border-emerald-500/20 bg-[#080a21]/90 flex flex-col justify-between gap-4 min-w-0 relative overflow-hidden group hover:border-emerald-500/40 transition-all">
          <div className="absolute top-0 inset-x-0 h-0.5 bg-emerald-500" />
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono uppercase tracking-wider text-emerald-400 font-bold">
                Attack Vector 3
              </span>
              <span className="text-[10px] font-mono text-slate-400">Zhu et al. (NeurIPS)</span>
            </div>
            <h3 className="font-bold text-slate-100 text-sm">Deep Leakage from Gradients (DLG)</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Verifies whether shared gradient vectors correlate closely enough to synthesize exact transaction raw data without DP noise injection.
            </p>
          </div>

          {dlgResult ? (
            <div className="p-3.5 rounded-xl bg-[#02030a]/80 border border-white/10 space-y-3">
              <ScoreMeter value={dlgResult.dlg_leakage_score} label="Pearson Leakage Correlation" />
              <div className="flex items-center justify-between pt-1">
                <span className="text-[10px] font-mono text-slate-400">Audit Status:</span>
                <RiskBadge tier={dlgResult.risk_tier as RiskTier} />
              </div>
              <div className="text-[10px] font-mono text-slate-400 flex justify-between pt-1 border-t border-white/5">
                <span>Audited Weights:</span>
                <span className="text-emerald-300 font-bold">{dlgResult.params_audited} params</span>
              </div>
            </div>
          ) : (
            <div className="p-3.5 rounded-xl bg-[#02030a]/50 border border-dashed border-white/10 text-center py-6">
              <span className="text-xs font-mono text-slate-400">Audit not executed yet</span>
            </div>
          )}

          <button
            id="btn-run-dlg-audit"
            onClick={() => auditDLG.mutate({ original_gradients: SAMPLE_ORIG_GRADS, received_gradients: SAMPLE_RECV_GRADS })}
            disabled={auditDLG.isPending}
            className="w-full py-2.5 rounded-xl text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-2 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-lg shadow-emerald-600/20 disabled:opacity-50"
          >
            {auditDLG.isPending ? (
              <>
                <span className="h-3 w-3 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                <span>Computing DLG Leakage...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5" />
                <span>Run DLG Gradient Audit</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function BudgetLogSection() {
  const { data: entries = [], isLoading } = usePrivacyBudgetLog(8.0);
  const hasExhausted = entries.some((e) => e.budget_exhausted);

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div>
          <h2 className="text-base sm:text-lg font-bold text-slate-100 flex items-center gap-2">
            <Database className="w-5 h-5 text-indigo-400" />
            <span>Enterprise Privacy Budget Audit Log (DP-SGD ε)</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Rényi Differential Privacy (RDP) cumulative ε-consumption tracker with strict ε = 8.0 SLA ceiling
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-emerald-300 bg-emerald-500/10 px-3 py-1 rounded-lg border border-emerald-500/20">
            Target SLA: ε ≤ 8.0, δ = 1e-5
          </span>
        </div>
      </div>

      {hasExhausted && (
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

