import { useState } from 'react';
import { useProvisionPilot } from '../../api/pilots';
import { formatApiError } from '../../api/client';
import { X, ShieldPlus, Server, AlertCircle } from 'lucide-react';

interface PilotProvisionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (pilotId: string) => void;
}

export default function PilotProvisionModal({ isOpen, onClose, onSuccess }: PilotProvisionModalProps) {
  const [pilotId, setPilotId] = useState('');
  const [consortiumId, setConsortiumId] = useState('');
  const [bankA, setBankA] = useState('bank_a');
  const [bankB, setBankB] = useState('bank_b');
  const [purpose, setPurpose] = useState('AML_COLLABORATIVE_INVESTIGATION');
  const [jurisdiction, setJurisdiction] = useState('eu-central-1');
  const [grantId, setGrantId] = useState('');
  const [quotaTotal, setQuotaTotal] = useState(50);
  const [quotaPerBank, setQuotaPerBank] = useState(25);
  const [durationDays, setDurationDays] = useState(14);
  const [coordUrl, setCoordUrl] = useState('http://localhost:8000');
  const [bankAUrl, setBankAUrl] = useState('http://localhost:8001');
  const [bankBUrl, setBankBUrl] = useState('http://localhost:8002');
  const [formError, setFormError] = useState<string | null>(null);

  const provisionMutation = useProvisionPilot();

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const cleanPilotId = pilotId.trim();
    const cleanConsortiumId = consortiumId.trim();

    if (!cleanPilotId || !cleanConsortiumId) {
      setFormError('Pilot ID and Consortium ID are required.');
      return;
    }

    if (bankA.trim().toLowerCase() === bankB.trim().toLowerCase()) {
      setFormError('Participating Bank A and Bank B must be distinct institutional entities.');
      return;
    }

    try {
      const res = await provisionMutation.mutateAsync({
        pilot_id: cleanPilotId,
        consortium_id: cleanConsortiumId,
        participating_institutions: [bankA.trim().toLowerCase(), bankB.trim().toLowerCase()],
        allowed_purposes: [purpose.trim()],
        allowed_jurisdictions: [jurisdiction.trim()],
        operational_grant_id: grantId.trim(),
        query_quota_total: Number(quotaTotal),
        query_quota_per_institution: Number(quotaPerBank),
        duration_days: Number(durationDays),
        topology: {
          coordinator_url: coordUrl.trim(),
          bank_a_url: bankAUrl.trim(),
          bank_b_url: bankBUrl.trim(),
          [`${bankA.trim().toLowerCase()}_url`]: bankAUrl.trim(),
          [`${bankB.trim().toLowerCase()}_url`]: bankBUrl.trim(),
        },
      });
      onSuccess(res.pilot_id);
      onClose();
    } catch (err) {
      setFormError(formatApiError(err, 'Failed to provision pilot.'));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-in fade-in duration-200 overflow-y-auto">
      <div className="relative w-full max-w-2xl rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-6 shadow-2xl space-y-4 my-8">
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2.5">
            <ShieldPlus className="w-5 h-5 text-indigo-400" />
            <h3 className="text-sm font-bold text-white tracking-wide">
              Provision Governed Two-Bank Controlled Pilot
            </h3>
          </div>
          <button
            onClick={onClose}
            disabled={provisionMutation.isPending}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-slate-400">
          Provisions a controlled two-bank operational pilot in <code className="text-indigo-300 font-mono">DRAFT</code> state.
          Calculates the initial immutable scope digest and establishes durable reservation quotas.
        </p>

        {formError && (
          <div className="p-3.5 rounded-xl border border-rose-500/40 bg-rose-950/30 text-rose-300 text-xs flex items-start gap-2.5">
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
            <div>{formError}</div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Pilot Identifier <span className="text-rose-400">*</span>
              </label>
              <input
                type="text"
                value={pilotId}
                onChange={(e) => setPilotId(e.target.value)}
                placeholder="e.g. pilot_bilateral_2026_q4"
                pattern="^[a-zA-Z0-9_\-]+$"
                required
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
              <span className="text-[10px] text-slate-500 mt-0.5 block">Alphanumeric, underscores, hyphens only</span>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Governing Consortium ID <span className="text-rose-400">*</span>
              </label>
              <input
                type="text"
                value={consortiumId}
                onChange={(e) => setConsortiumId(e.target.value)}
                placeholder="e.g. consortium_alpha"
                required
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Participating Institution A <span className="text-rose-400">*</span>
              </label>
              <input
                type="text"
                value={bankA}
                onChange={(e) => setBankA(e.target.value)}
                placeholder="bank_a"
                required
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Participating Institution B <span className="text-rose-400">*</span>
              </label>
              <input
                type="text"
                value={bankB}
                onChange={(e) => setBankB(e.target.value)}
                placeholder="bank_b"
                required
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Investigation Purpose</label>
              <input
                type="text"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder="AML_COLLABORATIVE_INVESTIGATION"
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Jurisdiction</label>
              <input
                type="text"
                value={jurisdiction}
                onChange={(e) => setJurisdiction(e.target.value)}
                placeholder="eu-central-1"
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Duration (Days)</label>
              <input
                type="number"
                min="1"
                max="365"
                value={durationDays}
                onChange={(e) => setDurationDays(Number(e.target.value))}
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Total Enclave Quota</label>
              <input
                type="number"
                min="1"
                value={quotaTotal}
                onChange={(e) => setQuotaTotal(Number(e.target.value))}
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Quota Per Institution</label>
              <input
                type="number"
                min="1"
                value={quotaPerBank}
                onChange={(e) => setQuotaPerBank(Number(e.target.value))}
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Operational Grant ID</label>
              <input
                type="text"
                value={grantId}
                onChange={(e) => setGrantId(e.target.value)}
                placeholder="Optional Grant Reference"
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>

          {/* Topology Details */}
          <div className="p-3.5 rounded-xl border border-slate-800 bg-slate-900/60 space-y-2.5">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
              <Server className="w-4 h-4 text-cyan-400" />
              Approved Topology Endpoints
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-xs font-mono">
              <div>
                <label className="block text-[10px] text-slate-400 mb-0.5">Coordinator URL</label>
                <input
                  type="text"
                  value={coordUrl}
                  onChange={(e) => setCoordUrl(e.target.value)}
                  className="w-full text-xs bg-slate-950 border border-slate-700 rounded p-1.5 text-white"
                />
              </div>
              <div>
                <label className="block text-[10px] text-slate-400 mb-0.5">{bankA} Worker</label>
                <input
                  type="text"
                  value={bankAUrl}
                  onChange={(e) => setBankAUrl(e.target.value)}
                  className="w-full text-xs bg-slate-950 border border-slate-700 rounded p-1.5 text-white"
                />
              </div>
              <div>
                <label className="block text-[10px] text-slate-400 mb-0.5">{bankB} Worker</label>
                <input
                  type="text"
                  value={bankBUrl}
                  onChange={(e) => setBankBUrl(e.target.value)}
                  className="w-full text-xs bg-slate-950 border border-slate-700 rounded p-1.5 text-white"
                />
              </div>
            </div>
          </div>

          {/* Submit / Cancel Buttons */}
          <div className="flex items-center justify-end gap-3 pt-3 border-t border-[var(--color-border)]">
            <button
              type="button"
              onClick={onClose}
              disabled={provisionMutation.isPending}
              className="px-4 py-2 rounded-lg text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={provisionMutation.isPending}
              className="px-5 py-2.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-all shadow-md cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {provisionMutation.isPending ? 'Provisioning Pilot...' : 'Provision Pilot (DRAFT)'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
