import React, { useState, useEffect } from 'react';
import {
  Lock,
  Unlock,
  CheckCircle2,
  AlertTriangle,
  RotateCcw,
  Sparkles,
  Sliders,
  ArrowRight,
  TrendingDown,
  Layers,
  Info,
} from 'lucide-react';
import { CounterfactualReport } from '../../types';
import { fetchCounterfactual } from '../../services/api';

export interface CounterfactualExplorerProps {
  alertId?: string;
  initialScore?: number;
  initialAmount?: number;
  initialVelocity?: number;
  initialMerchantRisk?: number;
  onSimulationComplete?: (report: CounterfactualReport) => void;
}

export const CounterfactualExplorer: React.FC<CounterfactualExplorerProps> = ({
  alertId = 'alt_1001',
  initialScore = 780.0,
  initialAmount = 15000.0,
  initialVelocity = 28.0,
  initialMerchantRisk = 0.95,
  onSimulationComplete,
}) => {
  // Domain bounded mutable parameters
  const [amount, setAmount] = useState<number>(initialAmount);
  const [velocity, setVelocity] = useState<number>(initialVelocity);
  const [merchantRisk, setMerchantRisk] = useState<number>(initialMerchantRisk);
  const [targetScore, setTargetScore] = useState<number>(350.0);

  // Re-inference state
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<CounterfactualReport | null>(null);

  // Immutable KYC and temporal features (strictly locked)
  const immutableFeatures = [
    { name: 'Customer National ID Hash', value: 'SHA256:7f9a...3c21', reason: 'Immutable KYC Identity' },
    { name: 'Account Tenure (Days)', value: '180 days', reason: 'Physical Historical Fact' },
    { name: 'Prior Chargeback Records', value: '0 chargebacks', reason: 'Immutable Financial Ledger' },
    { name: 'Originating Banking Tenant', value: 'Bank Alpha Corp', reason: 'Multi-Tenant Isolation' },
  ];

  const handleRunReInference = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchCounterfactual({
        alert_id: alertId,
        target_score: targetScore,
        amount,
        velocity,
        merchant_risk: merchantRisk,
      });
      setReport(result);
      if (onSimulationComplete) {
        onSimulationComplete(result);
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to execute counterfactual re-inference');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    handleRunReInference();
  }, [alertId]);

  const handleReset = () => {
    setAmount(initialAmount);
    setVelocity(initialVelocity);
    setMerchantRisk(initialMerchantRisk);
    setTargetScore(350.0);
    handleRunReInference();
  };

  const currentScore = report ? report.remediated_score : initialScore;
  const isCleared = report ? report.is_cleared : false;
  const scoreDelta = report ? Math.max(0, report.original_score - report.remediated_score) : 0;

  return (
    <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-6 shadow-2xl backdrop-blur-md text-slate-100 transition-all">
      {/* Header Banner */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
            <Sliders className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-white flex items-center gap-2">
              Counterfactual Explorer
              <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded-full bg-cyan-950/60 text-cyan-300 border border-cyan-800/40">
                Domain Guardrails
              </span>
            </h3>
            <p className="text-xs text-slate-400">
              Interactive minimal perturbation workbench with live RiskScoringEngine re-inference
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleReset}
            disabled={loading}
            className="px-3 py-1.5 text-xs font-medium text-slate-400 hover:text-slate-200 bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 rounded-lg transition-colors flex items-center gap-1.5"
            title="Reset parameters to initial alert baseline"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset
          </button>
          <button
            onClick={handleRunReInference}
            disabled={loading}
            className="px-4 py-1.5 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 disabled:opacity-50 rounded-lg shadow-lg shadow-indigo-500/20 transition-all flex items-center gap-2"
          >
            {loading ? (
              <>
                <span className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                Re-Inferring...
              </>
            ) : (
              <>
                <Sparkles className="w-3.5 h-3.5 text-indigo-200" />
                Run Re-Inference
              </>
            )}
          </button>
        </div>
      </div>

      {/* Main Grid: Immutable Guardrails vs Mutable Controls */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mt-6">
        {/* Left Column: Immutable Attributes (4 cols) */}
        <div className="lg:col-span-5 space-y-4">
          <div className="bg-slate-950/60 border border-amber-500/20 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-bold text-amber-300 flex items-center gap-1.5">
                <Lock className="w-3.5 h-3.5 text-amber-400" />
                Immutable Attribute Guardrails
              </span>
              <span className="text-[10px] font-mono text-slate-400 bg-slate-900 px-2 py-0.5 rounded border border-slate-800">
                0% Mutation Permitted
              </span>
            </div>
            <p className="text-[11px] text-slate-400 mb-3 leading-relaxed">
              These KYC, legal identity, and temporal parameters are strictly frozen. The search algorithm is prohibited from perturbing these values.
            </p>

            <div className="space-y-2">
              {immutableFeatures.map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between p-2 rounded bg-slate-900/80 border border-slate-800/80 text-xs"
                >
                  <div className="flex items-center gap-2">
                    <Lock className="w-3 h-3 text-amber-400/80 shrink-0" />
                    <div>
                      <div className="font-medium text-slate-300 text-[11px]">{item.name}</div>
                      <div className="text-[10px] text-slate-500">{item.reason}</div>
                    </div>
                  </div>
                  <span className="font-mono text-[11px] text-slate-400 bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                    {item.value}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Re-Inference Score Telemetry Card */}
          <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-4">
            <div className="text-xs font-semibold text-slate-300 mb-3 flex items-center gap-1.5">
              <TrendingDown className="w-4 h-4 text-emerald-400" />
              Live Re-Inference Telemetry
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800">
                <span className="text-[10px] font-mono uppercase text-slate-500">Original Risk</span>
                <div className="text-xl font-bold font-mono text-rose-400 mt-0.5">
                  {report ? report.original_score.toFixed(1) : initialScore.toFixed(1)}
                  <span className="text-xs text-slate-500 font-normal">/1000</span>
                </div>
                <span className="inline-block mt-1 text-[10px] font-semibold text-rose-400 bg-rose-950/50 px-1.5 py-0.5 rounded border border-rose-800/30">
                  {report && report.original_score >= 800 ? 'CRITICAL RISK' : 'HIGH RISK'}
                </span>
              </div>

              <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800">
                <span className="text-[10px] font-mono uppercase text-slate-500">Remediated Risk</span>
                <div className="text-xl font-bold font-mono text-emerald-400 mt-0.5">
                  {currentScore.toFixed(1)}
                  <span className="text-xs text-slate-500 font-normal">/1000</span>
                </div>
                <span
                  className={`inline-block mt-1 text-[10px] font-semibold px-1.5 py-0.5 rounded border ${
                    isCleared
                      ? 'text-emerald-300 bg-emerald-950/50 border-emerald-800/30'
                      : 'text-amber-300 bg-amber-950/50 border-amber-800/30'
                  }`}
                >
                  {isCleared ? 'CLEARED (ALLOW)' : 'IN PROGRESS'}
                </span>
              </div>
            </div>

            {/* Score Delta Uplift */}
            {scoreDelta > 0 && (
              <div className="mt-3 p-2.5 rounded-lg bg-emerald-950/20 border border-emerald-500/20 flex items-center justify-between text-xs text-emerald-300">
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  Risk Score Reduction:
                </span>
                <span className="font-mono font-bold text-emerald-400">-{scoreDelta.toFixed(1)} pts</span>
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Mutable Controls & Remediation Steps (7 cols) */}
        <div className="lg:col-span-7 space-y-4">
          <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-4 space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-indigo-300 flex items-center gap-1.5">
                <Unlock className="w-3.5 h-3.5 text-indigo-400" />
                Actionable Parameters (Bounded Domain Sliders)
              </span>
              <span className="text-[10px] font-mono text-slate-400 bg-slate-900 px-2 py-0.5 rounded border border-slate-800">
                Min Amount: $0.01 | Min Vel: 0
              </span>
            </div>

            {/* Slider 1: Transaction Amount */}
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs font-medium">
                <span className="text-slate-300">Transaction Monetary Amount ($):</span>
                <span className="font-mono font-bold text-cyan-400">${amount.toLocaleString()}</span>
              </div>
              <input
                type="range"
                min="10"
                max="50000"
                step="50"
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value))}
                className="w-full accent-cyan-500 cursor-pointer h-1.5 bg-slate-800 rounded-lg"
              />
              <div className="flex justify-between text-[10px] font-mono text-slate-500">
                <span>$10 (Baseline min)</span>
                <span>$25,000</span>
                <span>$50,000 (Max limit)</span>
              </div>
            </div>

            {/* Slider 2: Velocity */}
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs font-medium">
                <span className="text-slate-300">Burst Velocity (txns/hr):</span>
                <span className="font-mono font-bold text-indigo-400">{velocity} txns/hr</span>
              </div>
              <input
                type="range"
                min="0"
                max="50"
                step="1"
                value={velocity}
                onChange={(e) => setVelocity(Number(e.target.value))}
                className="w-full accent-indigo-500 cursor-pointer h-1.5 bg-slate-800 rounded-lg"
              />
              <div className="flex justify-between text-[10px] font-mono text-slate-500">
                <span>0 txns/hr (Idle)</span>
                <span>25 txns/hr</span>
                <span>50 txns/hr (Spike)</span>
              </div>
            </div>

            {/* Slider 3: Merchant Risk Index */}
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs font-medium">
                <span className="text-slate-300">Merchant Risk Factor:</span>
                <span className="font-mono font-bold text-purple-400">{merchantRisk.toFixed(2)}</span>
              </div>
              <input
                type="range"
                min="0.0"
                max="1.0"
                step="0.05"
                value={merchantRisk}
                onChange={(e) => setMerchantRisk(Number(e.target.value))}
                className="w-full accent-purple-500 cursor-pointer h-1.5 bg-slate-800 rounded-lg"
              />
              <div className="flex justify-between text-[10px] font-mono text-slate-500">
                <span>0.00 (3DS Retail)</span>
                <span>0.50 (Standard)</span>
                <span>1.00 (High-risk Crypto/Gambling)</span>
              </div>
            </div>

            {/* Target Score Threshold */}
            <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-xs">
              <span className="text-slate-400 flex items-center gap-1.5">
                <Info className="w-3.5 h-3.5 text-cyan-400" />
                Target Clearance Score:
              </span>
              <span className="font-mono font-bold text-slate-200 bg-slate-900 px-2 py-0.5 rounded border border-slate-700/60">
                ≤ {targetScore.toFixed(0)} / 1000
              </span>
            </div>
          </div>

          {/* Remediation Changes (L0 Sparsity Output) */}
          <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-bold text-slate-200 flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-cyan-400" />
                Verified Remediation Steps (Minimal L0 Norm)
              </span>
              <span className="text-[10px] font-mono text-cyan-400 bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-800/30">
                {report ? `${report.changes.length} Action(s)` : '0 Actions'}
              </span>
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-rose-950/30 border border-rose-500/30 text-xs text-rose-300 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
                {error}
              </div>
            )}

            {report && report.changes.length > 0 ? (
              <div className="space-y-2.5">
                {report.changes.map((change, idx) => (
                  <div
                    key={idx}
                    className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800 text-xs space-y-1.5"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-bold text-indigo-300 uppercase text-[11px]">
                        {change.feature.replace(/_/g, ' ')}
                      </span>
                      <div className="flex items-center gap-1.5 text-[11px] font-mono">
                        <span className="text-rose-400 line-through">{String(change.original_value)}</span>
                        <ArrowRight className="w-3 h-3 text-slate-500" />
                        <span className="text-emerald-400 font-bold">{String(change.suggested_value)}</span>
                      </div>
                    </div>
                    <p className="text-[11px] text-slate-400 leading-normal">{change.description}</p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-6 text-center text-xs text-slate-500">
                No active remediation steps required; transaction satisfies baseline clearance.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default CounterfactualExplorer;
