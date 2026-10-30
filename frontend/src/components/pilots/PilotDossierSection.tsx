import { useState } from 'react';
import type { PilotStatusResponse } from '../../api/types';
import { usePilotDossier } from '../../api/pilots';
import { FileText, Download, ShieldCheck, Lock, AlertCircle, History, Check } from 'lucide-react';

interface PilotDossierSectionProps {
  pilot: PilotStatusResponse;
}

export default function PilotDossierSection({ pilot }: PilotDossierSectionProps) {
  const [loadDossier, setLoadDossier] = useState(false);
  const [downloadSuccess, setDownloadSuccess] = useState(false);

  const { data: dossier, isLoading, error } = usePilotDossier(pilot.pilot_id, loadDossier);

  const handleDownload = () => {
    if (!dossier) return;
    try {
      const jsonStr = JSON.stringify(dossier, null, 2);
      const blob = new Blob([jsonStr], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pilot_${pilot.pilot_id}_audit_dossier.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setDownloadSuccess(true);
      setTimeout(() => setDownloadSuccess(false), 3000);
    } catch {
      // Ignore download errors
    }
  };

  const isForbidden = (error as any)?.response?.status === 403;

  return (
    <div className="space-y-6">
      {/* Top Dossier Card */}
      <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-indigo-400" />
            <h3 className="text-sm font-semibold text-white tracking-wide">
              Authoritative Compliance & Audit Dossier
            </h3>
          </div>
          <p className="text-xs text-slate-400 max-w-2xl">
            Immutable dossier compiled from durable pilot storage and <code className="text-indigo-300 font-mono text-[11px]">ImmutableAuditChain</code>.
            Restricted to compliance officers, security authorities, and institutional auditors.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {!loadDossier ? (
            <button
              onClick={() => setLoadDossier(true)}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-all shadow-sm cursor-pointer"
            >
              <FileText className="w-3.5 h-3.5" />
              Load Audit Dossier
            </button>
          ) : (
            <button
              onClick={handleDownload}
              disabled={!dossier || isLoading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white transition-all shadow-sm cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {downloadSuccess ? <Check className="w-3.5 h-3.5 text-white" /> : <Download className="w-3.5 h-3.5" />}
              {downloadSuccess ? 'Downloaded' : 'Export Dossier JSON'}
            </button>
          )}
        </div>
      </div>

      {/* Role Restriction Banner if 403 */}
      {isForbidden && (
        <div className="p-4 rounded-xl border border-amber-500/40 bg-amber-950/20 text-amber-300 text-xs flex items-start gap-3">
          <Lock className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="font-semibold">Access Restricted - Compliance / Auditor Authority Required</div>
            <p className="text-slate-400">
              Your authenticated principal does not hold compliance or audit clearance for dossier export.
              High-level status remains accessible; detailed cryptographic audit trails are restricted.
            </p>
          </div>
        </div>
      )}

      {error && !isForbidden && (
        <div className="p-4 rounded-xl border border-rose-500/40 bg-rose-950/20 text-rose-300 text-xs flex items-start gap-3">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold">Failed to Load Audit Dossier</div>
            <p className="text-slate-400 mt-0.5">{(error as any)?.message || 'An error occurred.'}</p>
          </div>
        </div>
      )}

      {/* State Transition History (Available from Status Response) */}
      <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-4">
        <div className="flex items-center gap-2">
          <History className="w-4 h-4 text-cyan-400" />
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
            Lifecycle State Transitions History
          </h4>
        </div>

        <div className="space-y-2 text-xs font-mono">
          <div className="flex items-start gap-3 p-3 rounded-lg bg-slate-900/60 border border-slate-800">
            <span className="w-2 h-2 rounded-full bg-cyan-400 shrink-0 mt-1.5" />
            <div className="space-y-0.5 flex-1">
              <div className="flex justify-between">
                <span className="font-bold text-white">{pilot.status}</span>
                <span className="text-slate-500">{new Date(pilot.updated_at).toLocaleString()}</span>
              </div>
              <div className="text-slate-400">{pilot.status_reason || 'Initial provision or transition'}</div>
            </div>
          </div>
          {pilot.activated_at && (
            <div className="flex items-start gap-3 p-3 rounded-lg bg-slate-900/60 border border-slate-800">
              <span className="w-2 h-2 rounded-full bg-emerald-400 shrink-0 mt-1.5" />
              <div className="space-y-0.5 flex-1">
                <div className="flex justify-between">
                  <span className="font-bold text-white">ACTIVATED</span>
                  <span className="text-slate-500">{new Date(pilot.activated_at).toLocaleString()}</span>
                </div>
                <div className="text-slate-400">Pilot activated for live queries</div>
              </div>
            </div>
          )}
          {pilot.terminated_at && (
            <div className="flex items-start gap-3 p-3 rounded-lg bg-slate-900/60 border border-slate-800">
              <span className="w-2 h-2 rounded-full bg-rose-400 shrink-0 mt-1.5" />
              <div className="space-y-0.5 flex-1">
                <div className="flex justify-between">
                  <span className="font-bold text-white">TERMINATED ({pilot.status})</span>
                  <span className="text-slate-500">{new Date(pilot.terminated_at).toLocaleString()}</span>
                </div>
                <div className="text-slate-400">{pilot.termination_reason || 'Pilot concluded'}</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Dossier JSON Preview when Loaded */}
      {dossier && (
        <div className="p-5 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
                Authoritative Dossier Payload Preview
              </h4>
            </div>
            <span className="text-[11px] font-mono text-slate-400">
              Audit Events: <strong>{(dossier.audit_events || []).length}</strong> | Receipts: <strong>{(dossier.execution_receipts || []).length}</strong>
            </span>
          </div>

          <pre className="p-4 rounded-lg bg-slate-950 border border-slate-800 font-mono text-[11px] text-slate-300 max-h-96 overflow-y-auto leading-relaxed select-all">
            {JSON.stringify(dossier, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
