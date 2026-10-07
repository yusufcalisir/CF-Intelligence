import React from 'react';
import { EvaluationMetrics } from '../../api/types';

interface AdversarialDefensePanelProps {
  metrics?: EvaluationMetrics | null;
  isEnabled?: boolean;
  attackType?: string;
  epsilon?: number;
}

export const AdversarialDefensePanel: React.FC<AdversarialDefensePanelProps> = ({
  metrics,
  isEnabled = false,
  attackType = 'fgsm',
  epsilon = 0.05,
}) => {
  const cleanAcc = metrics?.clean_accuracy !== undefined && metrics?.clean_accuracy !== null
    ? metrics.clean_accuracy
    : (metrics?.accuracy !== undefined && metrics?.accuracy !== null ? metrics.accuracy : null);
  const robustAcc = metrics?.robust_accuracy !== undefined && metrics?.robust_accuracy !== null
    ? metrics.robust_accuracy
    : null;
  const fgsmEvasion = metrics?.fgsm_evasion_rate !== undefined && metrics?.fgsm_evasion_rate !== null
    ? metrics.fgsm_evasion_rate
    : null;
  const pgdEvasion = metrics?.pgd_evasion_rate !== undefined && metrics?.pgd_evasion_rate !== null
    ? metrics.pgd_evasion_rate
    : null;
  const robustnessScore = metrics?.adversarial_robustness_score !== undefined && metrics?.adversarial_robustness_score !== null
    ? metrics.adversarial_robustness_score
    : null;

  const fgsmRejection = fgsmEvasion !== null ? Math.max(0, Math.min(100, Math.round((1 - fgsmEvasion) * 100))) : null;
  const pgdRejection = pgdEvasion !== null ? Math.max(0, Math.min(100, Math.round((1 - pgdEvasion) * 100))) : null;

  return (
    <div className="bg-slate-900/80 backdrop-blur-md border border-cyan-500/30 rounded-xl p-5 shadow-lg shadow-cyan-950/20 text-slate-100 mb-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-4 mb-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          </div>
          <div>
            <h3 className="font-bold text-slate-100 flex items-center gap-2">
              Active Defense & Adversarial ML Training
              <span className="text-xs px-2 py-0.5 rounded-full font-mono bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">
                L_inf Noise Bounds (ε = {epsilon})
              </span>
            </h3>
            <p className="text-xs text-slate-400">
              Hardens local node decision boundaries against FGSM & PGD evasion attacks
            </p>
          </div>
        </div>

        {/* Status Badge */}
        <div className={`px-3 py-1 rounded-full text-xs font-semibold flex items-center gap-2 border ${
          isEnabled
            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/40'
            : 'bg-amber-500/10 text-amber-400 border-amber-500/40'
        }`}>
          <span className={`w-2 h-2 rounded-full ${isEnabled ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
          {isEnabled ? `ADV-TRAINING ENABLED (${attackType.toUpperCase()})` : 'BASELINE (NO ADV DEFENSE)'}
        </div>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
        {/* Robustness Score */}
        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50">
          <span className="text-xs text-slate-400 font-medium">Robustness Score</span>
          <div className="text-xl font-bold font-mono text-cyan-400 mt-1">
            {robustnessScore !== null ? `${(robustnessScore * 100).toFixed(1)}%` : '—'}
          </div>
          <p className="text-[10px] text-slate-500 mt-1">Overall Evasion Immunity</p>
        </div>

        {/* Clean vs Robust Acc */}
        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50">
          <span className="text-xs text-slate-400 font-medium">Clean vs Robust Accuracy</span>
          <div className="flex items-baseline gap-2 mt-1 font-mono">
            <span className="text-lg font-bold text-slate-200">
              {cleanAcc !== null ? `${(cleanAcc * 100).toFixed(1)}%` : '—'}
            </span>
            <span className="text-xs text-slate-500">/</span>
            <span className={`text-lg font-bold ${isEnabled ? 'text-emerald-400' : 'text-rose-400'}`}>
              {robustAcc !== null ? `${(robustAcc * 100).toFixed(1)}%` : '—'}
            </span>
          </div>
          <p className="text-[10px] text-slate-500 mt-1">Clean Acc vs Perturbed Acc</p>
        </div>

        {/* FGSM Defense */}
        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50">
          <span className="text-xs text-slate-400 font-medium">FGSM Evasion Blocked</span>
          <div className="text-xl font-bold font-mono text-emerald-400 mt-1">
            {fgsmRejection !== null ? `${fgsmRejection}%` : '—'}
          </div>
          <div className="w-full bg-slate-700 rounded-full h-1.5 mt-2">
            <div
              className="bg-emerald-400 h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${fgsmRejection ?? 0}%` }}
            />
          </div>
        </div>

        {/* PGD Defense */}
        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50">
          <span className="text-xs text-slate-400 font-medium">PGD Evasion Blocked</span>
          <div className="text-xl font-bold font-mono text-cyan-400 mt-1">
            {pgdRejection !== null ? `${pgdRejection}%` : '—'}
          </div>
          <div className="w-full bg-slate-700 rounded-full h-1.5 mt-2">
            <div
              className="bg-cyan-400 h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${pgdRejection ?? 0}%` }}
            />
          </div>
        </div>
      </div>

      {/* Info Banner */}
      <div className="text-xs text-slate-400 bg-slate-950/40 rounded-lg p-3 border border-slate-800 flex items-start gap-2">
        <svg className="w-4 h-4 text-cyan-400 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span>
          <strong>Tabular Domain Constraint Projection:</strong> Evasion noise is bounded within L_inf (ε = {epsilon}) while projecting non-negative transaction constraints (Π_x) to prevent false positives during active adversarial training.
        </span>
      </div>
    </div>
  );
};
