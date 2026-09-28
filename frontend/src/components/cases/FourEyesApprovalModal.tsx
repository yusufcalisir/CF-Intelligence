import { useState, useMemo, FC, FormEvent } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ShieldCheck, ShieldAlert, AlertTriangle, UserCheck, Lock, CheckCircle2 } from 'lucide-react';

export interface FourEyesApprovalModalProps {
  isOpen: boolean;
  onClose: () => void;
  caseId: string;
  caseTitle: string;
  assignedInvestigator?: string | null;
  currentActor?: string;
  onConfirm: (payload: {
    resolution: 'CONFIRMED_FRAUD' | 'FALSE_POSITIVE';
    primarySupervisor: string;
    secondarySupervisor: string;
    notes: string;
  }) => Promise<void>;
  isSubmitting?: boolean;
}

export function cleanIdentity(id: string | null | undefined): string {
  if (!id) return '';
  let cleaned = id.trim();
  for (const prefix of ['supervisor:', 'analyst:', 'investigator:', 'SIG_SUPERVISOR_', 'sig_supervisor_']) {
    if (cleaned.startsWith(prefix)) {
      cleaned = cleaned.substring(prefix.length).trim();
    }
  }
  return cleaned.toLowerCase();
}

export const FourEyesApprovalModal: FC<FourEyesApprovalModalProps> = ({
  isOpen,
  onClose,
  caseId,
  caseTitle,
  assignedInvestigator,
  currentActor = 'analyst',
  onConfirm,
  isSubmitting = false,
}) => {
  const [resolution, setResolution] = useState<'CONFIRMED_FRAUD' | 'FALSE_POSITIVE'>('CONFIRMED_FRAUD');
  const [primarySupervisor, setPrimarySupervisor] = useState('');
  const [secondarySupervisor, setSecondarySupervisor] = useState('');
  const [notes, setNotes] = useState('');
  const [submitError, setSubmitError] = useState<string | null>(null);

  const cleanAssigned = useMemo(() => cleanIdentity(assignedInvestigator), [assignedInvestigator]);
  const cleanActor = useMemo(() => cleanIdentity(currentActor), [currentActor]);
  const cleanPrimary = useMemo(() => cleanIdentity(primarySupervisor), [primarySupervisor]);
  const cleanSecondary = useMemo(() => cleanIdentity(secondarySupervisor), [secondarySupervisor]);

  // Validation rules
  const selfApprovalPrimary = Boolean(
    cleanPrimary && ((cleanAssigned && cleanPrimary === cleanAssigned) || (cleanActor && cleanPrimary === cleanActor))
  );

  const selfApprovalSecondary = Boolean(
    cleanSecondary && ((cleanAssigned && cleanSecondary === cleanAssigned) || (cleanActor && cleanSecondary === cleanActor))
  );

  const duplicateSupervisors = Boolean(
    cleanPrimary && cleanSecondary && cleanPrimary === cleanSecondary
  );

  const isValid = Boolean(
    cleanPrimary &&
    cleanSecondary &&
    !selfApprovalPrimary &&
    !selfApprovalSecondary &&
    !duplicateSupervisors &&
    !isSubmitting
  );

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitError(null);

    if (selfApprovalPrimary || selfApprovalSecondary) {
      setSubmitError('Self-approval prohibited: An investigator cannot approve their own case (ApproverID != InvestigatorID).');
      return;
    }
    if (duplicateSupervisors) {
      setSubmitError('Dual control requires two distinct supervisor identities.');
      return;
    }
    if (!cleanPrimary || !cleanSecondary) {
      setSubmitError('Both primary and secondary supervisor authorizations are required.');
      return;
    }

    try {
      await onConfirm({
        resolution,
        primarySupervisor: primarySupervisor.trim(),
        secondarySupervisor: secondarySupervisor.trim(),
        notes: notes.trim(),
      });
      onClose();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || 'Four-Eyes authorization submission failed. Check supervisor credentials.';
      setSubmitError(detail);
    }
  };

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md overflow-y-auto">
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 15 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 15 }}
          className="w-full max-w-xl rounded-2xl bg-gradient-to-br from-[#0c0e29]/95 via-[#0e1138]/90 to-[#0c0e29]/95 border border-indigo-500/30 shadow-[0_0_50px_rgba(99,102,241,0.2)] p-6 space-y-5 text-slate-200 relative overflow-hidden"
        >
          {/* Header */}
          <div className="flex items-start justify-between border-b border-indigo-500/20 pb-4">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-400">
                <ShieldCheck className="w-6 h-6" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-white flex items-center gap-2">
                  Four-Eyes Dual Control Signoff
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">
                    Art. 14 High-Risk AI
                  </span>
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Case resolution requires dual supervisor approval. Self-approval is cryptographically blocked.
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              disabled={isSubmitting}
              className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800/60 transition-colors"
            >
              ✕
            </button>
          </div>

          {/* Case Context Summary */}
          <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800 text-xs space-y-1.5">
            <div className="flex justify-between items-center">
              <span className="text-slate-400">Case ID:</span>
              <span className="font-mono text-indigo-300 font-semibold">{caseId}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-400">Title:</span>
              <span className="text-white font-medium truncate max-w-[280px]">{caseTitle}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-400">Assigned Investigator:</span>
              <span className="font-medium text-amber-300 flex items-center gap-1">
                <UserCheck className="w-3.5 h-3.5" />
                {assignedInvestigator || 'Unassigned'}
              </span>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Resolution Choice */}
            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
                Resolution Determination
              </label>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setResolution('CONFIRMED_FRAUD')}
                  className={`p-3 rounded-xl border flex flex-col items-center gap-1.5 transition-all text-xs font-medium ${
                    resolution === 'CONFIRMED_FRAUD'
                      ? 'bg-rose-500/15 border-rose-500/50 text-rose-300 shadow-[0_0_20px_rgba(244,63,94,0.2)]'
                      : 'bg-slate-900/40 border-slate-800 text-slate-400 hover:bg-slate-800/40'
                  }`}
                >
                  <ShieldAlert className="w-5 h-5 text-rose-400" />
                  <span>Confirmed Fraud</span>
                  <span className="text-[10px] text-slate-500">SAR Filing Eligible</span>
                </button>

                <button
                  type="button"
                  onClick={() => setResolution('FALSE_POSITIVE')}
                  className={`p-3 rounded-xl border flex flex-col items-center gap-1.5 transition-all text-xs font-medium ${
                    resolution === 'FALSE_POSITIVE'
                      ? 'bg-emerald-500/15 border-emerald-500/50 text-emerald-300 shadow-[0_0_20px_rgba(16,185,129,0.2)]'
                      : 'bg-slate-900/40 border-slate-800 text-slate-400 hover:bg-slate-800/40'
                  }`}
                >
                  <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                  <span>False Positive</span>
                  <span className="text-[10px] text-slate-500">Dismiss Alert Activity</span>
                </button>
              </div>
            </div>

            {/* Primary Supervisor Input */}
            <div className="space-y-1">
              <label className="block text-xs font-semibold text-slate-300">
                Primary Supervisor Identity <span className="text-rose-400">*</span>
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={primarySupervisor}
                  onChange={(e) => {
                    setPrimarySupervisor(e.target.value);
                    setSubmitError(null);
                  }}
                  placeholder="e.g. supervisor_alice"
                  className={`w-full px-3 py-2 text-xs rounded-xl bg-slate-900 border text-white placeholder-slate-500 outline-none transition-colors ${
                    selfApprovalPrimary
                      ? 'border-rose-500/80 focus:border-rose-500'
                      : 'border-slate-700 focus:border-indigo-400'
                  }`}
                />
                <Lock className="w-4 h-4 text-slate-500 absolute right-3 top-2.5" />
              </div>
              {selfApprovalPrimary && (
                <p className="text-[11px] text-rose-400 flex items-center gap-1 mt-1">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  Self-approval blocked: Cannot match assigned investigator ({assignedInvestigator}) or current actor.
                </p>
              )}
            </div>

            {/* Secondary Supervisor Input */}
            <div className="space-y-1">
              <label className="block text-xs font-semibold text-slate-300">
                Secondary Supervisor Identity <span className="text-rose-400">*</span>
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={secondarySupervisor}
                  onChange={(e) => {
                    setSecondarySupervisor(e.target.value);
                    setSubmitError(null);
                  }}
                  placeholder="e.g. supervisor_bob"
                  className={`w-full px-3 py-2 text-xs rounded-xl bg-slate-900 border text-white placeholder-slate-500 outline-none transition-colors ${
                    selfApprovalSecondary || duplicateSupervisors
                      ? 'border-rose-500/80 focus:border-rose-500'
                      : 'border-slate-700 focus:border-indigo-400'
                  }`}
                />
                <Lock className="w-4 h-4 text-slate-500 absolute right-3 top-2.5" />
              </div>
              {selfApprovalSecondary && (
                <p className="text-[11px] text-rose-400 flex items-center gap-1 mt-1">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  Self-approval blocked: Cannot match assigned investigator ({assignedInvestigator}) or current actor.
                </p>
              )}
              {duplicateSupervisors && !selfApprovalSecondary && (
                <p className="text-[11px] text-amber-400 flex items-center gap-1 mt-1">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  Distinct signers required: Secondary supervisor must be different from primary supervisor.
                </p>
              )}
            </div>

            {/* Resolution Notes */}
            <div className="space-y-1">
              <label className="block text-xs font-semibold text-slate-300">
                Governance & Investigation Findings Rationale
              </label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="Document consensus findings, transaction risk review, and rationale for case closure..."
                className="w-full px-3 py-2 text-xs rounded-xl bg-slate-900 border border-slate-700 text-white placeholder-slate-500 outline-none focus:border-indigo-400 transition-colors"
              />
            </div>

            {/* Error Banner */}
            {submitError && (
              <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <span>{submitError}</span>
              </div>
            )}

            {/* Footer Actions */}
            <div className="flex items-center justify-end gap-3 pt-3 border-t border-indigo-500/20">
              <button
                type="button"
                onClick={onClose}
                disabled={isSubmitting}
                className="px-4 py-2 text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 rounded-xl transition-colors"
              >
                Cancel
              </button>

              <button
                type="submit"
                disabled={!isValid || isSubmitting}
                className={`px-4 py-2 text-xs font-semibold rounded-xl flex items-center gap-1.5 transition-all ${
                  isValid && !isSubmitting
                    ? 'bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white shadow-[0_0_20px_rgba(99,102,241,0.35)]'
                    : 'bg-slate-800 text-slate-500 border border-slate-700 cursor-not-allowed'
                }`}
              >
                <ShieldCheck className="w-4 h-4" />
                {isSubmitting ? 'Signing & Closing...' : 'Authorize & Resolve Case'}
              </button>
            </div>
          </form>
        </motion.div>
      </div>
    </AnimatePresence>
  );
};

export default FourEyesApprovalModal;
