import { useState, useMemo } from 'react';
import { useProvisionPilot } from '../../api/pilots';
import { useConsortium } from '../../api/queries';
import { formatApiError } from '../../api/client';
import { X, ShieldPlus, Server, AlertCircle, Info } from 'lucide-react';

interface PilotProvisionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (pilotId: string) => void;
}

const STANDARD_PURPOSES = [
  'AML_COLLABORATIVE_INVESTIGATION',
  'CROSS_BORDER_FRAUD_RING_DETECTION',
  'MULE_ACCOUNT_RECONCILIATION',
];

export default function PilotProvisionModal({ isOpen, onClose, onSuccess }: PilotProvisionModalProps) {
  const [pilotId, setPilotId] = useState('');
  const [consortiumId, setConsortiumId] = useState('');
  const [bankA, setBankA] = useState('');
  const [bankB, setBankB] = useState('');
  const [purpose, setPurpose] = useState('AML_COLLABORATIVE_INVESTIGATION');
  const [jurisdiction, setJurisdiction] = useState('');
  const [grantId, setGrantId] = useState('');
  const [quotaTotal, setQuotaTotal] = useState(50);
  const [quotaPerBank, setQuotaPerBank] = useState(25);
  const [durationDays, setDurationDays] = useState(14);
  const [coordUrl, setCoordUrl] = useState('');
  const [bankAUrl, setBankAUrl] = useState('');
  const [bankBUrl, setBankBUrl] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const cleanConsortiumId = consortiumId.trim();
  const { data: consortium, isLoading: isConsortiumLoading } = useConsortium(cleanConsortiumId || undefined);

  const candidateBanks = useMemo(() => {
    if (!consortium?.members || consortium.members.length === 0) return [];
    return consortium.members.map((m) => m.bank_id);
  }, [consortium]);

  const provisionMutation = useProvisionPilot();

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    const cleanPilotId = pilotId.trim();
    const cleanBankA = bankA.trim().toLowerCase();
    const cleanBankB = bankB.trim().toLowerCase();

    if (!cleanPilotId || !cleanConsortiumId) {
      setFormError('Pilot ID and Governing Consortium ID are required.');
      return;
    }

    if (!cleanBankA || !cleanBankB) {
      setFormError('Both Participating Institution A and B must be specified.');
      return;
    }

    if (cleanBankA === cleanBankB) {
      setFormError('Participating Institution A and Institution B must be distinct institutional entities.');
      return;
    }

    const topo: Record<string, string> = {};
    if (coordUrl.trim()) topo['coordinator_url'] = coordUrl.trim();
    if (bankAUrl.trim()) {
      topo['bank_a_worker_url'] = bankAUrl.trim();
      topo[`${cleanBankA}_worker_url`] = bankAUrl.trim();
    }
    if (bankBUrl.trim()) {
      topo['bank_b_worker_url'] = bankBUrl.trim();
      topo[`${cleanBankB}_worker_url`] = bankBUrl.trim();
    }

    try {
      const res = await provisionMutation.mutateAsync({
        pilot_id: cleanPilotId,
        consortium_id: cleanConsortiumId,
        participating_institutions: [cleanBankA, cleanBankB],
        allowed_purposes: [purpose.trim()],
        allowed_jurisdictions: jurisdiction.trim() ? [jurisdiction.trim()] : [],
        operational_grant_id: grantId.trim() || undefined,
        query_quota_total: Number(quotaTotal),
        query_quota_per_institution: Number(quotaPerBank),
        duration_days: Number(durationDays),
        topology: Object.keys(topo).length > 0 ? topo : undefined,
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
          Provisions an authorized bilateral pilot in <code className="text-indigo-300 font-mono">DRAFT</code> state.
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
                placeholder="e.g. cfi-consortium"
                required
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
              {cleanConsortiumId && (
                <span className="text-[10px] text-slate-400 mt-0.5 block">
                  {isConsortiumLoading
                    ? 'Validating consortium...'
                    : consortium
                    ? `Consortium: ${consortium.name || consortium.consortium_id} (${consortium.status})`
                    : 'Consortium membership will be verified by backend.'}
                </span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Participating Institution A <span className="text-rose-400">*</span>
              </label>
              {candidateBanks.length > 0 ? (
                <select
                  value={bankA}
                  onChange={(e) => setBankA(e.target.value)}
                  required
                  className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
                >
                  <option value="">Select Member Institution...</option>
                  {candidateBanks.map((b) => (
                    <option key={b} value={b}>
                      {b.toUpperCase()}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={bankA}
                  onChange={(e) => setBankA(e.target.value)}
                  placeholder="e.g. bank_a"
                  required
                  className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
                />
              )}
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Participating Institution B <span className="text-rose-400">*</span>
              </label>
              {candidateBanks.length > 0 ? (
                <select
                  value={bankB}
                  onChange={(e) => setBankB(e.target.value)}
                  required
                  className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
                >
                  <option value="">Select Member Institution...</option>
                  {candidateBanks.map((b) => (
                    <option key={b} value={b}>
                      {b.toUpperCase()}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={bankB}
                  onChange={(e) => setBankB(e.target.value)}
                  placeholder="e.g. bank_b"
                  required
                  className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
                />
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Investigation Purpose</label>
              <select
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white focus:outline-none focus:border-cyan-500"
              >
                {STANDARD_PURPOSES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Jurisdiction</label>
              <input
                type="text"
                value={jurisdiction}
                onChange={(e) => setJurisdiction(e.target.value)}
                placeholder="e.g. eu-central-1"
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
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
                className="w-full text-xs font-mono bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>

          {/* Topology Details */}
          <div className="p-3.5 rounded-xl border border-slate-800 bg-slate-900/60 space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
                <Server className="w-4 h-4 text-cyan-400" />
                Operational Topology Endpoints
              </div>
              <span className="text-[10px] text-slate-500">Optional during initial DRAFT</span>
            </div>

            <div className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800/80 text-[11px] text-slate-400 flex items-start gap-2">
              <Info className="w-4 h-4 text-cyan-400 shrink-0 mt-0.5" />
              <span>
                Provide explicit operational hostnames or URLs. If endpoints are not yet configured, the pilot will remain in truthful DRAFT state and preflight will require verified endpoints before approval.
              </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-xs font-mono">
              <div>
                <label className="block text-[10px] text-slate-400 mb-0.5">Coordinator URL</label>
                <input
                  type="text"
                  value={coordUrl}
                  onChange={(e) => setCoordUrl(e.target.value)}
                  placeholder="https://coordinator.consortium..."
                  className="w-full text-xs bg-slate-950 border border-slate-700 rounded p-1.5 text-white placeholder-slate-600"
                />
              </div>
              <div>
                <label className="block text-[10px] text-slate-400 mb-0.5">{bankA ? bankA.toUpperCase() : 'Institution A'} Worker</label>
                <input
                  type="text"
                  value={bankAUrl}
                  onChange={(e) => setBankAUrl(e.target.value)}
                  placeholder="https://worker-a.bank..."
                  className="w-full text-xs bg-slate-950 border border-slate-700 rounded p-1.5 text-white placeholder-slate-600"
                />
              </div>
              <div>
                <label className="block text-[10px] text-slate-400 mb-0.5">{bankB ? bankB.toUpperCase() : 'Institution B'} Worker</label>
                <input
                  type="text"
                  value={bankBUrl}
                  onChange={(e) => setBankBUrl(e.target.value)}
                  placeholder="https://worker-b.bank..."
                  className="w-full text-xs bg-slate-950 border border-slate-700 rounded p-1.5 text-white placeholder-slate-600"
                />
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={provisionMutation.isPending}
              className="px-4 py-2 rounded-lg text-xs font-semibold text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={provisionMutation.isPending}
              className="px-5 py-2 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {provisionMutation.isPending ? 'Provisioning...' : 'Provision Controlled Pilot'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
