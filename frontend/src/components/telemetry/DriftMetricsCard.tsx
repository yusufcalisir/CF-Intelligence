/**
 * DriftMetricsCard — Empirical Concept & Feature Drift Profiling Telemetry Card
 *
 * Visualizes multi-period statistical drift metrics:
 *   - Population Stability Index (PSI) across features and concept predictions
 *   - Kolmogorov-Smirnov 2-sample test statistics and FDR-corrected p-values
 *   - Normalized Wasserstein distance (Earth Mover's Distance)
 *   - Expected Calibration Error (ECE) and Brier Score
 *   - Automated retraining loop trigger integration with Celery / REST gateway
 */

import { useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import type { DriftAnalysisReport } from '../../api/types';
import { useTriggerAutoRetrain } from '../../api/queries';

export interface DriftMetricsCardProps {
  data?: DriftAnalysisReport | null;
  isLoading?: boolean;
  onTriggerRetrain?: (selectedFeatures: string[]) => Promise<void> | void;
  className?: string;
}

export default function DriftMetricsCard({
  data,
  isLoading = false,
  onTriggerRetrain,
  className = '',
}: DriftMetricsCardProps) {
  const triggerRetrainMutation = useTriggerAutoRetrain();
  const [selectedFeatures, setSelectedFeatures] = useState<string[]>([]);
  const [triggerSuccessMsg, setTriggerSuccessMsg] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const overallStatus = data?.overall_status?.toUpperCase() || 'HEALTHY';
  const isCritical = overallStatus === 'CRITICAL' || (data?.max_psi !== undefined && data.max_psi >= 0.20);
  const isWarning = overallStatus === 'WARNING' || (data?.max_psi !== undefined && data.max_psi >= 0.10 && !isCritical);

  const statusBadge = useMemo(() => {
    if (isCritical) {
      return {
        label: 'CRITICAL DRIFT',
        bg: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
        dot: 'bg-rose-400 animate-pulse',
      };
    }
    if (isWarning) {
      return {
        label: 'MODERATE DRIFT',
        bg: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
        dot: 'bg-amber-400',
      };
    }
    return {
      label: 'STABLE / HEALTHY',
      bg: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
      dot: 'bg-emerald-400',
    };
  }, [isCritical, isWarning]);

  const driftedFeatures = useMemo(() => {
    if (!data?.feature_drifts) return [];
    return data.feature_drifts.filter(
      (f) => f.psi >= 0.10 || f.status === 'SEVERE_DRIFT' || f.status === 'MODERATE_DRIFT'
    );
  }, [data]);

  const handleSelectAllDrifted = () => {
    const featureNames = driftedFeatures.map((f) => f.feature_name);
    setSelectedFeatures(featureNames);
  };

  const handleToggleFeature = (name: string) => {
    setSelectedFeatures((prev) =>
      prev.includes(name) ? prev.filter((f) => f !== name) : [...prev, name]
    );
  };

  const handleExecuteRetrain = async () => {
    setIsSubmitting(true);
    setTriggerSuccessMsg(null);
    try {
      const targets = selectedFeatures.length > 0 ? selectedFeatures : driftedFeatures.map((f) => f.feature_name);
      if (onTriggerRetrain) {
        await onTriggerRetrain(targets);
        setTriggerSuccessMsg(`Retraining dispatched for ${targets.length || 'all'} feature(s).`);
      } else {
        const res = await triggerRetrainMutation.mutateAsync({
          reason: `Automated Retraining triggered via DriftMetricsCard (Max PSI: ${data?.max_psi ?? 0.25})`,
          retrain_feature_subset: targets,
          max_psi: data?.max_psi ?? 0.25,
          target_simulation_rounds: 3,
          dispatch_alertmanager_webhook: true,
        });
        setTriggerSuccessMsg(`Retraining round started: ${res.new_simulation_id || 'Job Enqueued'}`);
      }
    } catch {
      setTriggerSuccessMsg('Retraining request failed to dispatch.');
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className={`p-6 rounded-2xl bg-slate-900/60 border border-slate-800 backdrop-blur-xl animate-pulse ${className}`}>
        <div className="h-6 w-64 bg-slate-800 rounded-md mb-4" />
        <div className="h-4 w-96 bg-slate-800/60 rounded-md mb-6" />
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-24 bg-slate-800/40 rounded-xl" />
          ))}
        </div>
        <div className="h-48 bg-slate-800/30 rounded-xl" />
      </div>
    );
  }

  return (
    <div
      data-testid="drift-metrics-card"
      className={`p-6 rounded-2xl bg-slate-900/80 border border-slate-800/90 shadow-2xl backdrop-blur-xl relative overflow-hidden ${className}`}
    >
      {/* Background radial glow */}
      <div
        className={`absolute -top-32 -right-32 w-80 h-80 rounded-full blur-3xl pointer-events-none transition-colors duration-700 ${
          isCritical ? 'bg-rose-500/10' : isWarning ? 'bg-amber-500/10' : 'bg-emerald-500/10'
        }`}
      />

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-5 border-b border-slate-800/80 relative z-10">
        <div>
          <div className="flex items-center gap-3">
            <h3 className="text-lg font-semibold text-slate-100 tracking-tight">
              Feature & Concept Drift Profiling
            </h3>
            <span
              data-testid="overall-status-badge"
              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border ${statusBadge.bg}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${statusBadge.dot}`} />
              {statusBadge.label}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Real-time Kolmogorov-Smirnov test, Wasserstein divergence, and Population Stability Index (PSI).
          </p>
        </div>

        {data?.evaluated_at && (
          <div className="text-xs text-slate-500 font-mono">
            Evaluated: {new Date(data.evaluated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </div>
        )}
      </div>

      {/* Top Metric KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 my-5 relative z-10">
        {/* KPI 1: Max Feature PSI */}
        <div className="p-4 rounded-xl bg-slate-800/50 border border-slate-700/50 flex flex-col justify-between">
          <div className="text-xs font-medium text-slate-400 flex items-center justify-between">
            <span>Peak Feature PSI</span>
            <span className="text-[10px] text-slate-500 font-mono">&gt;0.20 Crit</span>
          </div>
          <div className="my-2">
            <span
              data-testid="max-psi-value"
              className={`text-2xl font-bold font-mono tracking-tight ${
                isCritical ? 'text-rose-400' : isWarning ? 'text-amber-400' : 'text-emerald-400'
              }`}
            >
              {data?.max_psi !== undefined ? data.max_psi.toFixed(4) : '0.0000'}
            </span>
          </div>
          <div className="w-full bg-slate-700/50 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                isCritical ? 'bg-rose-500' : isWarning ? 'bg-amber-500' : 'bg-emerald-500'
              }`}
              style={{ width: `${Math.min(100, ((data?.max_psi ?? 0) / 0.30) * 100)}%` }}
            />
          </div>
        </div>

        {/* KPI 2: Concept Drift PSI */}
        <div className="p-4 rounded-xl bg-slate-800/50 border border-slate-700/50 flex flex-col justify-between">
          <div className="text-xs font-medium text-slate-400 flex items-center justify-between">
            <span>Concept Drift PSI</span>
            <span className="text-[10px] text-slate-500 font-mono">Predictions</span>
          </div>
          <div className="my-2">
            <span
              data-testid="concept-psi-value"
              className={`text-2xl font-bold font-mono tracking-tight ${
                (data?.concept_drift_psi ?? 0) >= 0.20
                  ? 'text-rose-400'
                  : (data?.concept_drift_psi ?? 0) >= 0.10
                  ? 'text-amber-400'
                  : 'text-slate-200'
              }`}
            >
              {data?.concept_drift_psi !== undefined ? data.concept_drift_psi.toFixed(4) : '0.0000'}
            </span>
          </div>
          <div className="text-[11px] text-slate-400">
            {(data?.concept_drift_psi ?? 0) >= 0.20
              ? 'Severe risk score shift'
              : (data?.concept_drift_psi ?? 0) >= 0.10
              ? 'Moderate score divergence'
              : 'Consistent score distribution'}
          </div>
        </div>

        {/* KPI 3: Mean KS p-value */}
        <div className="p-4 rounded-xl bg-slate-800/50 border border-slate-700/50 flex flex-col justify-between">
          <div className="text-xs font-medium text-slate-400 flex items-center justify-between">
            <span>Mean KS p-value</span>
            <span className="text-[10px] text-slate-500 font-mono">FDR α=0.05</span>
          </div>
          <div className="my-2">
            <span
              data-testid="ks-pvalue-value"
              className={`text-2xl font-bold font-mono tracking-tight ${
                (data?.mean_ks_p_value ?? 1.0) < 0.01
                  ? 'text-rose-400'
                  : (data?.mean_ks_p_value ?? 1.0) < 0.05
                  ? 'text-amber-400'
                  : 'text-emerald-400'
              }`}
            >
              {data?.mean_ks_p_value !== undefined ? data.mean_ks_p_value.toFixed(4) : '1.0000'}
            </span>
          </div>
          <div className="text-[11px] text-slate-400">
            {(data?.mean_ks_p_value ?? 1.0) < 0.05 ? 'Significant divergence detected' : 'Null hypothesis retained'}
          </div>
        </div>

        {/* KPI 4: Retraining State */}
        <div className="p-4 rounded-xl bg-slate-800/50 border border-slate-700/50 flex flex-col justify-between">
          <div className="text-xs font-medium text-slate-400 flex items-center justify-between">
            <span>Retraining Loop</span>
            <span className="text-[10px] text-slate-500 font-mono">Celery Trigger</span>
          </div>
          <div className="my-2">
            <span
              data-testid="retrain-status-text"
              className={`text-base font-semibold tracking-tight ${
                data?.auto_retrain_triggered ? 'text-rose-400 flex items-center gap-1.5' : 'text-slate-300'
              }`}
            >
              {data?.auto_retrain_triggered ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-rose-400 animate-ping inline-block" />
                  TRIGGERED
                </>
              ) : (
                'STANDBY'
              )}
            </span>
          </div>
          <div className="text-[11px] text-slate-400">
            {data?.auto_retrain_triggered ? 'Quality gate evaluation queued' : 'Baseline accuracy preserved'}
          </div>
        </div>
      </div>

      {/* Feature Drift Detailed Table */}
      <div className="mt-6 relative z-10">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Feature Divergence Vector Breakdown
          </h4>
          {driftedFeatures.length > 0 && (
            <button
              type="button"
              onClick={handleSelectAllDrifted}
              className="text-xs text-indigo-400 hover:text-indigo-300 underline underline-offset-2 transition-colors"
            >
              Select All Drifted ({driftedFeatures.length})
            </button>
          )}
        </div>

        <div className="overflow-x-auto rounded-xl border border-slate-800/80 bg-slate-950/40">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-800/60 text-slate-400 font-mono uppercase text-[11px] border-b border-slate-800">
              <tr>
                <th className="py-2.5 px-3 w-8">Sel</th>
                <th className="py-2.5 px-3">Feature Identifier</th>
                <th className="py-2.5 px-3">KS Statistic (D)</th>
                <th className="py-2.5 px-3">KS p-value</th>
                <th className="py-2.5 px-3">Wasserstein (EMD/σ)</th>
                <th className="py-2.5 px-3">PSI Divergence</th>
                <th className="py-2.5 px-3">Drift Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono text-slate-300">
              {data?.feature_drifts && data.feature_drifts.length > 0 ? (
                data.feature_drifts.map((feat) => {
                  const isSelected = selectedFeatures.includes(feat.feature_name);
                  const isFeatSevere = feat.status === 'SEVERE_DRIFT' || feat.psi >= 0.20;
                  const isFeatModerate = feat.status === 'MODERATE_DRIFT' || feat.psi >= 0.10;

                  return (
                    <tr
                      key={feat.feature_name}
                      data-testid={`feature-row-${feat.feature_name}`}
                      className={`hover:bg-slate-800/40 transition-colors ${
                        isSelected ? 'bg-indigo-500/5' : ''
                      }`}
                    >
                      <td className="py-2.5 px-3">
                        <input
                          type="checkbox"
                          aria-label={`Select ${feat.feature_name}`}
                          checked={isSelected}
                          onChange={() => handleToggleFeature(feat.feature_name)}
                          className="rounded border-slate-700 bg-slate-800 text-indigo-600 focus:ring-indigo-500 focus:ring-offset-slate-900"
                        />
                      </td>
                      <td className="py-2.5 px-3 font-sans font-medium text-slate-200">
                        {feat.feature_name}
                      </td>
                      <td className="py-2.5 px-3">{feat.ks_statistic.toFixed(4)}</td>
                      <td className="py-2.5 px-3">
                        <span className={feat.ks_p_value < 0.05 ? 'text-amber-400 font-semibold' : ''}>
                          {feat.ks_p_value.toFixed(4)}
                        </span>
                      </td>
                      <td className="py-2.5 px-3">{feat.wasserstein_distance.toFixed(4)}</td>
                      <td className="py-2.5 px-3">
                        <div className="flex items-center gap-2">
                          <span
                            className={
                              isFeatSevere
                                ? 'text-rose-400 font-bold'
                                : isFeatModerate
                                ? 'text-amber-400 font-semibold'
                                : 'text-slate-300'
                            }
                          >
                            {feat.psi.toFixed(4)}
                          </span>
                          <div className="w-16 bg-slate-800 rounded-full h-1 overflow-hidden hidden sm:block">
                            <div
                              className={`h-full rounded-full ${
                                isFeatSevere ? 'bg-rose-500' : isFeatModerate ? 'bg-amber-500' : 'bg-emerald-500'
                              }`}
                              style={{ width: `${Math.min(100, (feat.psi / 0.25) * 100)}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="py-2.5 px-3 font-sans">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded text-[10px] font-semibold tracking-wide ${
                            isFeatSevere
                              ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                              : isFeatModerate
                              ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                              : 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/20'
                          }`}
                        >
                          {feat.status}
                        </span>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-slate-500 font-sans">
                    No active feature drift metrics recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Retraining Action Bar */}
      <div className="mt-6 pt-5 border-t border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-4 relative z-10">
        <div className="text-xs text-slate-400 text-center sm:text-left">
          {selectedFeatures.length > 0 ? (
            <span data-testid="targeted-features-summary">
              <strong className="text-slate-200">{selectedFeatures.length}</strong> feature(s) targeted for retraining.
            </span>
          ) : isCritical ? (
            <span className="text-rose-400 font-medium">
              Critical drift threshold exceeded. Automated retraining dispatch recommended.
            </span>
          ) : (
            <span>Manual or automated retraining triggers update the federated model parameters.</span>
          )}
        </div>

        <div className="flex items-center gap-3">
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            type="button"
            data-testid="trigger-retrain-btn"
            disabled={isSubmitting || triggerRetrainMutation.isPending}
            onClick={handleExecuteRetrain}
            className={`px-4 py-2 rounded-xl text-xs font-semibold tracking-wide transition-all shadow-lg flex items-center gap-2 ${
              isCritical
                ? 'bg-rose-600 hover:bg-rose-500 text-white shadow-rose-900/40'
                : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-indigo-900/40'
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            {isSubmitting || triggerRetrainMutation.isPending ? (
              <>
                <svg className="animate-spin h-3.5 w-3.5 text-white" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                <span>Dispatching Retraining...</span>
              </>
            ) : (
              <span>Trigger Automated Retraining</span>
            )}
          </motion.button>
        </div>
      </div>

      {/* Feedback banner */}
      {triggerSuccessMsg && (
        <div
          data-testid="retrain-feedback-msg"
          className="mt-3 p-3 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 text-xs font-mono"
        >
          {triggerSuccessMsg}
        </div>
      )}
    </div>
  );
}
