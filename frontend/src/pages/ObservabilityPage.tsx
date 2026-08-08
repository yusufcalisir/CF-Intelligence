import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  useDriftAnalysis,
  useCalibrationReport,
  useActiveAlerts,
  useTriggerAutoRetrain,
} from '../api/queries';

export default function ObservabilityPage() {
  const [activeTab, setActiveTab] = useState<'drift' | 'calibration' | 'alerts' | 'telemetry'>('drift');
  const [simulatedSevereDrift, setSimulatedSevereDrift] = useState(false);

  const { data: driftData, isLoading: isDriftLoading } = useDriftAnalysis(simulatedSevereDrift);
  const { data: calibData, isLoading: isCalibLoading } = useCalibrationReport();
  const { data: alertsData, isLoading: isAlertsLoading } = useActiveAlerts();

  const triggerRetrain = useTriggerAutoRetrain();

  const handleRetrain = () => {
    triggerRetrain.mutate('Manual trigger from Observability Console: Drift PSI threshold exceeded');
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-[var(--color-text-primary)]">
            Enterprise Observability & Drift Monitoring
          </h1>
          <p className="text-xs sm:text-sm text-[var(--color-text-muted)] mt-1">
            Real-time Kolmogorov-Smirnov statistical feature drift, PSI concept drift, Brier calibration, and Prometheus Alertmanager
          </p>
        </div>

        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
          <label className="flex items-center gap-2 text-xs font-mono text-[var(--color-text-muted)] cursor-pointer whitespace-nowrap">
            <input
              type="checkbox"
              checked={simulatedSevereDrift}
              onChange={(e) => setSimulatedSevereDrift(e.target.checked)}
              className="rounded bg-[var(--color-surface-alt)] border-[var(--color-border)]"
            />
            Simulate Severe Drift (PSI &gt; 0.20)
          </label>

          <button
            onClick={handleRetrain}
            disabled={triggerRetrain.isPending}
            className="px-4 py-2 text-xs font-bold rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 shadow-md transition-all flex items-center justify-center gap-2 shrink-0 whitespace-nowrap"
          >
            {triggerRetrain.isPending ? 'Initiating FL Round...' : '🔄 Trigger Automated Re-training'}
          </button>
        </div>
      </div>

      {/* Retrain Trigger Notification Banner */}
      {triggerRetrain.data && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="p-4 rounded-xl border bg-indigo-500/10 border-indigo-500/30 text-indigo-400 flex flex-col sm:flex-row sm:items-center justify-between gap-2"
        >
          <div className="flex items-center gap-3">
            <span className="text-2xl shrink-0">⚡</span>
            <div>
              <div className="font-bold text-sm">Automated Federated Re-training Round Initiated</div>
              <div className="text-xs opacity-90">
                Simulation ID: <span className="font-mono">{triggerRetrain.data.new_simulation_id}</span> | Reason: {triggerRetrain.data.reason}
              </div>
            </div>
          </div>
          <span className="text-[10px] font-mono px-2 py-1 bg-black/30 rounded shrink-0 self-start sm:self-auto">
            {triggerRetrain.data.triggered_at}
          </span>
        </motion.div>
      )}

      {/* System Status Summary Banner */}
      {driftData && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="glass-card p-4 space-y-1">
            <div className="text-xs text-[var(--color-text-muted)]">Overall System Status</div>
            <div
              className={`text-lg font-bold font-mono ${
                driftData.overall_status === 'HEALTHY'
                  ? 'text-emerald-400'
                  : driftData.overall_status === 'WARNING'
                  ? 'text-amber-400'
                  : 'text-red-400'
              }`}
            >
              ● {driftData.overall_status}
            </div>
          </div>

          <div className="glass-card p-4 space-y-1">
            <div className="text-xs text-[var(--color-text-muted)]">Max Population Stability Index (PSI)</div>
            <div className="text-lg font-bold font-mono text-[var(--color-primary)]">
              {driftData.max_psi.toFixed(4)}
            </div>
          </div>

          <div className="glass-card p-4 space-y-1">
            <div className="text-xs text-[var(--color-text-muted)]">Concept Drift PSI (Risk Score)</div>
            <div className="text-lg font-bold font-mono text-indigo-400">
              {driftData.concept_drift_psi.toFixed(4)}
            </div>
          </div>

          <div className="glass-card p-4 space-y-1">
            <div className="text-xs text-[var(--color-text-muted)]">Mean KS Test p-value</div>
            <div className="text-lg font-bold font-mono">
              {driftData.mean_ks_p_value.toFixed(4)}
            </div>
          </div>
        </div>
      )}

      {/* 4-Tab Navigation */}
      <div className="flex overflow-x-auto border-b border-[var(--color-border)] text-xs sm:text-sm font-bold gap-4 sm:gap-6 pb-0.5">
        {[
          { id: 'drift', label: '📉 Model Drift Analytics (KS / PSI)' },
          { id: 'calibration', label: '🎯 Model Calibration Curve' },
          { id: 'alerts', label: '🚨 Prometheus Alertmanager' },
          { id: 'telemetry', label: '📊 Loki & OpenTelemetry Stack' },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as any)}
            className={`pb-3 shrink-0 whitespace-nowrap transition-all ${
              activeTab === tab.id
                ? 'text-[var(--color-primary)] border-b-2 border-[var(--color-primary)] font-bold'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab 1: Feature & Concept Drift Table */}
      {activeTab === 'drift' && (
        <div className="glass-card p-4 sm:p-5 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <h3 className="text-xs sm:text-sm font-bold uppercase text-[var(--color-text-muted)]">
              Statistical Feature Drift Breakdown (scipy.stats ks_2samp & wasserstein_distance)
            </h3>
            <span className="text-xs text-[var(--color-text-muted)] shrink-0">
              Evaluated: {driftData?.evaluated_at}
            </span>
          </div>

          {isDriftLoading ? (
            <div className="text-center py-8 text-[var(--color-text-muted)]">Running statistical drift tests...</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono min-w-[640px]">
                <thead>
                  <tr className="border-b border-[var(--color-border)] text-[var(--color-text-muted)]">
                    <th className="pb-2 pr-4 font-semibold">Feature Name</th>
                    <th className="pb-2 px-3 font-semibold">KS Statistic</th>
                    <th className="pb-2 px-3 font-semibold">KS p-value</th>
                    <th className="pb-2 px-3 font-semibold">Wasserstein Dist</th>
                    <th className="pb-2 px-3 font-semibold">PSI Index</th>
                    <th className="pb-2 pl-3 font-semibold">Drift Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-border)]">
                  {driftData?.feature_drifts.map((fd, i) => (
                    <tr key={i} className="hover:bg-[var(--color-surface-alt)]/50">
                      <td className="py-2.5 pr-4 font-semibold text-[var(--color-text-primary)] whitespace-nowrap">{fd.feature_name}</td>
                      <td className="py-2.5 px-3 whitespace-nowrap">{fd.ks_statistic.toFixed(4)}</td>
                      <td className="py-2.5 px-3 whitespace-nowrap">{fd.ks_p_value.toFixed(4)}</td>
                      <td className="py-2.5 px-3 whitespace-nowrap">{fd.wasserstein_distance.toFixed(4)}</td>
                      <td className="py-2.5 px-3 font-bold text-[var(--color-primary)] whitespace-nowrap">{fd.psi.toFixed(4)}</td>
                      <td className="py-2.5 pl-3 whitespace-nowrap">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-bold shrink-0 whitespace-nowrap ${
                            fd.status === 'STABLE'
                              ? 'bg-emerald-500/20 text-emerald-400'
                              : fd.status === 'MODERATE_DRIFT'
                              ? 'bg-amber-500/20 text-amber-400'
                              : 'bg-red-500/20 text-red-400'
                          }`}
                        >
                          {fd.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Tab 2: Model Calibration */}
      {activeTab === 'calibration' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="glass-card p-5 space-y-4 md:col-span-1">
            <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
              Calibration Summary
            </h3>
            {isCalibLoading ? (
              <div className="py-4 text-xs text-[var(--color-text-muted)]">Loading calibration...</div>
            ) : (
              <div className="space-y-3 text-xs">
                <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                  <span className="text-[var(--color-text-muted)]">Brier Score</span>
                  <span className="font-mono font-bold text-emerald-400">{calibData?.brier_score}</span>
                </div>
                <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                  <span className="text-[var(--color-text-muted)]">Expected Calibration Error (ECE)</span>
                  <span className="font-mono font-bold">{calibData?.expected_calibration_error}</span>
                </div>
                <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                  <span className="text-[var(--color-text-muted)]">Max Calibration Error</span>
                  <span className="font-mono font-bold">{calibData?.max_calibration_error}</span>
                </div>
                <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                  <span className="text-[var(--color-text-muted)]">Well Calibrated</span>
                  <span className="font-mono text-emerald-400 font-bold">
                    {calibData?.is_well_calibrated ? 'YES (Brier <= 0.15)' : 'NO (Degraded)'}
                  </span>
                </div>
              </div>
            )}
          </div>

          <div className="glass-card p-4 sm:p-5 space-y-4 md:col-span-2">
            <h3 className="text-xs sm:text-sm font-bold uppercase text-[var(--color-text-muted)]">
              Reliability Curve Bins (10-Bin Calibration)
            </h3>
            <div className="space-y-2 max-h-80 overflow-y-auto pr-1 overflow-x-auto">
              <div className="min-w-[540px] space-y-2">
                {calibData?.bins.map((bin) => (
                  <div
                    key={bin.bin_index}
                    className="p-2.5 rounded-lg bg-[var(--color-surface-alt)] text-xs grid grid-cols-12 items-center font-mono gap-2"
                  >
                    <div className="col-span-3 text-[var(--color-text-primary)] font-semibold truncate">
                      Bin #{bin.bin_index} [{bin.prob_min} - {bin.prob_max}]
                    </div>
                    <div className="col-span-3 text-left">
                      <span className="text-[var(--color-text-muted)] text-[11px]">Pred Prob: </span>
                      <strong className="text-[var(--color-primary)] font-bold">{bin.mean_predicted_prob}</strong>
                    </div>
                    <div className="col-span-3 text-left">
                      <span className="text-[var(--color-text-muted)] text-[11px]">Actual Ratio: </span>
                      <strong className="text-emerald-400 font-bold">{bin.empirical_fraud_ratio}</strong>
                    </div>
                    <div className="col-span-3 text-right text-[11px] text-[var(--color-text-muted)]">
                      ({bin.sample_count} samples)
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Tab 3: Prometheus Alertmanager */}
      {activeTab === 'alerts' && (
        <div className="glass-card p-4 sm:p-5 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <h3 className="text-xs sm:text-sm font-bold uppercase text-[var(--color-text-muted)]">
              Active Prometheus Alertmanager Feed
            </h3>
            <span className="text-xs font-mono text-[var(--color-text-muted)] shrink-0">Target: http://alertmanager:9093</span>
          </div>

          {isAlertsLoading ? (
            <div className="py-6 text-center text-xs text-[var(--color-text-muted)]">Fetching alert feed...</div>
          ) : (
            <div className="space-y-3">
              {alertsData?.map((alert, i) => (
                <div key={i} className="p-3 rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] flex items-start justify-between">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-sm text-[var(--color-text-primary)]">{alert.alert_name}</span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                          alert.severity === 'critical'
                            ? 'bg-red-500/20 text-red-400'
                            : alert.severity === 'warning'
                            ? 'bg-amber-500/20 text-amber-400'
                            : 'bg-blue-500/20 text-blue-400'
                        }`}
                      >
                        {alert.severity}
                      </span>
                    </div>
                    <p className="text-xs text-[var(--color-text-muted)]">{alert.summary}</p>
                  </div>

                  <div className="text-right font-mono text-[10px]">
                    <span className={`font-bold ${alert.status === 'firing' ? 'text-red-400' : 'text-emerald-400'}`}>
                      ● {alert.status.toUpperCase()}
                    </span>
                    <div className="text-[var(--color-text-muted)]">{alert.started_at}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab 4: Telemetry Links */}
      {activeTab === 'telemetry' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <a
            href={import.meta.env.VITE_GRAFANA_URL ?? 'https://curiousheather2678.grafana.net/d/cfi-overview/cfi-platform-overview'}
            target="_blank"
            rel="noreferrer"
            className="glass-card p-5 space-y-2 hover:border-[var(--color-primary)] transition-all block"
          >
            <div className="text-2xl">📈</div>
            <h4 className="font-bold text-sm">Grafana Dashboards</h4>
            <p className="text-xs text-[var(--color-text-muted)]">
              Unified visualization for Prometheus metrics, Loki logs, and Tempo traces.
            </p>
          </a>

          <a
            href={import.meta.env.VITE_LOKI_URL ?? 'https://curiousheather2678.grafana.net/explore'}
            target="_blank"
            rel="noreferrer"
            className="glass-card p-5 space-y-2 hover:border-[var(--color-primary)] transition-all block"
          >
            <div className="text-2xl">📜</div>
            <h4 className="font-bold text-sm">Grafana Loki Log Index</h4>
            <p className="text-xs text-[var(--color-text-muted)]">
              PLG log aggregation engine indexing structured JSON container log streams.
            </p>
          </a>

          <a
            href={import.meta.env.VITE_JAEGER_URL ?? 'https://curiousheather2678.grafana.net/explore'}
            target="_blank"
            rel="noreferrer"
            className="glass-card p-5 space-y-2 hover:border-[var(--color-primary)] transition-all block"
          >
            <div className="text-2xl">🔎</div>
            <h4 className="font-bold text-sm">Jaeger OTLP Traces</h4>
            <p className="text-xs text-[var(--color-text-muted)]">
              Distributed OpenTelemetry span traces across FL coordinator and microservices.
            </p>
          </a>
        </div>
      )}
    </div>
  );
}
