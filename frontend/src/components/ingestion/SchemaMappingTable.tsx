import React from 'react';
import {
  SlidersHorizontal,
  CheckCircle2,
  ShieldCheck,
  HelpCircle,
} from 'lucide-react';
import type { ColumnMappingItem } from '../../api/types';

interface SchemaMappingTableProps {
  detectedColumns: string[];
  mappings: ColumnMappingItem[];
  sampleRows: Record<string, any>[];
  onMappingChange: (sourceCol: string, targetSignal: string) => void;
  piiViolationsCount?: number;
  piiMaskedReceipt?: string;
}

const CANONICAL_TARGET_OPTIONS = [
  { value: 'transaction_amount', label: 'transaction_amount (Float / Required)', required: true },
  { value: 'timestamp', label: 'timestamp (DateTime / Epoch)', required: true },
  { value: 'source_account_id', label: 'source_account_id (Hashed / Required)', required: true },
  { value: 'destination_account_id', label: 'destination_account_id (Hashed)', required: false },
  { value: 'channel_type', label: 'channel_type (Category: WIRE/ACH/POS)', required: false },
  { value: 'is_fraud', label: 'is_fraud (Binary Target 0/1 / Required)', required: true },
  { value: 'device_id', label: 'device_id (Hardware Token)', required: false },
  { value: 'ip_address', label: 'ip_address (Hashed Network Node)', required: false },
  { value: 'currency', label: 'currency (ISO 4217 code)', required: false },
  { value: 'custom_feature', label: 'custom_feature (Auxiliary Model Input)', required: false },
];

export const SchemaMappingTable: React.FC<SchemaMappingTableProps> = ({
  detectedColumns,
  mappings,
  sampleRows,
  onMappingChange,
  piiViolationsCount = 0,
  piiMaskedReceipt = 'ZERO-PII-VERIFIED',
}) => {
  const currentMappingMap = new Map(mappings.map((m) => [m.source_column, m.target_signal]));
  const mappedCanonicalCount = Array.from(currentMappingMap.values()).filter(
    (val) => val !== 'custom_feature'
  ).length;

  return (
    <div className="space-y-4">
      {/* Header & Status bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl bg-white/[0.03] border border-white/10">
        <div className="flex items-center gap-2.5">
          <div className="p-2 rounded-lg bg-indigo-500/20 text-indigo-400">
            <SlidersHorizontal size={18} />
          </div>
          <div>
            <h4 className="text-sm font-bold text-[var(--color-text-primary)]">
              Interactive Schema & Signal Alignment
            </h4>
            <p className="text-xs text-[var(--color-text-muted)]">
              Map uploaded column headers into consortium AML features.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="px-3 py-1 rounded-xl bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 text-xs font-mono font-bold flex items-center gap-1.5">
            <CheckCircle2 size={13} />
            <span>{mappedCanonicalCount}/9 Canonical Signals</span>
          </div>
          <div className="px-3 py-1 rounded-xl bg-indigo-500/20 border border-indigo-500/40 text-indigo-300 text-xs font-mono font-bold flex items-center gap-1.5">
            <ShieldCheck size={13} />
            <span>{piiMaskedReceipt}</span>
          </div>
        </div>
      </div>

      {/* Mapping Configuration Table */}
      <div className="overflow-x-auto rounded-xl border border-[var(--color-border-subtle)] bg-[var(--color-bg-primary)]/80 max-h-72">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-[var(--color-bg-primary)] border-b border-[var(--color-border-subtle)] text-[var(--color-text-muted)] uppercase tracking-wider font-semibold z-10">
            <tr>
              <th className="px-3.5 py-2.5">Source Header</th>
              <th className="px-3.5 py-2.5">Inferred Type</th>
              <th className="px-3.5 py-2.5">Sample Values</th>
              <th className="px-3.5 py-2.5">Target AML Signal</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border-subtle)]/50">
            {detectedColumns.map((col) => {
              const currentTarget = currentMappingMap.get(col) || 'custom_feature';
              const mappingItem = mappings.find((m) => m.source_column === col);
              const sampleVals = sampleRows.slice(0, 3).map((r) => String(r[col] ?? ''));

              return (
                <tr key={col} className="hover:bg-white/[0.02] transition-colors">
                  <td className="px-3.5 py-2.5 font-mono font-semibold text-slate-200">
                    {col}
                  </td>
                  <td className="px-3.5 py-2.5">
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-white/5 border border-white/10 text-slate-300">
                      {mappingItem?.data_type || 'string'}
                    </span>
                  </td>
                  <td className="px-3.5 py-2.5 font-mono text-[11px] text-[var(--color-text-muted)] max-w-xs truncate">
                    {sampleVals.join(', ')}
                  </td>
                  <td className="px-3.5 py-2.5">
                    <select
                      id={`mapping-select-${col}`}
                      value={currentTarget}
                      onChange={(e) => onMappingChange(col, e.target.value)}
                      className="w-full max-w-xs px-2.5 py-1.5 rounded-lg bg-black/40 border border-white/15 text-slate-200 text-xs focus:outline-none focus:border-indigo-500 font-sans"
                    >
                      {CANONICAL_TARGET_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {piiViolationsCount > 0 && (
        <div className="p-3 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-300 text-xs flex items-center gap-2">
          <HelpCircle size={15} className="shrink-0 text-amber-400" />
          <span>
            Notice: {piiViolationsCount} raw identifier patterns were detected and hashed client-side with bank type salt before transmission.
          </span>
        </div>
      )}
    </div>
  );
};
