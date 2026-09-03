import React, { useState } from 'react';
import {
  Landmark,
  Layers,
  Zap,
  Play,
  CheckCircle2,
} from 'lucide-react';
import type { DatasetConsortiumEnrollRequest } from '../../api/types';

interface ConsortiumAssignmentPanelProps {
  auditId: string;
  onEnroll: (payload: DatasetConsortiumEnrollRequest) => void;
  isSubmitting?: boolean;
}

const BANK_NODES = [
  { id: 'bank_alpha', name: 'Bank Alpha', subtitle: 'Tier-1 Enterprise Gateway', records: '24,500 baseline txs' },
  { id: 'bank_beta', name: 'Bank Beta', subtitle: 'Retail & SME Neo-Bank', records: '18,200 baseline txs' },
  { id: 'bank_gamma', name: 'Bank Gamma', subtitle: 'Cross-Border Wire Hub', records: '12,900 baseline txs' },
  { id: 'bank_delta', name: 'Bank Delta (New Node)', subtitle: 'External Challenger Bank', records: '0 txs (New Partition)' },
];

export const ConsortiumAssignmentPanel: React.FC<ConsortiumAssignmentPanelProps> = ({
  auditId,
  onEnroll,
  isSubmitting,
}) => {
  const [selectedBank, setSelectedBank] = useState('bank_alpha');
  const [allocationMode, setAllocationMode] = useState<'replace_partition' | 'append_partition' | 'guest_node'>('replace_partition');
  const [triggerFL, setTriggerFL] = useState(true);

  const handleSubmit = () => {
    onEnroll({
      audit_id: auditId,
      target_bank_id: selectedBank,
      allocation_mode: allocationMode,
      trigger_fl_round: triggerFL,
    });
  };

  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-sm font-bold text-[var(--color-text-primary)]">
          Consortium Node Allocation & Federated Pipeline Handoff
        </h4>
        <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
          Select target banking node to enroll the audited dataset into the federated aggregation loop.
        </p>
      </div>

      {/* Bank Node Selection Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        {BANK_NODES.map((bank) => {
          const isSelected = selectedBank === bank.id;
          return (
            <div
              key={bank.id}
              onClick={() => setSelectedBank(bank.id)}
              className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                isSelected
                  ? 'border-indigo-500 bg-indigo-950/30 shadow-[0_0_20px_rgba(99,102,241,0.2)]'
                  : 'border-[var(--color-border-subtle)] bg-[var(--color-bg-primary)]/70 hover:border-white/20'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Landmark size={16} className={isSelected ? 'text-indigo-400' : 'text-slate-400'} />
                  <span className="text-xs font-bold text-slate-200">{bank.name}</span>
                </div>
                {isSelected && <CheckCircle2 size={15} className="text-indigo-400" />}
              </div>
              <p className="text-[11px] text-[var(--color-text-muted)] mt-1">{bank.subtitle}</p>
              <span className="text-[10px] font-mono text-slate-400 mt-2 block">{bank.records}</span>
            </div>
          );
        })}
      </div>

      {/* Allocation Mode & Trigger Options */}
      <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/5 space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <label className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
            <Layers size={13} className="text-indigo-400" /> Partition Allocation Strategy:
          </label>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setAllocationMode('replace_partition')}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                allocationMode === 'replace_partition'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'bg-white/5 text-slate-400 hover:text-white'
              }`}
            >
              Replace Partition
            </button>
            <button
              type="button"
              onClick={() => setAllocationMode('append_partition')}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                allocationMode === 'append_partition'
                  ? 'bg-indigo-600 text-white shadow'
                  : 'bg-white/5 text-slate-400 hover:text-white'
              }`}
            >
              Append Records
            </button>
          </div>
        </div>

        <div className="pt-2 border-t border-white/5 flex items-center justify-between">
          <label htmlFor="trigger-fl-toggle" className="text-xs font-semibold text-slate-300 flex items-center gap-1.5 cursor-pointer">
            <Zap size={13} className="text-amber-400" /> Trigger Federated Learning Round Automatically
          </label>
          <input
            id="trigger-fl-toggle"
            type="checkbox"
            checked={triggerFL}
            onChange={(e) => setTriggerFL(e.target.checked)}
            className="w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 bg-black/40 border-white/20 cursor-pointer"
          />
        </div>
      </div>

      {/* Final Action Button */}
      <button
        id="enroll-consortium-btn"
        type="button"
        disabled={isSubmitting}
        onClick={handleSubmit}
        className="w-full py-2.5 px-4 rounded-xl font-bold text-xs text-white bg-indigo-600 hover:bg-indigo-500 active:scale-[0.99] transition-all shadow-lg shadow-indigo-500/25 flex items-center justify-center gap-2 disabled:opacity-50"
      >
        <Play size={14} className="fill-white" />
        <span>Enroll Audited Dataset into {BANK_NODES.find((b) => b.id === selectedBank)?.name}</span>
      </button>
    </div>
  );
};
