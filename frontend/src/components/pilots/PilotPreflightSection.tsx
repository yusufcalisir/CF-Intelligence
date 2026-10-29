import { useState } from 'react';
import type { PilotStatusResponse } from '../../api/types';
import { useRunPilotPreflight } from '../../api/pilots';
import { formatApiError } from '../../api/client';
import { CheckCircle2, XCircle, AlertCircle, RefreshCw, ShieldCheck, Clock } from 'lucide-react';

interface PilotPreflightSectionProps {
  pilot: PilotStatusResponse;
}

export default function PilotPreflightSection({ pilot }: PilotPreflightSectionProps) {
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const runPreflightMutation = useRunPilotPreflight();

  const handleRunPreflight = async () => {
    setErrorMsg(null);
    try {
      await runPreflightMutation.mutateAsync({ pilotId: pilot.pilot_id });
    } catch (err) {
      setErrorMsg(formatApiError(err, 'Failed to execute preflight readiness verification.'));
    }
  };

  const checks = pilot.preflight_checks || [];
  const hasChecks = checks.length > 0;
  const allPassed = hasChecks && checks.every((c) => c.passed);
  const hasFailures = hasChecks && checks.some((c) => !c.passed);

  const canRunPreflight =
    pilot.permitted_actions?.includes('preflight') ??
    ['DRAFT', 'PREFLIGHT', 'AWAITING_APPROVAL'].includes(pilot.status);

  return (
    <div className="space-y-6">
      {/* Top Preflight Summary Card */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-indigo-400" />
            <h3 className="text-sm font-semibold text-white tracking-wide">
              Authoritative Preflight Verification Probes
            </h3>
          </div>
          <p className="text-xs text-slate-400 max-w-2xl">
            Preflight evaluates container connectivity, bilateral Ed25519 public key registries, active consortium grants,
            and query quota reserves via SSRF-safe backend probes before institutional activation.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleRunPreflight}
            disabled={!canRunPreflight || runPreflightMutation.isPending}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold transition-all shadow-sm ${
              canRunPreflight && !runPreflightMutation.isPending
                ? 'bg-indigo-600 hover:bg-indigo-500 text-white cursor-pointer'
                : 'bg-slate-800 text-slate-500 border border-slate-700/50 cursor-not-allowed'
            }`}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${runPreflightMutation.isPending ? 'animate-spin' : ''}`} />
            {runPreflightMutation.isPending ? 'Running Probes...' : 'Run Preflight Verification'}
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="p-4 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs flex items-start gap-2.5">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold">Preflight Verification Error</div>
            <div className="mt-0.5">{errorMsg}</div>
          </div>
        </div>
      )}

      {/* Preflight State Overview Banner */}
      {!hasChecks && (
        <div className="p-4 rounded-xl border border-slate-700/60 bg-slate-900/40 text-slate-400 text-xs flex items-center gap-3">
          <Clock className="w-5 h-5 text-slate-500 shrink-0" />
          <div>
            <div className="font-semibold text-slate-300">Preflight Probes Never Executed</div>
            <div>Run preflight verification to evaluate bilateral node connectivity, signing keys, and grant validity.</div>
          </div>
        </div>
      )}

      {hasChecks && allPassed && (
        <div className="p-4 rounded-xl border border-emerald-500/40 bg-emerald-950/20 text-emerald-300 text-xs flex items-center gap-3">
          <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
          <div>
            <div className="font-semibold">All Preflight Readiness Probes Passed</div>
            <div className="text-slate-400 mt-0.5">
              Infrastructure topology, institutional cryptographic keys, and consortium grants verified operational.
            </div>
          </div>
        </div>
      )}

      {hasChecks && hasFailures && (
        <div className="p-4 rounded-xl border border-rose-500/40 bg-rose-950/20 text-rose-300 text-xs flex items-center gap-3">
          <XCircle className="w-5 h-5 text-rose-400 shrink-0" />
          <div>
            <div className="font-semibold">Preflight Verification Failed</div>
            <div className="text-slate-400 mt-0.5">
              One or more mandatory infrastructure preconditions failed. Pilot activation is blocked until corrected.
            </div>
          </div>
        </div>
      )}

      {/* Check Items List */}
      {hasChecks && (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] overflow-hidden">
          <div className="p-4 border-b border-[var(--color-border)] flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
              Verification Check Results ({checks.filter((c) => c.passed).length}/{checks.length} Passed)
            </span>
          </div>

          <div className="divide-y divide-[var(--color-border)]">
            {checks.map((check, idx) => (
              <div key={idx} className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 hover:bg-slate-900/30 transition-colors">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    {check.passed ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                    ) : (
                      <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
                    )}
                    <span className="text-xs font-mono font-semibold text-white">
                      {check.check_name}
                    </span>
                    <span
                      className={`text-[10px] font-mono px-2 py-0.5 rounded ${
                        check.passed
                          ? 'bg-emerald-950/50 text-emerald-300 border border-emerald-800/40'
                          : 'bg-rose-950/50 text-rose-300 border border-rose-800/40'
                      }`}
                    >
                      {check.passed ? 'PASSED' : 'FAILED'}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 font-mono pl-6 break-words">
                    {check.details}
                  </p>
                </div>

                <div className="text-[11px] font-mono text-slate-500 pl-6 md:pl-0 shrink-0">
                  {check.checked_at ? new Date(check.checked_at).toLocaleTimeString() : 'Recent'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
