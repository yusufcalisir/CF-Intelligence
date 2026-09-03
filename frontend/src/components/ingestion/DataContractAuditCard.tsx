import React from 'react';
import {
  FileCheck2,
  CheckCircle2,
  AlertTriangle,
  Download,
  Gauge,
  Layers,
  Activity,
} from 'lucide-react';
import type { DatasetContractAuditResponse } from '../../api/types';

interface DataContractAuditCardProps {
  auditResult: DatasetContractAuditResponse;
}

export const DataContractAuditCard: React.FC<DataContractAuditCardProps> = ({ auditResult }) => {
  const isPassed = auditResult.status === 'passed';
  const compliancePct = Math.round(auditResult.overall_compliance_score * 100);

  return (
    <div className="space-y-4">
      {/* Header Posture Card */}
      <div
        className={`p-4 rounded-2xl border transition-all ${
          isPassed
            ? 'bg-emerald-950/20 border-emerald-500/40 shadow-[0_0_30px_rgba(16,185,129,0.15)]'
            : 'bg-amber-950/20 border-amber-500/40 shadow-[0_0_30px_rgba(245,158,11,0.15)]'
        }`}
      >
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className={`p-2.5 rounded-xl shrink-0 ${
                isPassed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'
              }`}
            >
              {isPassed ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h4 className="text-base font-bold text-[var(--color-text-primary)]">
                  Great Expectations (GE 1.x) Contract Audit
                </h4>
                <span
                  className={`text-[10px] font-mono font-extrabold px-2 py-0.5 rounded-full uppercase tracking-wider ${
                    isPassed
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                      : 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                  }`}
                >
                  {auditResult.status}
                </span>
              </div>
              <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                {auditResult.audit_message}
              </p>
            </div>
          </div>

          {auditResult.quarantine_csv_download_url && (
            <a
              href={auditResult.quarantine_csv_download_url}
              download="quarantine_records.csv"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl font-semibold text-xs text-amber-300 bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 transition-all shrink-0 self-start sm:self-auto"
            >
              <Download size={13} />
              <span>Download Quarantine CSV ({auditResult.quarantined_records} rows)</span>
            </a>
          )}
        </div>

        {/* Statistical Metrics Strip */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mt-4 pt-3.5 border-t border-white/5">
          <div className="p-2.5 rounded-xl bg-black/30 border border-white/5">
            <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
              <FileCheck2 size={11} className="text-indigo-400" /> Compliance Score
            </span>
            <p className="text-base font-bold font-mono text-emerald-400 mt-0.5">
              {compliancePct}%
            </p>
          </div>

          <div className="p-2.5 rounded-xl bg-black/30 border border-white/5">
            <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
              <Layers size={11} className="text-amber-400" /> Records Partition
            </span>
            <p className="text-xs font-bold font-mono text-slate-200 mt-1">
              <span className="text-emerald-400">{auditResult.passed_records} OK</span>
              {auditResult.quarantined_records > 0 && (
                <span className="text-rose-400 ml-1.5 font-bold">
                  / {auditResult.quarantined_records} Quarantined
                </span>
              )}
            </p>
          </div>

          <div className="p-2.5 rounded-xl bg-black/30 border border-white/5">
            <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
              <Gauge size={11} className="text-cyan-400" /> Dirichlet Non-IID (α)
            </span>
            <p className="text-base font-bold font-mono text-cyan-400 mt-0.5">
              α = {auditResult.dirichlet_alpha_estimate.toFixed(2)}
            </p>
          </div>

          <div className="p-2.5 rounded-xl bg-black/30 border border-white/5">
            <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
              <Activity size={11} className="text-purple-400" /> Drift (KS Metric)
            </span>
            <p className="text-base font-bold font-mono text-purple-400 mt-0.5">
              {auditResult.drift_ks_score.toFixed(3)}
            </p>
          </div>
        </div>
      </div>

      {/* Expectation Rules Checklist */}
      <div className="space-y-2">
        <span className="text-[11px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
          Expectation Suite Verification Details:
        </span>
        <div className="grid grid-cols-1 gap-2 max-h-56 overflow-y-auto">
          {auditResult.contract_checks.map((check, idx) => (
            <div
              key={idx}
              className="p-3 rounded-xl bg-white/[0.02] border border-white/5 flex items-start justify-between gap-3 text-xs"
            >
              <div className="space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-bold text-slate-200">
                    {check.expectation_name}
                  </span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 text-indigo-300">
                    col: {check.column}
                  </span>
                </div>
                <p className="text-xs text-[var(--color-text-muted)]">{check.details}</p>
              </div>

              <span
                className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold shrink-0 ${
                  check.status === 'passed'
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                    : 'bg-rose-500/20 text-rose-400 border border-rose-500/40'
                }`}
              >
                {check.status.toUpperCase()}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
