import { useState } from 'react';
import type { PilotStatusResponse } from '../../api/types';
import { Copy, Check, Shield, Server, Calendar, Database, FileText } from 'lucide-react';

interface PilotScopeSectionProps {
  pilot: PilotStatusResponse;
}

export default function PilotScopeSection({ pilot }: PilotScopeSectionProps) {
  const [copied, setCopied] = useState(false);

  const handleCopyDigest = async () => {
    if (!pilot.scope_digest) return;
    try {
      await navigator.clipboard.writeText(pilot.scope_digest);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Ignore clipboard write failures
    }
  };

  const isExpired = pilot.has_expired || (pilot.expires_at && new Date(pilot.expires_at) <= new Date());

  return (
    <div className="space-y-6">
      {/* Scope Digest Banner */}
      <div className="rounded-xl border border-indigo-500/30 bg-indigo-950/20 p-4 md:p-5">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Shield className="w-4 h-4 text-indigo-400" />
              <span className="text-xs font-semibold uppercase tracking-wider text-indigo-300">
                Authoritative Scope Digest (SHA-256)
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Cryptographically binds participating institutions, dataset scopes, operational grants, and topology endpoints.
              All institutional approvals strictly verify against this immutable revision digest.
            </p>
          </div>
          <div className="flex items-center gap-2 self-start md:self-auto">
            <code className="text-xs font-mono bg-slate-900 px-3 py-1.5 rounded-lg border border-slate-700 text-indigo-200 select-all">
              {pilot.scope_digest ? `${pilot.scope_digest.slice(0, 16)}...${pilot.scope_digest.slice(-8)}` : 'Generating...'}
            </code>
            <button
              onClick={handleCopyDigest}
              title="Copy full 64-character SHA-256 scope digest"
              className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors border border-slate-700"
            >
              {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
            </button>
          </div>
        </div>
        {pilot.scope_digest && (
          <div className="mt-3 pt-3 border-t border-indigo-500/20">
            <span className="text-[11px] font-mono text-slate-400 break-all select-all">
              Full digest: {pilot.scope_digest}
            </span>
          </div>
        )}
      </div>

      {/* Grid of Scope Metadata */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Participating Institutions */}
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            <Server className="w-4 h-4 text-cyan-400" />
            Participating Institutions
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            {pilot.participating_institutions.map((bank) => (
              <span
                key={bank}
                className="px-2.5 py-1 rounded-md text-xs font-mono font-medium bg-cyan-950/40 text-cyan-300 border border-cyan-800/50"
              >
                {bank.toUpperCase()}
              </span>
            ))}
          </div>
          <p className="text-[11px] text-slate-400 pt-1">
            Governed by Consortium: <span className="font-mono text-slate-200">{pilot.consortium_id}</span>
          </p>
        </div>

        {/* Quota & Operational Bounds */}
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            <Database className="w-4 h-4 text-amber-400" />
            Authorized Query Quotas
          </div>
          <div className="grid grid-cols-2 gap-2 pt-1">
            <div>
              <div className="text-[11px] text-slate-400">Total Enclave Quota</div>
              <div className="text-lg font-mono font-bold text-white">
                {pilot.queries_total_quota} <span className="text-xs text-slate-400">queries</span>
              </div>
            </div>
            <div>
              <div className="text-[11px] text-slate-400">Executed / Denied</div>
              <div className="text-lg font-mono font-bold text-slate-200">
                <span className="text-emerald-400">{pilot.queries_executed}</span> /{' '}
                <span className="text-rose-400">{pilot.queries_denied}</span>
              </div>
            </div>
          </div>
          <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden mt-2">
            <div
              className="bg-cyan-500 h-2 rounded-full transition-all duration-300"
              style={{
                width: `${Math.min(100, Math.round((pilot.queries_executed / Math.max(1, pilot.queries_total_quota)) * 100))}%`,
              }}
            />
          </div>
        </div>

        {/* Schedule & Retention */}
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            <Calendar className="w-4 h-4 text-emerald-400" />
            Operational Window
          </div>
          <div className="space-y-1.5 text-xs font-mono pt-1">
            <div className="flex justify-between">
              <span className="text-slate-400">Starts:</span>
              <span className="text-slate-200">{pilot.starts_at ? new Date(pilot.starts_at).toLocaleString() : 'Upon Activation'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Expires:</span>
              <span className={`font-semibold ${isExpired ? 'text-rose-400' : 'text-slate-200'}`}>
                {pilot.expires_at ? new Date(pilot.expires_at).toLocaleString() : 'Unbounded'}
              </span>
            </div>
            <div className="flex justify-between pt-1 border-t border-[var(--color-border)]">
              <span className="text-slate-400">Status:</span>
              <span className={isExpired ? 'text-rose-400 font-bold' : 'text-emerald-400'}>
                {isExpired ? 'EXPIRED' : 'VALID / UNEXPIRED'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Purpose, Jurisdiction, and Grant Details */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            <FileText className="w-4 h-4 text-purple-400" />
            Governance & Regulatory Gating
          </div>
          <div className="space-y-2 text-xs">
            <div>
              <div className="text-[11px] text-slate-400">Operational Grant Reference</div>
              <div className="font-mono text-slate-200 bg-slate-900/60 p-1.5 rounded border border-slate-800 break-all">
                {pilot.operational_grant_id || 'Embedded Bilateral Consortium Grant'}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-slate-400">Permitted Investigation Purposes</div>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {pilot.allowed_purposes.map((purpose) => (
                  <span key={purpose} className="px-2 py-0.5 rounded text-[11px] font-mono bg-purple-950/40 text-purple-300 border border-purple-800/40">
                    {purpose}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-slate-400">Approved Jurisdictions</div>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {pilot.allowed_jurisdictions.map((j) => (
                  <span key={j} className="px-2 py-0.5 rounded text-[11px] font-mono bg-slate-800 text-slate-300 border border-slate-700">
                    {j}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            <Server className="w-4 h-4 text-indigo-400" />
            Execution Boundary & Architectural Scope
          </div>
          <div className="space-y-2 text-xs text-slate-300">
            <p>
              This console controls an authentic <strong>Two-Bank Enclave Pilot</strong> between participating institutions.
            </p>
            <div className="bg-slate-900/70 p-3 rounded-lg border border-slate-800 text-[11px] space-y-1.5 text-slate-400">
              <div className="text-slate-300 font-semibold">Boundary Contract:</div>
              <div>• Queries are governed by pairwise Ed25519 bilateral operational grants.</div>
              <div>• Independent of separately assembled multi-counterparty investigation dossiers.</div>
              <div>• Strict SSRF protection and deterministic preflight health probes enforce container containment.</div>
            </div>
            <div className="flex justify-between text-[11px] text-slate-400 pt-1">
              <span>Configuration Revision:</span>
              <span className="font-mono text-indigo-300 font-semibold">v{pilot.revision}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
