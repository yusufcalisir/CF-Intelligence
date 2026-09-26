import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useComparativeBaselines } from '../../api/queries';
import { ComparativeModelItem } from '../../api/types';

export default function ComparativeModelWidget() {
  const { data: benchmarkData, isLoading, isError } = useComparativeBaselines();
  const [selectedMetric, setSelectedMetric] = useState<'pr_auc' | 'roc_auc' | 'recall_at_01_fpr'>('pr_auc');

  if (isLoading) {
    return (
      <div className="p-6 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl animate-pulse">
        <div className="h-6 w-72 bg-slate-800 rounded mb-4" />
        <div className="h-20 bg-slate-900/60 rounded-xl mb-4" />
        <div className="h-48 bg-slate-900/40 rounded-xl" />
      </div>
    );
  }

  if (isError || !benchmarkData) {
    return (
      <div className="p-5 rounded-2xl bg-[#090a1f]/80 border border-rose-500/20 text-rose-400 font-mono text-xs">
        ⚠️ Failed to load comparative benchmark baselines. Verify backend orchestrator is active.
      </div>
    );
  }

  const { comparison_matrix, centralization_gap_analysis, silo_deficit_analysis, dataset_name } = benchmarkData;

  const metricLabels: Record<string, string> = {
    pr_auc: 'PR-AUC (Precision-Recall)',
    roc_auc: 'ROC-AUC (Discrimination)',
    recall_at_01_fpr: 'Recall @ 0.1% FPR (High-Precision)',
  };

  const getCategoryBadge = (category: ComparativeModelItem['category']) => {
    switch (category) {
      case 'PRODUCTION_CHAMPION':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono tracking-wider uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            Champion (PET)
          </span>
        );
      case 'THEORETICAL_UPPER_BOUND':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono tracking-wider uppercase bg-rose-500/10 text-rose-300 border border-rose-500/20">
            Upper Bound (Illegal)
          </span>
        );
      case 'ISOLATED_SILO':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono tracking-wider uppercase bg-amber-500/10 text-amber-300 border border-amber-500/20">
            Isolated Silo
          </span>
        );
      case 'CLASSICAL_BASELINE':
      default:
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono tracking-wider uppercase bg-blue-500/10 text-blue-300 border border-blue-500/20">
            Classical Baseline
          </span>
        );
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl mt-6 relative overflow-hidden"
    >
      {/* Background Decorative Glow */}
      <div className="absolute top-0 right-1/4 w-96 h-96 bg-indigo-500/5 rounded-full blur-3xl pointer-events-none" />

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-white/6 pb-4 mb-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-base">⚖️</span>
            <h3 className="text-sm font-bold uppercase tracking-wider text-slate-200">
              Multi-Paradigm Benchmark Baselines & Comparative Analysis
            </h3>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
              {dataset_name}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Empirical evaluation comparing Centralized Upper Bound, Federated Champion, Isolated Banking Silos, and Classical Tabular Models on untouched global test set.
          </p>
        </div>

        {/* Metric Selector Pills */}
        <div className="flex items-center gap-1.5 bg-black/40 p-1 rounded-xl border border-white/5 self-start sm:self-auto shrink-0">
          {(['pr_auc', 'roc_auc', 'recall_at_01_fpr'] as const).map((metric) => (
            <button
              key={metric}
              onClick={() => setSelectedMetric(metric)}
              className={`px-2.5 py-1 rounded-lg text-xs font-mono font-medium transition-all ${
                selectedMetric === metric
                  ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
              }`}
            >
              {metric === 'pr_auc' ? 'PR-AUC' : metric === 'roc_auc' ? 'ROC-AUC' : 'Recall@0.1%'}
            </button>
          ))}
        </div>
      </div>

      {/* KPI Cards: Collaborative Uplift & Centralization Gap */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        <div className="p-3.5 rounded-xl bg-slate-900/60 border border-emerald-500/20 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">Collaborative Uplift</span>
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
          </div>
          <div className="mt-2">
            <span className="text-2xl font-bold font-mono text-emerald-400">
              +{silo_deficit_analysis.collaborative_uplift_pr_auc.toFixed(4)}
            </span>
            <span className="text-xs text-emerald-300 font-mono ml-1.5">Δ PR-AUC</span>
          </div>
          <p className="text-[11px] text-slate-400 mt-1">
            Uplift over {silo_deficit_analysis.silo_count}-bank isolated silo average
          </p>
        </div>

        <div className="p-3.5 rounded-xl bg-slate-900/60 border border-indigo-500/20 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">Federated Efficiency</span>
            <span className="w-2 h-2 rounded-full bg-indigo-400" />
          </div>
          <div className="mt-2">
            <span className="text-2xl font-bold font-mono text-indigo-300">
              {centralization_gap_analysis.federated_efficiency_pct.toFixed(1)}%
            </span>
            <span className="text-xs text-slate-400 font-mono ml-1.5">of Upper Bound</span>
          </div>
          <p className="text-[11px] text-slate-400 mt-1">
            Retained without raw financial data pooling
          </p>
        </div>

        <div className="p-3.5 rounded-xl bg-slate-900/60 border border-amber-500/20 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">Centralization Gap</span>
            <span className="w-2 h-2 rounded-full bg-amber-400" />
          </div>
          <div className="mt-2">
            <span className="text-2xl font-bold font-mono text-amber-300">
              -{centralization_gap_analysis.centralization_gap_pr_auc.toFixed(4)}
            </span>
            <span className="text-xs text-amber-400/80 font-mono ml-1.5">Δ PR-AUC</span>
          </div>
          <p className="text-[11px] text-slate-400 mt-1">
            Privacy cost vs illegal data centralization
          </p>
        </div>

        <div className="p-3.5 rounded-xl bg-slate-900/60 border border-cyan-500/20 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">Operational Recall</span>
            <span className="w-2 h-2 rounded-full bg-cyan-400" />
          </div>
          <div className="mt-2">
            <span className="text-2xl font-bold font-mono text-cyan-300">
              {(comparison_matrix.find((m) => m.category === 'PRODUCTION_CHAMPION')?.recall_at_01_fpr ?? 0 * 100).toFixed(1)}%
            </span>
            <span className="text-xs text-slate-400 font-mono ml-1.5">@ 0.1% FPR</span>
          </div>
          <p className="text-[11px] text-slate-400 mt-1">
            High-precision fraud capture rate
          </p>
        </div>
      </div>

      {/* Mobile View: Cards */}
      <div className="block md:hidden space-y-3">
        {comparison_matrix.map((item, idx) => (
          <div
            key={idx}
            className={`p-3.5 rounded-xl border font-mono text-xs space-y-2 ${
              item.category === 'PRODUCTION_CHAMPION'
                ? 'bg-emerald-950/20 border-emerald-500/40'
                : 'bg-slate-900/60 border-white/5'
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <span className="font-bold text-slate-200">{item.paradigm}</span>
              {getCategoryBadge(item.category)}
            </div>
            <div className="grid grid-cols-3 gap-2 border-t border-white/5 pt-2 text-center">
              <div>
                <span className="text-[10px] text-slate-500 block">PR-AUC</span>
                <span className="font-bold text-slate-200">{item.pr_auc.toFixed(4)}</span>
              </div>
              <div>
                <span className="text-[10px] text-slate-500 block">ROC-AUC</span>
                <span className="font-bold text-slate-200">{item.roc_auc.toFixed(4)}</span>
              </div>
              <div>
                <span className="text-[10px] text-slate-500 block">Recall@0.1%</span>
                <span className="font-bold text-slate-200">{(item.recall_at_01_fpr * 100).toFixed(1)}%</span>
              </div>
            </div>
            <div className="border-t border-white/5 pt-2 text-[10px] text-slate-400 flex items-center justify-between">
              <span>Latency: {item.latency_ms.toFixed(3)} ms/tx</span>
              <span className="text-slate-500">{item.privacy_guarantee}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Desktop View: Full Side-by-Side Table with Bars */}
      <div className="hidden md:block overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="border-b border-white/10 text-slate-400 font-mono text-[11px]">
              <th className="py-2.5 px-3">Evaluation Paradigm</th>
              <th className="py-2.5 px-3">Classification</th>
              <th className="py-2.5 px-3 w-48">{metricLabels[selectedMetric]}</th>
              <th className="py-2.5 px-3 text-center">PR-AUC</th>
              <th className="py-2.5 px-3 text-center">ROC-AUC</th>
              <th className="py-2.5 px-3 text-center">Recall@0.1%</th>
              <th className="py-2.5 px-3 text-center">Δ vs Fed</th>
              <th className="py-2.5 px-3 text-right">Inference Latency</th>
            </tr>
          </thead>
          <tbody>
            <AnimatePresence>
              {comparison_matrix.map((item, idx) => {
                const metricVal = item[selectedMetric];
                const isChampion = item.category === 'PRODUCTION_CHAMPION';
                const isUpper = item.category === 'THEORETICAL_UPPER_BOUND';

                return (
                  <tr
                    key={idx}
                    className={`border-b border-white/5 transition-colors font-mono text-[11px] ${
                      isChampion
                        ? 'bg-emerald-500/5 hover:bg-emerald-500/10'
                        : isUpper
                        ? 'bg-rose-500/3 hover:bg-rose-500/5'
                        : 'hover:bg-white/3'
                    }`}
                  >
                    <td className="py-2.5 px-3">
                      <span className={`font-semibold ${isChampion ? 'text-emerald-300' : 'text-slate-200'}`}>
                        {item.paradigm}
                      </span>
                      <span className="block text-[10px] text-slate-500 font-sans truncate max-w-xs">
                        {item.description}
                      </span>
                    </td>
                    <td className="py-2.5 px-3">{getCategoryBadge(item.category)}</td>
                    <td className="py-2.5 px-3">
                      <div className="flex items-center gap-2">
                        <div className="w-28 bg-slate-800 rounded-full h-2 overflow-hidden shrink-0">
                          <div
                            className={`h-full rounded-full ${
                              isChampion
                                ? 'bg-gradient-to-r from-emerald-500 to-teal-400'
                                : isUpper
                                ? 'bg-gradient-to-r from-rose-500 to-pink-400'
                                : 'bg-gradient-to-r from-indigo-500 to-blue-400'
                            }`}
                            style={{ width: `${Math.min(100, Math.max(0, metricVal * 100))}%` }}
                          />
                        </div>
                        <span className="text-[11px] font-bold text-slate-300">
                          {selectedMetric === 'recall_at_01_fpr'
                            ? `${(metricVal * 100).toFixed(1)}%`
                            : metricVal.toFixed(4)}
                        </span>
                      </div>
                    </td>
                    <td className="py-2.5 px-3 text-center text-slate-300">{item.pr_auc.toFixed(4)}</td>
                    <td className="py-2.5 px-3 text-center text-slate-300">{item.roc_auc.toFixed(4)}</td>
                    <td className="py-2.5 px-3 text-center text-slate-300">
                      {(item.recall_at_01_fpr * 100).toFixed(1)}%
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      {item.delta_pr_auc_vs_fed === 0 ? (
                        <span className="text-emerald-400 font-bold">Baseline</span>
                      ) : item.delta_pr_auc_vs_fed > 0 ? (
                        <span className="text-rose-400 font-semibold">+{item.delta_pr_auc_vs_fed.toFixed(4)}</span>
                      ) : (
                        <span className="text-amber-400 font-semibold">{item.delta_pr_auc_vs_fed.toFixed(4)}</span>
                      )}
                    </td>
                    <td className="py-2.5 px-3 text-right text-slate-400">{item.latency_ms.toFixed(3)} ms</td>
                  </tr>
                );
              })}
            </AnimatePresence>
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}
