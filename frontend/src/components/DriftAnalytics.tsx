import React from 'react';
import { ShieldAlert, BarChart3, CheckCircle2 } from 'lucide-react';

export const DriftAnalytics: React.FC = () => {
  const driftMetrics = [
    { feature: 'transaction_amount', psi: 0.042, status: 'STABLE', p_value: 0.88 },
    { feature: 'merchant_category', psi: 0.128, status: 'MODERATE_DRIFT', p_value: 0.04 },
    { feature: 'country_code', psi: 0.245, status: 'SIGNIFICANT_DRIFT', p_value: 0.001 },
    { feature: 'velocity', psi: 0.018, status: 'STABLE', p_value: 0.94 },
    { feature: 'account_age_days', psi: 0.035, status: 'STABLE', p_value: 0.82 },
  ];

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="glass-card p-4 rounded-xl flex items-center justify-between gap-4 border border-slate-800">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div>
            <h2 className="font-semibold text-sm text-slate-100">Model Drift & Calibration Analytics</h2>
            <p className="text-xs text-slate-400">Population Stability Index (PSI) & Kolmogorov-Smirnov Drift Detection</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* PSI Metric Cards */}
        <div className="lg:col-span-7 glass-card rounded-xl p-5 border border-slate-800 space-y-4">
          <h3 className="font-semibold text-xs text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2">
            Feature Population Stability Index (PSI)
          </h3>

          <div className="space-y-3">
            {driftMetrics.map((m, idx) => (
              <div key={idx} className="bg-slate-900/80 p-3.5 rounded-lg border border-slate-800 flex items-center justify-between text-xs">
                <div>
                  <span className="font-mono text-cyan-400 font-medium">{m.feature}</span>
                  <p className="text-slate-400 text-[11px] mt-0.5">KS-test p-value: {m.p_value}</p>
                </div>
                <div className="text-right">
                  <span className="font-mono font-bold text-slate-200 text-sm">{m.psi.toFixed(3)}</span>
                  <div className="mt-1">
                    {m.status === 'STABLE' && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        STABLE (&lt;0.10)
                      </span>
                    )}
                    {m.status === 'MODERATE_DRIFT' && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                        MODERATE (0.10-0.20)
                      </span>
                    )}
                    {m.status === 'SIGNIFICANT_DRIFT' && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
                        DRIFT (&gt;0.20)
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ECE Calibration Card */}
        <div className="lg:col-span-5 glass-card rounded-xl p-5 border border-slate-800 space-y-4">
          <h3 className="font-semibold text-xs text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2">
            Expected Calibration Error (ECE)
          </h3>

          <div className="bg-slate-900/80 p-4 rounded-lg border border-slate-800 text-xs space-y-3">
            <div className="flex justify-between items-center">
              <span className="text-slate-400">Brier Score</span>
              <span className="font-mono font-bold text-cyan-400">0.038</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-400">Global ECE</span>
              <span className="font-mono font-bold text-emerald-400">0.019 (Well Calibrated)</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-400">Reliability Binning</span>
              <span className="font-mono text-slate-200">10 Bins</span>
            </div>
          </div>

          <div className="bg-cyan-500/10 border border-cyan-500/20 text-cyan-300 p-3 rounded-lg text-xs">
            Model predictions are well-calibrated across all 3 consortium banks. Auto-rollback threshold set at PSI &gt; 0.25.
          </div>
        </div>
      </div>
    </div>
  );
};
