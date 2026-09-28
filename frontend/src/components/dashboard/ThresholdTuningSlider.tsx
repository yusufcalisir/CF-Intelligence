import React, { useState, useMemo } from 'react';
import {
  Sliders,
  DollarSign,
  TrendingUp,
  CheckCircle,
  Sparkles,
  RotateCcw,
} from 'lucide-react';

export interface EmpiricalThresholdDataPoint {
  threshold: number;
  normalized: number;
  tp: number;
  fp: number;
  tn: number;
  fn: number;
  recall: number;
  precision: number;
  fpr: number;
}

// Empirical evaluation points on 5,000 transaction held-out test split (100 fraud positives / 2.0% prevalence)
export const EMPIRICAL_SWEEP_DATA: EmpiricalThresholdDataPoint[] = [
  { threshold: 500, normalized: 0.50, tp: 93, fp: 434, tn: 4466, fn: 7, recall: 0.930, precision: 0.1765, fpr: 0.08857 },
  { threshold: 550, normalized: 0.55, tp: 86, fp: 255, tn: 4645, fn: 14, recall: 0.860, precision: 0.2522, fpr: 0.05204 },
  { threshold: 600, normalized: 0.60, tp: 81, fp: 149, tn: 4751, fn: 19, recall: 0.810, precision: 0.3522, fpr: 0.03041 },
  { threshold: 650, normalized: 0.65, tp: 70, fp: 78, tn: 4822, fn: 30, recall: 0.700, precision: 0.4730, fpr: 0.01592 },
  { threshold: 700, normalized: 0.70, tp: 58, fp: 46, tn: 4854, fn: 42, recall: 0.580, precision: 0.5577, fpr: 0.00939 },
  { threshold: 750, normalized: 0.75, tp: 46, fp: 22, tn: 4878, fn: 54, recall: 0.460, precision: 0.6765, fpr: 0.00449 },
  { threshold: 800, normalized: 0.80, tp: 40, fp: 7, tn: 4893, fn: 60, recall: 0.400, precision: 0.8511, fpr: 0.00143 },
  { threshold: 850, normalized: 0.85, tp: 27, fp: 4, tn: 4896, fn: 73, recall: 0.270, precision: 0.8710, fpr: 0.00082 },
  { threshold: 900, normalized: 0.90, tp: 10, fp: 0, tn: 4900, fn: 90, recall: 0.100, precision: 1.0000, fpr: 0.00000 },
];

export interface ThresholdTuningSliderProps {
  initialThreshold?: number;
  initialCostFn?: number;
  initialCostFp?: number;
  initialCostTp?: number;
  onThresholdChange?: (threshold: number) => void;
}

const DEFAULT_POINT: EmpiricalThresholdDataPoint = EMPIRICAL_SWEEP_DATA[0] ?? {
  threshold: 600,
  normalized: 0.60,
  tp: 81,
  fp: 149,
  tn: 4751,
  fn: 19,
  recall: 0.81,
  precision: 0.3522,
  fpr: 0.03041,
};

