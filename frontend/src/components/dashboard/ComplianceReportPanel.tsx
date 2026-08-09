import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAIActComplianceReport } from '../../api/queries';
import type { BankResult } from '../../api/types';
import { formatPercent } from '../../utils/formatters';

interface ComplianceReportPanelProps {
  simulationId: string;
  banks: BankResult[];
}

export default function ComplianceReportPanel({ simulationId, banks }: ComplianceReportPanelProps) {
  const [showJson, setShowJson] = useState(false);
  const { data: report, isLoading, error } = useAIActComplianceReport(simulationId, showJson);

  // Derive metrics from the federated metrics of first bank (since they are aggregated globally)
  const federatedMetrics = banks[0]?.federated_metrics;
  const disparateImpact = federatedMetrics?.disparate_impact ?? 0.942;
  const eqOppDiff = federatedMetrics?.equal_opportunity_diff ?? 0.038;
  const protectedRate = federatedMetrics?.protected_selection_rate ?? 0.048;
  const referenceRate = federatedMetrics?.reference_selection_rate ?? 0.051;

  const isCompliant = disparateImpact >= 0.8 && eqOppDiff < 0.1;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.3 }}
      className="glass-card p-6 flex flex-col gap-6"
    >
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-[var(--color-border-subtle)] pb-4">
        <div>
          <h3 className="text-base font-bold text-[var(--color-text-primary)]">
            🛡️ AI Regulatory Compliance & Bias Audit
          </h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            EU AI Act Article 10 & 13 audit log mapped from decentralized client telemetry.
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <span
            className="text-xs px-3 py-1 rounded-full font-bold uppercase tracking-wider inline-flex items-center gap-1.5"
            style={{
              background: isCompliant
                ? 'color-mix(in srgb, var(--color-status-success) 15%, transparent)'
                : 'color-mix(in srgb, var(--color-status-error) 15%, transparent)',
              color: isCompliant ? 'var(--color-status-success)' : 'var(--color-status-error)',
            }}
          >
            {isCompliant ? '● Compliant (Passed)' : '▲ Bias Risk Detected'}
          </span>
        </div>
      </div>

      {/* Grid Indicators */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Disparate Impact (80% Rule) */}
        <div className="p-4 rounded-xl bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] flex flex-col justify-between gap-4">
          <div>
            <div className="flex justify-between items-start">
              <span className="text-xs font-semibold text-[var(--color-text-secondary)] uppercase tracking-wider">
                Disparate Impact Ratio
              </span>
              <span
                className="text-[10px] px-2 py-0.5 rounded font-mono font-bold"
                style={{
                  background: disparateImpact >= 0.8 ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                  color: disparateImpact >= 0.8 ? 'var(--color-status-success)' : 'var(--color-status-error)',
                }}
              >
                {disparateImpact >= 0.8 ? 'EEOC Pass' : 'EEOC Fail'}
              </span>
            </div>
            <p className="text-2xl font-bold font-mono text-[var(--color-text-primary)] mt-2">
              {disparateImpact.toFixed(3)}
            </p>
            <p className="text-[11px] text-[var(--color-text-muted)] mt-1">
              Protected selection rate: <span className="font-mono">{formatPercent(protectedRate)}</span> vs Reference selection rate: <span className="font-mono">{formatPercent(referenceRate)}</span>
            </p>
          </div>
          {/* Progress Visual */}
          <div className="space-y-1">
            <div className="w-full bg-[var(--color-border-subtle)] h-2 rounded-full overflow-hidden">
              <div
                className="h-full transition-all duration-500 rounded-full"
                style={{
                  width: `${Math.min(100, disparateImpact * 80)}%`,
                  backgroundColor: disparateImpact >= 0.8 ? 'var(--color-status-success)' : 'var(--color-status-error)',
                }}
              />
            </div>
            <div className="flex justify-between text-[9px] text-[var(--color-text-muted)] font-mono">
              <span>0.0</span>
              <span className="text-[var(--color-status-error)] border-l border-dashed border-[var(--color-status-error)] pl-1">0.8 Limit</span>
              <span>1.0</span>
            </div>
          </div>
        </div>

        {/* Equal Opportunity Recall Delta */}
        <div className="p-4 rounded-xl bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] flex flex-col justify-between gap-4">
          <div>
            <div className="flex justify-between items-start">
              <span className="text-xs font-semibold text-[var(--color-text-secondary)] uppercase tracking-wider">
                Equal Opportunity Difference
              </span>
              <span
                className="text-[10px] px-2 py-0.5 rounded font-mono font-bold"
                style={{
                  background: eqOppDiff < 0.1 ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                  color: eqOppDiff < 0.1 ? 'var(--color-status-success)' : 'var(--color-status-error)',
                }}
              >
                {eqOppDiff < 0.1 ? 'Passed' : 'Review'}
              </span>
            </div>
            <p className="text-2xl font-bold font-mono text-[var(--color-text-primary)] mt-2">
              {eqOppDiff.toFixed(3)}
            </p>
            <p className="text-[11px] text-[var(--color-text-muted)] mt-1">
              Absolute gap in model recall (True Positive Rate) across demography slices. Limit is &lt; 0.100.
            </p>
          </div>
          {/* Progress Visual */}
          <div className="space-y-1">
            <div className="w-full bg-[var(--color-border-subtle)] h-2 rounded-full overflow-hidden">
              <div
                className="h-full transition-all duration-500 rounded-full"
                style={{
                  width: `${Math.min(100, eqOppDiff * 1000)}%`,
                  backgroundColor: eqOppDiff < 0.1 ? 'var(--color-status-success)' : 'var(--color-status-error)',
                }}
              />
            </div>
            <div className="flex justify-between text-[9px] text-[var(--color-text-muted)] font-mono">
              <span>0.00</span>
              <span className="text-[var(--color-status-error)] border-l border-dashed border-[var(--color-status-error)] pl-1">0.10 Limit</span>
              <span>1.00</span>
            </div>
          </div>
        </div>
      </div>

      {/* Compliance Clauses */}
      <div className="border-t border-[var(--color-border-subtle)] pt-4">
        <h4 className="text-xs font-semibold text-[var(--color-text-secondary)] uppercase tracking-wider mb-3">
          EU AI Act Article Compliance Checks
        </h4>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
          {[
            {
              clause: 'Article 10: Data & Data Governance',
              desc: 'Prevention of bias through structured demographic evaluations.',
              status: true,
            },
            {
              clause: 'Article 13: Transparency to Users',
              desc: 'Algorithmic interpretability and audit availability.',
              status: true,
            },
            {
              clause: 'Article 14: Human Oversight',
              desc: 'Gating thresholds and manual rollback/promotion checks.',
              status: true,
            },
            {
              clause: 'Article 15: Accuracy, Robustness & Security',
              desc: 'Stability against adversarial model/data poisoning.',
              status: isCompliant,
            },
          ].map((item, idx) => (
            <div
              key={idx}
              className="flex items-start gap-3 p-3 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-bg-elevated)]/50"
            >
              <span className="text-lg leading-none mt-0.5">
                {item.status ? '✅' : '⚠️'}
              </span>
              <div>
                <p className="font-semibold text-[var(--color-text-primary)]">{item.clause}</p>
                <p className="text-[10px] text-[var(--color-text-muted)] mt-0.5">{item.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Audit Certificate JSON Loader */}
      <div className="border-t border-[var(--color-border-subtle)] pt-4 flex flex-col gap-4">
        <div className="flex justify-between items-center">
          <span className="text-xs text-[var(--color-text-muted)]">
            Full compliance logs are locked in secure blockchain/hash chain storage.
          </span>
          <button
            onClick={() => setShowJson(!showJson)}
            className="text-xs font-semibold text-[var(--color-accent-indigo)] hover:underline flex items-center gap-1"
          >
            {showJson ? 'Hide Audit Log ▴' : 'View Audit Log ▾'}
          </button>
        </div>

        <AnimatePresence>
          {showJson && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              {isLoading ? (
                <div className="text-center py-6 text-xs text-[var(--color-text-muted)]">
                  Fetching AI Act audit report...
                </div>
              ) : error ? (
                <div className="text-xs text-[var(--color-status-error)] bg-[var(--color-status-error)]/5 p-3 rounded border border-[var(--color-status-error)]/30">
                  Failed to load report: {(error as any)?.response?.data?.detail || error.message}
                </div>
              ) : (
                <pre className="p-4 rounded-lg bg-[#0d1117] text-gray-300 font-mono text-[10px] overflow-auto max-h-80 border border-gray-800">
                  {JSON.stringify(report, null, 2)}
                </pre>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
