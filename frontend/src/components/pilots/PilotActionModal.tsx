import { useState } from 'react';
import { AlertTriangle, PauseCircle, PlayCircle, CheckCircle, Ban, X } from 'lucide-react';

interface PilotActionModalProps {
  isOpen: boolean;
  actionType: 'pause' | 'resume' | 'abort' | 'complete' | null;
  pilotId: string;
  isPending: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}

export default function PilotActionModal({
  isOpen,
  actionType,
  pilotId,
  isPending,
  onClose,
  onConfirm,
}: PilotActionModalProps) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  if (!isOpen || !actionType) return null;

  const requiresReason = actionType === 'pause' || actionType === 'abort';

  const titleMap = {
    pause: 'Trigger Emergency Operational Pause',
    resume: 'Resume Paused Pilot Operations',
    abort: 'Emergency Pilot Abort (Permanent Terminal)',
    complete: 'Complete Pilot Lifecycle Normally',
  };

  const descriptionMap = {
    pause:
      'Immediately pauses all new operational matching queries across participating institutions. In-flight transactions complete; all subsequent queries fail closed.',
    resume:
      'Re-validates bilateral container readiness, unexpired grant validity, and remaining quota before re-enabling active operational queries.',
    abort:
      'Permanently terminates this pilot enclave. This action is terminal and cannot be reversed. Complete audit evidence will be preserved.',
    complete:
      'Marks the controlled pilot lifecycle as successfully completed and seals operational quota reservations.',
  };

  const buttonClassMap = {
    pause: 'bg-amber-600 hover:bg-amber-500 text-white',
    resume: 'bg-emerald-600 hover:bg-emerald-500 text-white',
    abort: 'bg-rose-600 hover:bg-rose-500 text-white',
    complete: 'bg-cyan-600 hover:bg-cyan-500 text-white',
  };

  const iconMap = {
    pause: <PauseCircle className="w-5 h-5 text-amber-400" />,
    resume: <PlayCircle className="w-5 h-5 text-emerald-400" />,
    abort: <Ban className="w-5 h-5 text-rose-400" />,
    complete: <CheckCircle className="w-5 h-5 text-cyan-400" />,
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (requiresReason && reason.trim().length < 3) {
      setError('A meaningful operational reason (minimum 3 characters) is required.');
      return;
    }
    setError(null);
    try {
      await onConfirm(reason.trim());
      setReason('');
      onClose();
    } catch (err: any) {
      setError(err?.message || 'Action failed');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-lg rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-6 shadow-2xl space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2.5">
            {iconMap[actionType]}
            <h3 className="text-sm font-bold text-white tracking-wide">
              {titleMap[actionType]}
            </h3>
          </div>
          <button
            onClick={onClose}
            disabled={isPending}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Description */}
        <div className="text-xs text-slate-300 space-y-2">
          <p>{descriptionMap[actionType]}</p>
          <div className="font-mono text-[11px] text-slate-400 bg-slate-900/60 p-2 rounded border border-slate-800">
            Target Pilot: <span className="text-white font-semibold">{pilotId}</span>
          </div>
        </div>

        {actionType === 'abort' && (
          <div className="p-3 rounded-lg border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>CRITICAL: Aborting is permanent and cannot be undone by operator resume.</span>
          </div>
        )}

        {error && (
          <div className="p-3 rounded-lg border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs">
            {error}
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {requiresReason && (
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Operational Rationale / Mandatory Audit Reason <span className="text-rose-400">*</span>
              </label>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Document the regulatory, operational, or compliance reason for this kill switch action..."
                rows={3}
                required
                className="w-full text-xs bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 font-sans"
              />
            </div>
          )}

          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isPending}
              className="px-4 py-2 rounded-lg text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isPending}
              className={`px-5 py-2 rounded-lg text-xs font-semibold transition-all shadow-md cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${buttonClassMap[actionType]}`}
            >
              {isPending ? 'Executing Action...' : 'Confirm & Execute'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
