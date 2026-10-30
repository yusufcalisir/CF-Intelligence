import { useState, useEffect } from 'react';
import type { PilotStatusResponse } from '../../api/types';
import { useApprovePilot } from '../../api/pilots';
import { formatApiError } from '../../api/client';
import { CheckCircle2, Clock, AlertTriangle, Key, Send, ShieldAlert, ShieldCheck } from 'lucide-react';

interface PilotApprovalsSectionProps {
  pilot: PilotStatusResponse;
}

export default function PilotApprovalsSection({ pilot }: PilotApprovalsSectionProps) {
  const approvableOptions = pilot.approvable_institutions || [];
  const [approvingInstitution, setApprovingInstitution] = useState<string>(
    approvableOptions[0] || ''
  );
  const [notes, setNotes] = useState<string>('');
  const [signature, setSignature] = useState<string>('');
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    const firstOption = approvableOptions[0];
    if (firstOption && !approvableOptions.includes(approvingInstitution)) {
      setApprovingInstitution(firstOption);
    }
  }, [approvableOptions, approvingInstitution]);

  const approvePilotMutation = useApprovePilot();

  // Define required approval roles: all participating institutions + OPERATOR
  const requiredEntities = [...pilot.participating_institutions, 'OPERATOR'];

  const handleApprove = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    try {
      await approvePilotMutation.mutateAsync({
        pilotId: pilot.pilot_id,
        payload: {
          approving_institution: approvingInstitution,
          notes: notes.trim(),
          signature: signature.trim(),
        },
      });
      setNotes('');
      setSignature('');
    } catch (err) {
      setSubmitError(formatApiError(err, 'Failed to record institutional approval.'));
    }
  };

  const currentDigest = pilot.scope_digest;

  const canApprove =
    Boolean(pilot.permitted_actions?.includes('approve')) &&
    approvableOptions.length > 0 &&
    !pilot.has_expired;

  const getApprovalRecord = (entityId: string) => {
    const trimmed = entityId.trim();
    if (pilot.approvals[trimmed]) return pilot.approvals[trimmed];
    if (trimmed.toUpperCase() === 'OPERATOR') {
      return pilot.approvals['OPERATOR'] || pilot.approvals['operator'];
    }
    return pilot.approvals[trimmed.toLowerCase()] || pilot.approvals[trimmed.toUpperCase()];
  };

  const activeChallenge =
    pilot.canonical_signing_challenge && approvingInstitution === approvableOptions[0]
      ? pilot.canonical_signing_challenge
      : `PILOT_APPROVAL:${pilot.pilot_id}:${pilot.revision}:${currentDigest}:${(approvingInstitution || 'institution').toLowerCase()}:${pilot.caller_approver_id || '{actor_id}'}`;

  return (
    <div className="space-y-6">
      {/* Approvals Overview Banner */}
      <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Key className="w-5 h-5 text-purple-400" />
            <h3 className="text-sm font-semibold text-white tracking-wide">
              Independent Bilateral & Operator Approvals
            </h3>
          </div>
          <p className="text-xs text-slate-400 max-w-2xl">
            Controlled two-bank pilots require independent cryptographic authorization from each participating bank and the consortium operator.
            Approvals are strictly bound to current scope digest <code className="text-indigo-300 font-mono text-[11px]">{currentDigest ? currentDigest.slice(0, 12) + '...' : ''}</code>.
          </p>
        </div>

        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="text-slate-400">Authorization Status:</span>
          <span
            className={`px-3 py-1 rounded-full font-semibold border ${
              pilot.is_fully_approved
                ? 'bg-emerald-950/60 text-emerald-300 border-emerald-600/50'
                : 'bg-amber-950/60 text-amber-300 border-amber-600/50'
            }`}
          >
            {pilot.is_fully_approved ? 'FULLY AUTHORIZED' : 'APPROVALS PENDING'}
          </span>
        </div>
      </div>

      {submitError && (
        <div className="p-4 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs flex items-start gap-2.5">
          <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold">Approval Submission Failed</div>
            <div className="mt-0.5">{submitError}</div>
          </div>
        </div>
      )}

      {/* Required Approvals Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {requiredEntities.map((entityId) => {
          const approval = getApprovalRecord(entityId);
          const isApproved = Boolean(approval);
          const isStale = Boolean(approval && approval.scope_digest !== currentDigest);

          return (
            <div
              key={entityId}
              className={`p-4 rounded-xl border transition-all ${
                isApproved && !isStale
                  ? 'border-emerald-500/40 bg-emerald-950/10'
                  : isStale
                  ? 'border-amber-500/40 bg-amber-950/10'
                  : 'border-[var(--color-border)] bg-[var(--color-bg-secondary)]'
              } space-y-3`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-bold tracking-wider uppercase text-white">
                  {entityId === 'OPERATOR' ? 'Consortium Operator' : entityId.toUpperCase()}
                </span>
                {isApproved && !isStale && (
                  <span className="inline-flex items-center gap-1 text-[11px] font-mono text-emerald-400 font-semibold">
                    <CheckCircle2 className="w-3.5 h-3.5" /> APPROVED
                  </span>
                )}
                {isStale && (
                  <span className="inline-flex items-center gap-1 text-[11px] font-mono text-amber-400 font-semibold">
                    <AlertTriangle className="w-3.5 h-3.5" /> STALE REVISION
                  </span>
                )}
                {!isApproved && (
                  <span className="inline-flex items-center gap-1 text-[11px] font-mono text-slate-400">
                    <Clock className="w-3.5 h-3.5" /> AWAITING
                  </span>
                )}
              </div>

              {isApproved && approval ? (
                <div className="space-y-1.5 text-[11px] font-mono text-slate-300">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Approver ID:</span>
                    <span className="text-white truncate max-w-[140px]">{approval.approver_id}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Role:</span>
                    <span className="text-indigo-300">{approval.approver_role}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Approved At:</span>
                    <span>{new Date(approval.approved_at).toLocaleDateString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Signature:</span>
                    <span className="text-slate-400 truncate max-w-[140px]">
                      {approval.signature ? `${approval.signature.slice(0, 10)}...` : 'Operator Log Record'}
                    </span>
                  </div>
                  {approval.notes && (
                    <div className="mt-2 pt-2 border-t border-[var(--color-border)] text-slate-400 text-[10px] italic">
                      "{approval.notes}"
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-xs text-slate-400">
                  {entityId === 'OPERATOR'
                    ? 'Pending authorized consortium operator review and approval.'
                    : `Pending independent authorized sign-off from ${entityId.toUpperCase()} compliance/security.`}
                </p>
              )}
            </div>
          );
        })}
      </div>

      {/* Submit Approval Form */}
      {canApprove ? (
        <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-4">
          <div className="flex items-center gap-2">
            <Send className="w-4 h-4 text-cyan-400" />
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
              Submit Authorized Institutional Approval
            </h4>
          </div>

          <form onSubmit={handleApprove} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  Approving Authority (Restricted to Effective Role)
                </label>
                <select
                  value={approvingInstitution}
                  onChange={(e) => setApprovingInstitution(e.target.value)}
                  className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
                >
                  {approvableOptions.map((inst) => (
                    <option key={inst} value={inst}>
                      {inst === 'OPERATOR' ? 'CONSORTIUM OPERATOR' : inst.toUpperCase()}
                    </option>
                  ))}
                </select>
                <span className="text-[10px] text-slate-500 mt-1 block">
                  Only institutions you are authorized to represent for this pilot are listed.
                </span>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  Approval Notes / Rationale (Optional)
                </label>
                <input
                  type="text"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g. Approved per Q4 Cross-Border AML Enclave Authorization"
                  className="w-full text-xs bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-slate-400">
                Canonical Signing Challenge
              </label>
              <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-[11px] font-mono break-all text-cyan-300 select-all">
                {activeChallenge}
              </div>
              <span className="text-[10px] text-slate-500 block">
                Sign these exact UTF-8 bytes using the institution's enrolled sovereign Ed25519 private key.
              </span>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Institutional Ed25519 Signature Hex (Required for Bank Approval)
              </label>
              <input
                type="text"
                value={signature}
                onChange={(e) => setSignature(e.target.value)}
                placeholder="128-char hex Ed25519 signature computed over canonical approval bytes above"
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div className="flex justify-end">
              <button
                type="submit"
                disabled={approvePilotMutation.isPending}
                className="px-5 py-2.5 rounded-lg text-xs font-semibold bg-cyan-600 hover:bg-cyan-500 text-white transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {approvePilotMutation.isPending ? 'Recording Approval...' : 'Record Authorized Approval'}
              </button>
            </div>
          </form>
        </div>
      ) : (
        <div className="p-4 rounded-xl border border-slate-800 bg-slate-900/40 text-slate-400 text-xs flex items-center gap-2.5">
          <ShieldCheck className="w-4 h-4 text-slate-500 shrink-0" />
          <span>
            {pilot.is_fully_approved
              ? 'All required institutional and operator approvals have been recorded for the current scope digest.'
              : approvableOptions.length === 0
              ? 'Institutional approval submission requires compliance or admin clearance for your institution.'
              : 'Pilot scope is not currently open for approvals or has expired.'}
          </span>
        </div>
      )}
    </div>
  );
}