export function ThresholdTuningSlider({
  initialThreshold = 600,
  initialCostFn = 850,
  initialCostFp = 45,
  initialCostTp = 15,
  onThresholdChange,
}: ThresholdTuningSliderProps) {
  const [threshold, setThreshold] = useState<number>(initialThreshold);
  const [costFn, setCostFn] = useState<number>(initialCostFn);
  const [costFp, setCostFp] = useState<number>(initialCostFp);
  const [costTp, setCostTp] = useState<number>(initialCostTp);

  // Find nearest discrete empirical point
  const currentPoint: EmpiricalThresholdDataPoint = useMemo(() => {
    let closest: EmpiricalThresholdDataPoint = DEFAULT_POINT;
    let minDiff = Math.abs(DEFAULT_POINT.threshold - threshold);
    for (const pt of EMPIRICAL_SWEEP_DATA) {
      const diff = Math.abs(pt.threshold - threshold);
      if (diff < minDiff) {
        minDiff = diff;
        closest = pt;
      }
    }
    return closest;
  }, [threshold]);

  // Total Positives (ground-truth fraud)
  const totalPositives = currentPoint.tp + currentPoint.fn; // 100
  const baselineCost = totalPositives * costFn;

  // Total Expected Operational Cost at threshold tau
  const totalCost =
    costFn * currentPoint.fn +
    costFp * currentPoint.fp +
    costTp * currentPoint.tp;

  // Net Financial Savings
  const netSavings = baselineCost - totalCost;
  const efficiencyRatio = baselineCost > 0 ? (netSavings / baselineCost) * 100 : 0;

  // Find optimal threshold tau* across all sweep points for the current cost parameters
  const { optimalPoint, optimalSavings } = useMemo(() => {
    let bestPt: EmpiricalThresholdDataPoint = DEFAULT_POINT;
    let maxSav = -Infinity;

    for (const pt of EMPIRICAL_SWEEP_DATA) {
      const cost = costFn * pt.fn + costFp * pt.fp + costTp * pt.tp;
      const sav = baselineCost - cost;
      if (sav > maxSav) {
        maxSav = sav;
        bestPt = pt;
      }
    }

    return { optimalPoint: bestPt, optimalSavings: maxSav };
  }, [costFn, costFp, costTp, baselineCost]);

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value);
    setThreshold(val);
    if (onThresholdChange) {
      onThresholdChange(val);
    }
  };

  const handleApplyOptimal = () => {
    setThreshold(optimalPoint.threshold);
    if (onThresholdChange) {
      onThresholdChange(optimalPoint.threshold);
    }
  };

  const handleResetDefaults = () => {
    setThreshold(600);
    setCostFn(850);
    setCostFp(45);
    setCostTp(15);
    if (onThresholdChange) {
      onThresholdChange(600);
    }
  };

  const isCurrentOptimal = currentPoint.threshold === optimalPoint.threshold;

  return (
    <div
      className="glass-card p-6 space-y-6 border border-indigo-500/20 shadow-xl"
      data-testid="threshold-tuning-slider-container"
    >
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-[var(--color-border)] pb-4">
        <div>
          <div className="flex items-center gap-2 text-indigo-400 font-semibold text-xs tracking-wider uppercase mb-1">
            <Sliders className="w-4 h-4" />
            Cost-Sensitive Risk Threshold & Financial Utility Optimizer
          </div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            Decision Threshold Tuning
            <span className="text-xs px-2.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-mono border border-indigo-500/30">
              τ = {threshold} ({(threshold / 1000).toFixed(2)})
            </span>
          </h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            Formally optimizes decision cutoffs by balancing undetected fraud loss (c_FN) against investigation overhead (c_FP).
          </p>
        </div>

        <div className="flex items-center gap-2">
          {!isCurrentOptimal && (
            <button
              type="button"
              onClick={handleApplyOptimal}
              className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-emerald-950/70 text-emerald-300 hover:bg-emerald-900 border border-emerald-500/40 hover:border-emerald-400 transition-all flex items-center gap-1.5 shadow-sm cursor-pointer"
              title={`Snap to optimal threshold ${optimalPoint.threshold}`}
            >
              <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
              <span>Apply Optimal (τ* = {optimalPoint.threshold})</span>
            </button>
          )}
          <button
            type="button"
            onClick={handleResetDefaults}
            className="text-xs text-slate-400 hover:text-white p-1.5 rounded bg-slate-800/80 hover:bg-slate-700 transition-colors"
            title="Reset parameters to defaults"
            aria-label="Reset parameters"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Main Controls Grid: Threshold Slider + Financial Cost Inputs */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Threshold Slider (2 cols on md) */}
        <div className="md:col-span-2 space-y-3 bg-slate-900/50 p-4 rounded-xl border border-slate-800">
          <div className="flex items-center justify-between">
            <label
              htmlFor="threshold-range-slider"
              className="text-xs font-semibold text-slate-200 flex items-center gap-1.5"
            >
              <span>Operating Decision Cutoff (τ)</span>
              <span className="text-[10px] text-slate-400 font-mono">[500 – 900]</span>
            </label>
            <span className="font-mono text-sm font-bold text-indigo-300">
              {threshold} <span className="text-xs text-slate-400">/ 1000</span>
            </span>
          </div>

          <input
            id="threshold-range-slider"
            type="range"
            min={500}
            max={900}
            step={50}
            value={threshold}
            onChange={handleSliderChange}
            aria-label="Decision Threshold Slider"
            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-indigo-500"
          />

          <div className="flex justify-between text-[10px] text-slate-400 font-mono">
            <span>500 (Aggressive / High Recall)</span>
            <span>700 (Balanced)</span>
            <span>900 (Conservative / High Precision)</span>
          </div>

          {/* Quick Preset Threshold Buttons */}
          <div className="pt-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-slate-400 mr-1">Presets:</span>
            {EMPIRICAL_SWEEP_DATA.map((pt) => {
              const isSelected = pt.threshold === threshold;
              const isOpt = pt.threshold === optimalPoint.threshold;
              return (
                <button
                  key={pt.threshold}
                  type="button"
                  onClick={() => {
                    setThreshold(pt.threshold);
                    if (onThresholdChange) onThresholdChange(pt.threshold);
                  }}
                  className={`text-[10px] px-2 py-0.5 rounded font-mono transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-indigo-600 text-white font-bold shadow-sm'
                      : isOpt
                      ? 'bg-emerald-950/70 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-900/60'
                      : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                  }`}
                >
                  {pt.threshold}
                  {isOpt ? ' ★' : ''}
                </button>
              );
            })}
          </div>
        </div>

        {/* Cost Matrix Config Inputs */}
        <div className="space-y-3 bg-slate-900/50 p-4 rounded-xl border border-slate-800 text-xs">
          <div className="font-semibold text-slate-200 flex items-center gap-1">
            <DollarSign className="w-3.5 h-3.5 text-emerald-400" />
            <span>Financial Cost Matrix ($)</span>
          </div>

          <div>
            <div className="flex justify-between text-[11px] text-slate-300 mb-1">
              <label htmlFor="cost-fn-input">Undetected Fraud Loss (c_FN):</label>
              <span className="font-mono text-emerald-300">${costFn}</span>
            </div>
            <input
              id="cost-fn-input"
              type="number"
              min={100}
              max={10000}
              step={50}
              value={costFn}
              onChange={(e) => setCostFn(Math.max(0, Number(e.target.value)))}
              aria-label="Cost of False Negative"
              className="w-full px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-200 font-mono text-xs focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div>
            <div className="flex justify-between text-[11px] text-slate-300 mb-1">
              <label htmlFor="cost-fp-input">False Alarm Overhead (c_FP):</label>
              <span className="font-mono text-amber-300">${costFp}</span>
            </div>
            <input
              id="cost-fp-input"
              type="number"
              min={5}
              max={500}
              step={5}
              value={costFp}
              onChange={(e) => setCostFp(Math.max(0, Number(e.target.value)))}
              aria-label="Cost of False Positive"
              className="w-full px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-200 font-mono text-xs focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div>
            <div className="flex justify-between text-[11px] text-slate-300 mb-1">
              <label htmlFor="cost-tp-input">SAR Filing / Hold Cost (c_TP):</label>
              <span className="font-mono text-indigo-300">${costTp}</span>
            </div>
            <input
              id="cost-tp-input"
              type="number"
              min={0}
              max={200}
              step={5}
              value={costTp}
              onChange={(e) => setCostTp(Math.max(0, Number(e.target.value)))}
              aria-label="Cost of True Positive"
              className="w-full px-2 py-1 rounded bg-slate-800 border border-slate-700 text-slate-200 font-mono text-xs focus:outline-none focus:border-indigo-500"
            />
          </div>
        </div>
      </div>

      {/* KPI Ribbons: Cost & Net Savings */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="text-[11px] text-slate-400 font-medium">Expected Total Cost</div>
          <div className="text-lg font-bold text-white font-mono mt-0.5">
            ${totalCost.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
          </div>
          <div className="text-[10px] text-slate-500">c_FN·FN + c_FP·FP + c_TP·TP</div>
        </div>

        <div className="p-3 rounded-xl bg-emerald-950/40 border border-emerald-500/30">
          <div className="text-[11px] text-emerald-400 font-medium flex items-center gap-1">
            <TrendingUp className="w-3 h-3" />
            <span>Net Financial Savings</span>
          </div>
          <div className="text-lg font-bold text-emerald-300 font-mono mt-0.5">
            +${netSavings.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
          </div>
          <div className="text-[10px] text-emerald-400/80">vs Zero-Detection Baseline</div>
        </div>

        <div className="p-3 rounded-xl bg-indigo-950/40 border border-indigo-500/30">
          <div className="text-[11px] text-indigo-300 font-medium">Efficiency Ratio</div>
          <div className="text-lg font-bold text-indigo-200 font-mono mt-0.5">
            {efficiencyRatio.toFixed(1)}%
          </div>
          <div className="text-[10px] text-indigo-400/80">Baseline Loss Averted</div>
        </div>

        <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800">
          <div className="text-[11px] text-slate-400 font-medium flex items-center gap-1">
            <CheckCircle className="w-3 h-3 text-emerald-400" />
            <span>Optimal Cutoff (τ*)</span>
          </div>
          <div className="text-lg font-bold text-white font-mono mt-0.5">
            {optimalPoint.threshold}
            {isCurrentOptimal && (
              <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-sans">
                Active
              </span>
            )}
          </div>
          <div className="text-[10px] text-slate-500">Max Savings: ${optimalSavings.toLocaleString()}</div>
        </div>
      </div>

      {/* Confusion Matrix & Classification Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 2x2 Confusion Matrix Visualizer */}
        <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800 space-y-3">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-200">
            <span>Projected Confusion Matrix (N = 5,000)</span>
            <span className="text-[11px] text-slate-400 font-mono">100 Fraud / 4,900 Clean</span>
          </div>

          <div className="grid grid-cols-2 gap-2 text-center text-xs">
            <div className="p-3 rounded-lg bg-emerald-950/50 border border-emerald-500/30">
              <div className="text-[10px] text-emerald-400 font-semibold uppercase">True Positive (TP)</div>
              <div className="text-xl font-mono font-bold text-emerald-200 mt-0.5">{currentPoint.tp}</div>
              <div className="text-[10px] text-emerald-400/80">Fraud Blocked</div>
            </div>

            <div className="p-3 rounded-lg bg-amber-950/40 border border-amber-500/30">
              <div className="text-[10px] text-amber-400 font-semibold uppercase">False Positive (FP)</div>
              <div className="text-xl font-mono font-bold text-amber-200 mt-0.5">{currentPoint.fp}</div>
              <div className="text-[10px] text-amber-400/80">False Alarms Reviewed</div>
            </div>

            <div className="p-3 rounded-lg bg-red-950/40 border border-red-500/30">
              <div className="text-[10px] text-red-400 font-semibold uppercase">False Negative (FN)</div>
              <div className="text-xl font-mono font-bold text-red-200 mt-0.5">{currentPoint.fn}</div>
              <div className="text-[10px] text-red-400/80">Undetected Fraud Loss</div>
            </div>

            <div className="p-3 rounded-lg bg-slate-800/60 border border-slate-700/50">
              <div className="text-[10px] text-slate-400 font-semibold uppercase">True Negative (TN)</div>
              <div className="text-xl font-mono font-bold text-slate-200 mt-0.5">{currentPoint.tn}</div>
              <div className="text-[10px] text-slate-400">Clean Pass-Through</div>
            </div>
          </div>
        </div>

        {/* Operating Performance Metrics */}
        <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800 space-y-3">
          <div className="text-xs font-semibold text-slate-200">Statistical Performance Metrics</div>

          <div className="space-y-2 text-xs">
            <div className="flex justify-between items-center py-1 border-b border-slate-800">
              <span className="text-slate-400">Recall / Sensitivity (Fraud Captured):</span>
              <span className="font-mono font-bold text-emerald-300">
                {(currentPoint.recall * 100).toFixed(1)}%
              </span>
            </div>

            <div className="flex justify-between items-center py-1 border-b border-slate-800">
              <span className="text-slate-400">Precision / PPV (Alert Accuracy):</span>
              <span className="font-mono font-bold text-indigo-300">
                {(currentPoint.precision * 100).toFixed(1)}%
              </span>
            </div>

            <div className="flex justify-between items-center py-1 border-b border-slate-800">
              <span className="text-slate-400">False Positive Rate (FPR):</span>
              <span className="font-mono font-bold text-amber-300">
                {(currentPoint.fpr * 100).toFixed(3)}%
              </span>
            </div>

            <div className="flex justify-between items-center py-1 border-b border-slate-800">
              <span className="text-slate-400">F1-Score:</span>
              <span className="font-mono font-bold text-slate-200">
                {(
                  (2 * currentPoint.precision * currentPoint.recall) /
                  (currentPoint.precision + currentPoint.recall || 1)
                ).toFixed(4)}
              </span>
            </div>

            <div className="flex justify-between items-center py-1">
              <span className="text-slate-400">Baseline Default Cost (Zero-Detection):</span>
              <span className="font-mono text-red-400 font-semibold">
                ${baselineCost.toLocaleString()}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
