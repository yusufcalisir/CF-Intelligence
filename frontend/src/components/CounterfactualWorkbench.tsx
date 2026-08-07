import React, { useState } from 'react';
import { Sliders, CheckCircle2, AlertTriangle, ArrowRight, RotateCcw, ShieldCheck } from 'lucide-react';
import { CounterfactualReport } from '../types';
import { fetchCounterfactual } from '../services/api';

export const CounterfactualWorkbench: React.FC = () => {
  const [amount, setAmount] = useState(15000);
  const [velocity, setVelocity] = useState(28);
  const [merchantRisk, setMerchantRisk] = useState(0.95);
  const [report, setReport] = useState<CounterfactualReport | null>(null);
  const [loading, setLoading] = useState(false);

  const calculateDynamicScore = () => {
    const baseAmountRisk = Math.min(300, (amount / 20000) * 300);
    const velocityRisk = Math.min(250, (velocity / 30) * 250);
    const merchRisk = merchantRisk * 250;
    return Math.round(Math.min(990, baseAmountRisk + velocityRisk + merchRisk + 120));
  };

  const currentScore = calculateDynamicScore();
  const isSuspicious = currentScore >= 600;

  const handleSimulateCounterfactual = async () => {
    setLoading(true);
    const res = await fetchCounterfactual('alt_1001', 350.0);
    setReport(res);
    setLoading(false);
  };

  const handleResetSliders = () => {
    setAmount(15000);
    setVelocity(28);
    setMerchantRisk(0.95);
    setReport(null);
  };

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="glass-card p-4 rounded-xl flex items-center justify-between gap-4 border border-slate-800">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
            <Sliders className="h-5 w-5" />
          </div>
          <div>
            <h2 className="font-semibold text-sm text-slate-100">Counterfactual Remediation Workbench</h2>
            <p className="text-xs text-slate-400">Interactive Minimum Remediating Feature Path Simulator</p>
          </div>
        </div>

        <button
          onClick={handleResetSliders}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-700 text-xs text-slate-300 hover:text-white transition-all"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          <span>Reset Parameters</span>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Interactive Feature Adjustment Sliders */}
        <div className="lg:col-span-5 glass-card rounded-xl p-5 border border-slate-800 space-y-5">
          <h3 className="font-semibold text-xs text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2">
            Adjust Transaction Signals
          </h3>

          {/* Slider 1: Transaction Amount */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-300 font-medium">Transaction Amount ($)</span>
              <span className="font-mono text-cyan-400 font-semibold">${amount.toLocaleString()}</span>
            </div>
            <input
              type="range"
              min="100"
              max="50000"
              step="500"
              value={amount}
              onChange={(e) => setAmount(Number(e.target.value))}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>

          {/* Slider 2: Velocity */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-300 font-medium">Hourly Velocity (txns/hr)</span>
              <span className="font-mono text-cyan-400 font-semibold">{velocity} txns</span>
            </div>
            <input
              type="range"
              min="1"
              max="40"
              step="1"
              value={velocity}
              onChange={(e) => setVelocity(Number(e.target.value))}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>

          {/* Slider 3: Merchant Risk */}
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-300 font-medium">Merchant Risk Category</span>
              <span className="font-mono text-cyan-400 font-semibold">{(merchantRisk * 100).toFixed(0)}% Risk</span>
            </div>
            <input
              type="range"
              min="0.05"
              max="0.99"
              step="0.05"
              value={merchantRisk}
              onChange={(e) => setMerchantRisk(Number(e.target.value))}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>

          <button
            onClick={handleSimulateCounterfactual}
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-gradient-to-r from-cyan-500 to-purple-600 font-medium text-xs text-white shadow-lg shadow-cyan-500/20 hover:opacity-90 transition-all flex items-center justify-center gap-2"
          >
            {loading ? 'Calculating Path...' : 'Simulate Optimal Counterfactual Path'}
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>

        {/* Live Risk Score Gauge & Remediation Path */}
        <div className="lg:col-span-7 space-y-6">
          {/* Gauge Status */}
          <div className="glass-card rounded-xl p-6 border border-slate-800 flex items-center justify-between">
            <div>
              <span className="text-xs text-slate-400 uppercase font-medium tracking-wider">Live Evaluated Risk Score</span>
              <div className="flex items-baseline gap-2 mt-1">
                <span className={`text-4xl font-extrabold font-mono ${isSuspicious ? 'text-rose-500' : 'text-emerald-400'}`}>
                  {currentScore}
                </span>
                <span className="text-slate-400 text-xs">/ 1000</span>
              </div>
              <div className="flex items-center gap-2 mt-2">
                {isSuspicious ? (
                  <span className="flex items-center gap-1 text-xs text-rose-400 bg-rose-500/10 px-2.5 py-1 rounded-full border border-rose-500/20 font-medium">
                    <AlertTriangle className="h-3.5 w-3.5" /> High Risk (Fraud Suspected)
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-xs text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-full border border-emerald-500/20 font-medium">
                    <ShieldCheck className="h-3.5 w-3.5" /> Cleared (Below Threshold)
                  </span>
                )}
              </div>
            </div>

            {/* Score Radial Bar */}
            <div className="relative w-28 h-28 flex items-center justify-center">
              <svg className="w-full h-full transform -rotate-90">
                <circle cx="56" cy="56" r="45" stroke="currentColor" strokeWidth="8" className="text-slate-800" fill="transparent" />
                <circle
                  cx="56"
                  cy="56"
                  r="45"
                  stroke="currentColor"
                  strokeWidth="8"
                  className={isSuspicious ? 'text-rose-500' : 'text-emerald-400'}
                  fill="transparent"
                  strokeDasharray={282}
                  strokeDashoffset={282 - (282 * (currentScore / 1000))}
                  strokeLinecap="round"
                />
              </svg>
              <span className="absolute font-mono font-bold text-slate-200 text-sm">
                {((currentScore / 1000) * 100).toFixed(0)}%
              </span>
            </div>
          </div>

          {/* Counterfactual Remediation Path Recommendations */}
          {report && (
            <div className="glass-card rounded-xl p-5 border border-slate-800 space-y-4">
              <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
                <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                <h3 className="font-semibold text-xs text-slate-100 uppercase tracking-wider">
                  Remediation Action Path ({report.changes.length} Required Adjustments)
                </h3>
              </div>

              <div className="space-y-3">
                {report.changes.map((c, idx) => (
                  <div key={idx} className="bg-slate-900/80 p-3.5 rounded-lg border border-slate-800 text-xs flex items-center justify-between">
                    <div>
                      <span className="font-mono text-cyan-400 font-semibold uppercase">{c.feature}</span>
                      <p className="text-slate-300 mt-0.5">{c.description}</p>
                    </div>
                    <div className="text-right">
                      <span className="text-rose-400 font-mono line-through mr-2">{String(c.original_value)}</span>
                      <span className="text-emerald-400 font-mono font-bold">&rarr; {String(c.suggested_value)}</span>
                    </div>
                  </div>
                ))}
              </div>

              <div className="bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 p-3 rounded-lg text-xs">
                {report.summary_text}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
