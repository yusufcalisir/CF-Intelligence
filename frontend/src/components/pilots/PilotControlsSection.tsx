import { useState } from 'react';
import type { PilotStatusResponse } from '../../api/types';
import {
  useActivatePilot,
  usePausePilot,
  useResumePilot,
  useAbortPilot,
  useCompletePilot,
} from '../../api/pilots';
import { formatApiError } from '../../api/client';
import PilotActionModal from './PilotActionModal';
import {
  Play,
  Pause,
  RotateCcw,
  CheckCircle,
  Ban,
  ShieldCheck,
  ShieldAlert,
  Info,
} from 'lucide-react';

interface PilotControlsSectionProps {
  pilot: PilotStatusResponse;
}

export default function PilotControlsSection({ pilot }: PilotControlsSectionProps) {
  const [modalAction, setModalAction] = useState<'pause' | 'resume' | 'abort' | 'complete' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const activateMutation = useActivatePilot();
  const pauseMutation = usePausePilot();
  const resumeMutation = useResumePilot();
  const abortMutation = useAbortPilot();
  const completeMutation = useCompletePilot();

  const handleActivate = async () => {
    setActionError(null);
    try {
      await activateMutation.mutateAsync({ pilotId: pilot.pilot_id });
    } catch (err) {
      setActionError(formatApiError(err, 'Failed to activate pilot enclave.'));
    }
  };

  const handleModalConfirm = async (reason: string) => {
    setActionError(null);
    try {
      if (modalAction === 'pause') {
        await pauseMutation.mutateAsync({ pilotId: pilot.pilot_id, payload: { reason } });
      } else if (modalAction === 'resume') {
        await resumeMutation.mutateAsync({ pilotId: pilot.pilot_id });
      } else if (modalAction === 'abort') {
        await abortMutation.mutateAsync({ pilotId: pilot.pilot_id, payload: { reason } });
      } else if (modalAction === 'complete') {
        await completeMutation.mutateAsync({ pilotId: pilot.pilot_id });
      }
    } catch (err) {
      setActionError(formatApiError(err, `Failed to execute ${modalAction} action.`));
      throw err;
    }
  };

  const isTerminal = ['COMPLETED', 'ABORTED', 'FAILED'].includes(pilot.status);
  const isActive = pilot.status === 'ACTIVE';
  const isPaused = pilot.status === 'PAUSED';
  const isAuthorized = pilot.status === 'AUTHORIZED';

  const canActivate = (pilot.permitted_actions?.includes('activate') ?? isAuthorized) && !pilot.has_expired;
  const canPause = (pilot.permitted_actions?.includes('pause') ?? isActive);
  const canResume = (pilot.permitted_actions?.includes('resume') ?? isPaused) && !pilot.has_expired;
  const canAbort = (pilot.permitted_actions?.includes('abort') ?? !isTerminal);
  const canComplete = (pilot.permitted_actions?.includes('complete') ?? (isActive || isPaused));

  const isAnyActionPending =
    activateMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    abortMutation.isPending ||
    completeMutation.isPending;

  return (
    <div className="space-y-6">
      {/* Current Operational Gate Banner */}
      <div
        className={`p-5 rounded-xl border flex items-start gap-3.5 ${
          isActive
            ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-200'
            : isPaused
            ? 'border-amber-500/40 bg-amber-950/20 text-amber-200'
            : isTerminal
            ? 'border-slate-700 bg-slate-900/50 text-slate-400'
            : 'border-cyan-500/40 bg-cyan-950/20 text-cyan-200'
        }`}
      >
        {isActive ? (
          <ShieldCheck className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
        ) : isPaused ? (
          <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
        ) : (
          <Info className="w-5 h-5 text-cyan-400 shrink-0 mt-0.5" />
        )}
        <div className="space-y-1 text-xs">
          <div className="font-bold text-sm tracking-wide">
            {isActive && 'Operational Queries ENABLED - Active Enclave'}
            {isPaused && 'Operational Queries BLOCKED - Emergency Pause Active'}
            {isAuthorized && 'Pilot Fully Authorized - Awaiting Operator Activation'}
            {isTerminal && `Pilot Execution Concluded (${pilot.status})`}
            {!isActive && !isPaused && !isAuthorized && !isTerminal && 'Pilot Provisioning & Verification In-Progress'}
          </div>
          <p className="text-slate-300">
            {isActive &&
              'Live PSI queries against participating institutions are permitted subject to rate limits and query quota reservations.'}
            {isPaused &&
              'The kill switch is engaged. All matching requests are rejected immediately; no new quota is consumed.'}
            {isAuthorized &&
              'Independent bilateral approvals and preflight checks have passed. Authorized operators may activate the pilot.'}
            {isTerminal &&
              `Pilot lifecycle reached terminal outcome: ${pilot.status_reason || 'Normal completion or termination'}.`}
          </p>
        </div>
      </div>

      {actionError && (
        <div className="p-4 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs flex items-start gap-2.5">
          <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold">Action Execution Failed</div>
            <div className="mt-0.5">{actionError}</div>
          </div>
        </div>
      )}

      {/* Action Controls Grid */}
      <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-4">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
            Governance & Kill Switch Actions
          </h4>
          <p className="text-xs text-slate-400 mt-0.5">
            Actions adhere to domain transition rules (<code className="text-slate-300 font-mono text-[11px]">VALID_PILOT_TRANSITIONS</code>) and fail closed on unauthorized or stale calls.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 pt-2">
          {/* Activate */}
          <button
            onClick={handleActivate}
            disabled={!canActivate || isAnyActionPending}
            className={`p-3.5 rounded-xl border text-xs font-semibold flex flex-col items-center justify-center gap-2 transition-all ${
              canActivate && !isAnyActionPending
                ? 'border-cyan-500/50 bg-cyan-950/30 hover:bg-cyan-900/40 text-cyan-200 cursor-pointer shadow-sm hover:scale-[1.02]'
                : 'border-slate-800 bg-slate-900/40 text-slate-600 cursor-not-allowed opacity-60'
            }`}
          >
            <Play className="w-5 h-5 text-cyan-400" />
            <span>Activate Pilot</span>
            <span className="text-[10px] font-normal text-slate-400 text-center">
              {isAuthorized ? 'Operator Trigger' : 'Requires AUTHORIZED state'}
            </span>
          </button>

          {/* Pause */}
          <button
            onClick={() => setModalAction('pause')}
            disabled={!canPause || isAnyActionPending}
            className={`p-3.5 rounded-xl border text-xs font-semibold flex flex-col items-center justify-center gap-2 transition-all ${
              canPause && !isAnyActionPending
                ? 'border-amber-500/50 bg-amber-950/30 hover:bg-amber-900/40 text-amber-200 cursor-pointer shadow-sm hover:scale-[1.02]'
                : 'border-slate-800 bg-slate-900/40 text-slate-600 cursor-not-allowed opacity-60'
            }`}
          >
            <Pause className="w-5 h-5 text-amber-400" />
            <span>Emergency Pause</span>
            <span className="text-[10px] font-normal text-slate-400 text-center">
              {isActive ? 'Instant Kill Switch' : 'Requires ACTIVE state'}
            </span>
          </button>

          {/* Resume */}
          <button
            onClick={() => setModalAction('resume')}
            disabled={!canResume || isAnyActionPending}
            className={`p-3.5 rounded-xl border text-xs font-semibold flex flex-col items-center justify-center gap-2 transition-all ${
              canResume && !isAnyActionPending
                ? 'border-emerald-500/50 bg-emerald-950/30 hover:bg-emerald-900/40 text-emerald-200 cursor-pointer shadow-sm hover:scale-[1.02]'
                : 'border-slate-800 bg-slate-900/40 text-slate-600 cursor-not-allowed opacity-60'
            }`}
          >
            <RotateCcw className="w-5 h-5 text-emerald-400" />
            <span>Resume Pilot</span>
            <span className="text-[10px] font-normal text-slate-400 text-center">
              {isPaused ? 'Re-validates Readiness' : 'Requires PAUSED state'}
            </span>
          </button>

          {/* Complete */}
          <button
            onClick={() => setModalAction('complete')}
            disabled={!canComplete || isAnyActionPending}
            className={`p-3.5 rounded-xl border text-xs font-semibold flex flex-col items-center justify-center gap-2 transition-all ${
              canComplete && !isAnyActionPending
                ? 'border-teal-500/50 bg-teal-950/30 hover:bg-teal-900/40 text-teal-200 cursor-pointer shadow-sm hover:scale-[1.02]'
                : 'border-slate-800 bg-slate-900/40 text-slate-600 cursor-not-allowed opacity-60'
            }`}
          >
            <CheckCircle className="w-5 h-5 text-teal-400" />
            <span>Complete Pilot</span>
            <span className="text-[10px] font-normal text-slate-400 text-center">
              {isActive || isPaused ? 'Normal Conclusion' : 'Active or Paused'}
            </span>
          </button>

          {/* Abort */}
          <button
            onClick={() => setModalAction('abort')}
            disabled={!canAbort || isAnyActionPending}
            className={`p-3.5 rounded-xl border text-xs font-semibold flex flex-col items-center justify-center gap-2 transition-all ${
              canAbort && !isAnyActionPending
                ? 'border-rose-500/50 bg-rose-950/30 hover:bg-rose-900/40 text-rose-200 cursor-pointer shadow-sm hover:scale-[1.02]'
                : 'border-slate-800 bg-slate-900/40 text-slate-600 cursor-not-allowed opacity-60'
            }`}
          >
            <Ban className="w-5 h-5 text-rose-400" />
            <span>Emergency Abort</span>
            <span className="text-[10px] font-normal text-slate-400 text-center">
              {!isTerminal ? 'Permanent Termination' : 'Already Terminal'}
            </span>
          </button>
        </div>

        {/* Action eligibility feedback for disabled actions */}
        <div className="pt-2 text-[11px] text-slate-400 space-y-1">
          {pilot.missing_approvals && pilot.missing_approvals.length > 0 && !pilot.is_fully_approved && (
            <div className="text-amber-400">
              • Activation blocked: Missing approvals from {pilot.missing_approvals.join(', ')}.
            </div>
          )}
          {pilot.has_expired && (
            <div className="text-rose-400">• Pilot operational window has expired. Transitions to active execution are forbidden.</div>
          )}
        </div>
      </div>

      {/* Confirmation & Rationale Modal */}
      <PilotActionModal
        isOpen={modalAction !== null}
        actionType={modalAction}
        pilotId={pilot.pilot_id}
        isPending={isAnyActionPending}
        onClose={() => setModalAction(null)}
        onConfirm={handleModalConfirm}
      />
    </div>
  );
}
