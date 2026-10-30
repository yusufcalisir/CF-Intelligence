import type { PilotStatusResponse } from '../../api/types';
import { usePilotTelemetry } from '../../api/pilots';
import { Activity, ShieldCheck, Database, AlertCircle, RefreshCw } from 'lucide-react';

interface PilotTelemetrySectionProps {
  pilot: PilotStatusResponse;
}

export default function PilotTelemetrySection({ pilot }: PilotTelemetrySectionProps) {
  const isTerminal = ['COMPLETED', 'ABORTED', 'FAILED'].includes(pilot.status);
  const { data: telemetry, isLoading, error, refetch, isFetching } = usePilotTelemetry(
    pilot.pilot_id,
    isTerminal
  );

  if (isLoading) {
    return (
      <div className="flex min-h-[300px] items-center justify-center rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="flex flex-col items-center gap-3">
          <div className="h-7 w-7 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
          <span className="text-xs font-mono text-slate-400">Loading authoritative telemetry...</span>
        </div>
      </div>
    );
  }

  if (error || !telemetry) {
    return (
      <div className="p-5 rounded-xl border border-rose-500/40 bg-rose-950/20 text-rose-300 text-xs space-y-2">
        <div className="flex items-center gap-2 font-semibold">
          <AlertCircle className="w-4 h-4 text-rose-400" />
          Telemetry Unavailable
        </div>
        <p className="text-slate-400">
          Failed to load live telemetry for pilot <code className="text-slate-300 font-mono">{pilot.pilot_id}</code>.
          Zero values will not be fabricated in place of server measurement.
        </p>
        <button
          onClick={() => refetch()}
          className="mt-2 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-white text-xs border border-slate-700 font-medium"
        >
          Retry Telemetry Poll
        </button>
      </div>
    );
  }

  const quotaUsedPct = Math.min(
    100,
    Math.round((telemetry.queries_executed / Math.max(1, telemetry.query_quota_total)) * 100)
  );

  const hasLatencySamples =
    telemetry.has_latency_observations ??
    ((telemetry.latency_sample_count ?? 0) > 0 || (telemetry.mean_latency_ms !== null && telemetry.mean_latency_ms > 0));

  return (
    <div className="space-y-6">
      {/* Telemetry Header Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-cyan-400" />
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Live Two-Bank Operational Telemetry
            </h3>
            <span className="text-[11px] text-slate-400 font-mono">
              Last operation: {telemetry.last_operation_at ? new Date(telemetry.last_operation_at).toLocaleTimeString() : 'None observed'}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono border ${
              telemetry.bilateral_authorization_valid
                ? 'bg-emerald-950/40 text-emerald-300 border-emerald-600/40'
                : 'bg-rose-950/40 text-rose-300 border-rose-600/40'
            }`}
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            {telemetry.bilateral_authorization_valid ? 'Grant Valid' : 'Grant Inactive'}
          </span>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors border border-slate-700 cursor-pointer"
            title="Refresh Telemetry"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin text-cyan-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* Telemetry KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {/* Executed Queries */}
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-1">
          <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Executed Queries</div>
          <div className="text-2xl font-mono font-bold text-emerald-400">{telemetry.queries_executed}</div>
          <div className="text-[10px] text-slate-500 font-mono">Successful PSI enclaves</div>
        </div>

        {/* Denied Queries */}
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-1">
          <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Denied Queries</div>
          <div className="text-2xl font-mono font-bold text-rose-400">{telemetry.queries_denied}</div>
          <div className="text-[10px] text-slate-500 font-mono">Policy/Quota blocks</div>
        </div>

        {/* Observed Mean Latency */}
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-1">
          <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Mean Latency</div>
          {hasLatencySamples && telemetry.mean_latency_ms !== null ? (
            <div className="text-2xl font-mono font-bold text-cyan-300">
              {telemetry.mean_latency_ms} <span className="text-xs font-normal text-slate-400">ms</span>
            </div>
          ) : (
            <div className="text-xs font-mono font-semibold text-slate-500 pt-1.5">
              Unavailable (0 samples)
            </div>
          )}
          <div className="text-[10px] text-slate-500 font-mono">
            {hasLatencySamples ? `Sample count: ${telemetry.latency_sample_count ?? 1}` : 'No queries executed'}
          </div>
        </div>

        {/* Observed p95 Latency */}
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-1">
          <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">p95 Latency</div>
          {hasLatencySamples && telemetry.p95_latency_ms !== null ? (
            <div className="text-2xl font-mono font-bold text-indigo-300">
              {telemetry.p95_latency_ms} <span className="text-xs font-normal text-slate-400">ms</span>
            </div>
          ) : (
            <div className="text-xs font-mono font-semibold text-slate-500 pt-1.5">
              Unavailable (0 samples)
            </div>
          )}
          <div className="text-[10px] text-slate-500 font-mono">95th percentile latency</div>
        </div>
      </div>

      {/* Quota Consumption Card */}
      <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-300">
            <Database className="w-4 h-4 text-amber-400" />
            Enclave Quota Reservation ({telemetry.queries_executed} / {telemetry.query_quota_total})
          </div>
          <span className="text-xs font-mono text-slate-400">
            Remaining: <strong className="text-white">{telemetry.quota_remaining}</strong>
          </span>
        </div>

        <div className="w-full bg-slate-800 rounded-full h-3 overflow-hidden">
          <div
            className={`h-3 rounded-full transition-all duration-300 ${
              quotaUsedPct >= 90 ? 'bg-rose-500' : quotaUsedPct >= 70 ? 'bg-amber-500' : 'bg-cyan-500'
            }`}
            style={{ width: `${quotaUsedPct}%` }}
          />
        </div>

        <div className="flex justify-between text-[11px] font-mono text-slate-400 pt-1">
          <span>0 queries</span>
          <span>{quotaUsedPct}% Consumed</span>
          <span>{telemetry.query_quota_total} max</span>
        </div>
      </div>

      {/* Per-Institution Activity Breakdown */}
      <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-3">
        <div className="text-xs font-semibold uppercase tracking-wider text-slate-300">
          Per-Institution Query Breakdown
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
          {Object.entries(telemetry.queries_by_institution || {}).map(([bank, count]) => (
            <div
              key={bank}
              className="p-3 rounded-lg border border-[var(--color-border)] bg-slate-900/60 flex items-center justify-between text-xs font-mono"
            >
              <span className="text-slate-300 uppercase">{bank}</span>
              <span className="text-cyan-300 font-bold">{count} queries</span>
            </div>
          ))}
          {Object.keys(telemetry.queries_by_institution || {}).length === 0 && (
            <div className="text-xs font-mono text-slate-500 italic col-span-2">
              No queries logged by individual institutions yet.
            </div>
          )}
        </div>
      </div>

      {/* Disclaimers & Boundary Note */}
      <div className="p-4 rounded-xl border border-slate-800 bg-slate-900/40 text-[11px] text-slate-400 space-y-1">
        <div className="font-semibold text-slate-300">Architectural Boundary:</div>
        <p>
          Telemetry displayed here reflects the governed two-bank pilot enclave.
          Multi-counterparty investigation batches and case evidence dossiers are handled under separate batch pipelines and are intentionally isolated from two-bank pilot metrics.
        </p>
      </div>
    </div>
  );
}
