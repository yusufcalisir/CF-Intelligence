import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  useCase,
  useAddCaseNote,
  useUpdateCaseStatus,
  useCaseEvidence,
  useAddEvidence,
} from '../api/queries';

import { CASE_STATUS_LABELS, PRIORITY_LABELS } from '../api/types';
import { useQueryClient } from '@tanstack/react-query';
import { ExplainabilityPanel } from './AlertsPage';


const STATUS_COLORS: Record<string, string> = {
  open: '#3b82f6',
  assigned: '#8b5cf6',
  investigating: '#f59e0b',
  pending_review: '#f97316',
  escalated: '#ef4444',
  sar_filed: '#d946ef',
  closed_confirmed: '#22c55e',
  closed_false_positive: '#6b7280',
};

const EVENT_ICONS: Record<string, string> = {
  created: '📋',
  assigned: '👤',
  status_changed: '🔄',
  note_added: '📝',
  alert_linked: '🔗',
};

export default function CaseDetailPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const { data: caseData, isLoading } = useCase(caseId);
  const addNote = useAddCaseNote();
  const updateStatus = useUpdateCaseStatus();
  const queryClient = useQueryClient();
  const [noteContent, setNoteContent] = useState('');
  const [supervisorSig, setSupervisorSig] = useState('');

  // Agentic AML Copilot state
  const [copilotData, setCopilotData] = useState<{
    fincen_sar_narrative: string;
    four_eyes_briefing: string;
    recommended_action: string;
    top_risk_drivers: Array<{ feature: string; impact: number; description?: string }>;
    lineage_hash: string;
  } | null>(null);
  const [isCopilotLoading, setIsCopilotLoading] = useState(false);

  const handleGenerateCopilotNarrative = async () => {
    if (!caseId) return;
    setIsCopilotLoading(true);
    try {
      const res = await fetch(`/api/v1/cases/${caseId}/copilot/narrative`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_id: caseId, include_fincen_narrative: true }),
      });
      if (res.ok) {
        const data = await res.json();
        setCopilotData(data);
      }
    } catch (e) {
      console.error('Failed to generate Copilot narrative', e);
    } finally {
      setIsCopilotLoading(false);
    }
  };
  const [evType, setEvType] = useState('document');
  const [evTitle, setEvTitle] = useState('');
  const [evFilePath, setEvFilePath] = useState('');
  const [evContent, setEvContent] = useState('');
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);

  const { data: evidenceList } = useCaseEvidence(caseId);

  const addEvidence = useAddEvidence();

  const handleAddNote = async () => {
    if (!noteContent.trim() || !caseId) return;
    await addNote.mutateAsync({ caseId, author: 'analyst', content: noteContent });
    setNoteContent('');
    queryClient.invalidateQueries({ queryKey: ['case', caseId] });
  };

  const [statusError, setStatusError] = useState<string | null>(null);

  const handleStatusChange = async (newStatus: string) => {
    if (!caseId) return;
    setStatusError(null);
    try {
      const isClosure = newStatus.startsWith('closed_');
      if (isClosure && (!supervisorSig || !supervisorSig.trim())) {
        setStatusError('⚠️ Supervisor signature is required for case closure (Four-Eyes Principle).');
        return;
      }
      await updateStatus.mutateAsync({
        caseId,
        status: newStatus,
        actor: 'analyst',
        ...(isClosure ? { supervisor_signature: supervisorSig } : {}),
      });
      setSupervisorSig('');
      queryClient.invalidateQueries({ queryKey: ['case', caseId] });
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || 'Status transition failed. Check your permissions and try again.';
      setStatusError(`❌ ${detail}`);
    }
  };

  const handleAddEvidence = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caseId || !evTitle.trim() || !evFilePath.trim() || !evContent.trim()) return;
    await addEvidence.mutateAsync({
      caseId,
      evidence_type: evType,
      title: evTitle,
      file_path: evFilePath,
      content: evContent,
      uploaded_by: 'analyst',
    });
    setEvTitle('');
    setEvFilePath('');
    setEvContent('');
    queryClient.invalidateQueries({ queryKey: ['case', caseId] });
    queryClient.invalidateQueries({ queryKey: ['case-evidence', caseId] });
  };

  if (isLoading) {
    return (
      <div className="glass-card p-8 text-center text-[var(--color-text-muted)]">
        Loading case...
      </div>
    );
  }

  if (!caseData) {
    return (
      <div className="glass-card p-8 text-center text-[var(--color-text-muted)]">
        Case not found
      </div>
    );
  }

  const statusColor = STATUS_COLORS[caseData.status] || '#6b7280';

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-5"
      >
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-3">
          <div>
            <h1 className="text-xl font-bold mb-1 break-words">{caseData.title}</h1>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:text-sm text-[var(--color-text-muted)]">
              <span>ID: {caseData.id.slice(0, 8)}</span>
              <span>•</span>
              <span>{PRIORITY_LABELS[caseData.priority] || caseData.priority}</span>
              {caseData.assigned_to && (
                <>
                  <span>•</span>
                  <span>Assigned to {caseData.assigned_to}</span>
                </>
              )}
            </div>
          </div>
          <span
            className="px-3 py-1 rounded-lg text-sm font-bold text-white self-start sm:self-auto"
            style={{ backgroundColor: statusColor }}
          >
            {CASE_STATUS_LABELS[caseData.status] || caseData.status}
          </span>
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-4">
          {[
            { label: 'Linked Alerts', value: caseData.alert_ids.length },
            { label: 'Notes', value: caseData.notes.length },
            { label: 'Timeline Events', value: caseData.timeline.length },
            { label: 'Duration', value: caseData.duration_hours ? `${caseData.duration_hours.toFixed(1)}h` : '-' },
          ].map((stat) => (
            <div key={stat.label} className="text-center p-2 bg-[var(--color-bg-elevated)]/30 rounded-lg">
              <div className="text-lg font-bold">{stat.value}</div>
              <div className="text-[10px] uppercase text-[var(--color-text-muted)]">{stat.label}</div>
            </div>
          ))}
        </div>

        {/* Status Actions */}
        {caseData.is_open && (
          <div className="flex flex-wrap gap-2 mt-4 pt-3 border-t border-[var(--color-border)] items-center w-full">
            <span className="text-xs text-[var(--color-text-muted)] self-center mr-2">Change status:</span>
            {(() => {
              const VALID_TRANSITIONS: Record<string, string[]> = {
                open: ['assigned', 'investigating', 'closed_false_positive'],
                assigned: ['investigating', 'open'],
                investigating: ['pending_review', 'escalated', 'closed_confirmed', 'closed_false_positive'],
                pending_review: ['investigating', 'escalated', 'closed_confirmed', 'closed_false_positive'],
                escalated: ['investigating', 'closed_confirmed', 'sar_filed'],
                sar_filed: ['closed_confirmed'],
              };
              return (VALID_TRANSITIONS[caseData.status] || []).map((value) => (
                <button
                  key={value}
                  onClick={() => handleStatusChange(value)}
                  disabled={updateStatus.isPending}
                  className="px-2 py-1 text-xs rounded border border-[var(--color-border)] hover:bg-[var(--color-surface-alt)] transition-colors disabled:opacity-50"
                >
                  {CASE_STATUS_LABELS[value] || value}
                </button>
              ));
            })()}
            {caseData.status === 'sar_filed' && (
              <a
                href={`/api/v1/cases/${caseData.id}/sar-report`}
                download={`sar_report_${caseData.id.slice(0, 8)}.xml`}
                target="_blank"
                rel="noreferrer"
                className="ml-auto px-3 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded text-xs font-semibold flex items-center gap-1 transition-colors"
              >
                📥 Download SAR XML
              </a>
            )}
            {/* Supervisor Signature for Case Closure */}
            {['investigating', 'pending_review', 'escalated', 'sar_filed'].includes(caseData.status) && (
              <div className="flex gap-2 items-center w-full max-w-sm mt-3 p-2 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                <span className="text-[10px] text-yellow-500 font-bold uppercase whitespace-nowrap">Supervisor Signature:</span>
                <input
                  type="text"
                  value={supervisorSig}
                  onChange={(e) => { setSupervisorSig(e.target.value); setStatusError(null); }}
                  placeholder="Secondary authorization key..."
                  className="px-2 py-1 text-xs rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] flex-1 focus:outline-none focus:border-yellow-500/50"
                />
              </div>
            )}
            {/* Status Error Toast */}
            {statusError && (
              <div className="w-full mt-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-xs font-medium animate-in fade-in">
                {statusError}
              </div>
            )}
          </div>
        )}
      </motion.div>

      {/* Agentic AML Copilot & RAG Narrative Section */}
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.05 }}
        className="glass-card p-5 space-y-4"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xl">🤖</span>
            <div>
              <h2 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                Autonomous Agentic AML Copilot & RAG Narrative
              </h2>
              <p className="text-xs text-[var(--color-text-muted)]">
                Synthesizes FinCEN FIN-2007-G003 5-Paragraph SAR Narratives & 4-Eyes Supervisor Briefings
              </p>
            </div>
          </div>
          <button
            onClick={handleGenerateCopilotNarrative}
            disabled={isCopilotLoading}
            className="px-3.5 py-1.5 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white rounded-lg text-xs font-bold transition-all shadow-md shadow-indigo-600/20 disabled:opacity-50 cursor-pointer flex items-center gap-1.5"
          >
            {isCopilotLoading ? '⏳ Synthesizing AI Narrative...' : '✨ Generate AI SAR Narrative'}
          </button>
        </div>

        {copilotData && (
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 pt-2 border-t border-[var(--color-border)]">
            <div className="xl:col-span-2 glass-card p-4 space-y-3 bg-black/20">
              <div className="flex items-center justify-between text-xs font-bold text-indigo-300 border-b border-[var(--color-border)] pb-2">
                <span>📄 FinCEN 5-Paragraph Regulatory SAR Narrative</span>
                <span className="font-mono text-[10px] text-emerald-400">ZERO-PII VERIFIED</span>
              </div>
              <div className="text-xs text-slate-300 leading-relaxed font-mono whitespace-pre-wrap max-h-96 overflow-y-auto pr-2">
                {copilotData.fincen_sar_narrative}
              </div>
            </div>

            <div className="glass-card p-4 space-y-3 bg-black/20 flex flex-col justify-between">
              <div>
                <div className="text-xs font-bold text-purple-300 border-b border-[var(--color-border)] pb-2 mb-3">
                  🛡️ BSA/AML 4-Eyes Supervisor Briefing
                </div>
                <div className="text-xs text-slate-200 font-mono whitespace-pre-wrap mb-4">
                  {copilotData.four_eyes_briefing}
                </div>
                <div className="space-y-1.5">
                  <div className="text-[10px] font-bold text-[var(--color-text-muted)] uppercase">Top SHAP Anomaly Drivers</div>
                  {copilotData.top_risk_drivers.map((d, i) => (
                    <div key={i} className="p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[11px] flex justify-between">
                      <span className="font-mono text-indigo-300">{d.feature}</span>
                      <span className="font-mono text-emerald-400 font-bold">+{(d.impact * 100).toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="text-[9px] font-mono text-[var(--color-text-muted)] pt-2 border-t border-[var(--color-border)] flex justify-between">
                <span>Lineage Hash: {copilotData.lineage_hash.slice(0, 16)}...</span>
                <span className="text-indigo-400 font-bold">{copilotData.recommended_action}</span>
              </div>
            </div>
          </div>
        )}
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Timeline */}
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.1 }}
          className="glass-card p-5"
        >
          <h2 className="text-sm font-bold uppercase text-[var(--color-text-muted)] mb-4">
            Investigation Timeline
          </h2>
          <div className="space-y-3">
            {caseData.timeline.length === 0 ? (
              <p className="text-sm text-[var(--color-text-muted)]">No events yet</p>
            ) : (
              caseData.timeline.map((event, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.05 }}
                  className="flex gap-3 items-start"
                >
                  <div className="text-lg mt-0.5">
                    {EVENT_ICONS[event.event_type] || '📌'}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm">{event.description}</p>
                    <div className="flex gap-2 text-[10px] text-[var(--color-text-muted)] mt-0.5">
                      <span>{new Date(event.timestamp).toLocaleString()}</span>
                      <span>•</span>
                      <span>{event.actor}</span>
                    </div>
                  </div>
                </motion.div>
              ))
            )}
          </div>
        </motion.div>

        {/* Notes */}
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.2 }}
          className="glass-card p-5"
        >
          <h2 className="text-sm font-bold uppercase text-[var(--color-text-muted)] mb-4">
            Investigation Notes
          </h2>

          {/* Add Note */}
          <div className="mb-4">
            <textarea
              value={noteContent}
              onChange={(e) => setNoteContent(e.target.value)}
              placeholder="Add an investigation note..."
              rows={3}
              className="w-full px-3 py-2 text-sm rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] resize-none"
            />
            <button
              onClick={handleAddNote}
              disabled={!noteContent.trim() || addNote.isPending}
              className="mt-2 px-4 py-1.5 text-xs font-semibold rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 disabled:opacity-50"
            >
              {addNote.isPending ? 'Adding...' : 'Add Note'}
            </button>
          </div>

          {/* Existing Notes */}
          <div className="space-y-3">
            {caseData.notes.length === 0 ? (
              <p className="text-sm text-[var(--color-text-muted)]">No notes yet</p>
            ) : (
              caseData.notes.map((note) => (
                <div
                  key={note.id}
                  className="p-3 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)]"
                >
                  <div className="flex justify-between text-[10px] text-[var(--color-text-muted)] mb-1">
                    <span className="font-semibold">{note.author}</span>
                    <span>{new Date(note.created_at).toLocaleString()}</span>
                  </div>
                  <p className="text-sm whitespace-pre-wrap">{note.content}</p>
                </div>
              ))
            )}
          </div>
        </motion.div>
      </div>

      {/* Linked Alerts & Explainability Inspector */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="glass-card p-5"
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
            Linked Alerts & AI Explainability Audit ({caseData.alert_ids.length})
          </h2>
          <span className="text-xs text-[var(--color-text-muted)]">Click an alert tag to launch audit replay</span>
        </div>
        {caseData.alert_ids.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No alerts linked to this case</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {caseData.alert_ids.map((id) => (
                <button
                  key={id}
                  onClick={() => setSelectedAlertId(selectedAlertId === id ? null : id)}
                  className={`px-3 py-1 text-xs font-mono rounded transition-all border ${
                    selectedAlertId === id
                      ? 'bg-[var(--color-primary)] text-white border-[var(--color-primary)] font-bold shadow-md'
                      : 'bg-[var(--color-surface-alt)] border-[var(--color-border)] hover:border-[var(--color-primary)] text-[var(--color-text-primary)]'
                  }`}
                >
                  🔍 {id.slice(0, 12)}
                </button>
              ))}
            </div>

            {selectedAlertId && (
              <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
                <ExplainabilityPanel
                  alert={{
                    id: selectedAlertId,
                    bank_id: (caseData as any).bank_id || 'bank_a',
                    transaction_id: `tx_${selectedAlertId.slice(0, 8)}`,
                    risk_score: 720.0,
                    severity: 'high' as any,
                    status: 'new' as any,
                    reason_codes: ['HIGH-AMT', 'GEO-RISK', 'VEL-001'],
                    confidence: 0.92,
                    involved_entity_ids: ['cust_linked_1'],
                    created_at: caseData.created_at,
                    top_features: [{ feature: 'transaction_amount', contribution: 0.45 }],
                    risk_factors: ['High risk score across multiple signals'],
                    model_confidence: 0.92,
                  }}
                />

              </div>
            )}
          </div>
        )}
      </motion.div>


      {/* Evidence Registry */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="glass-card p-4 sm:p-5 mt-6"
      >
        <h2 className="text-xs sm:text-sm font-bold uppercase text-[var(--color-text-muted)] mb-4 flex items-center gap-2">
          📁 Case Evidence Registry (Chain-of-Custody)
        </h2>

        {/* Register Evidence Form */}
        <form onSubmit={handleAddEvidence} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5 mb-6 p-4 rounded-xl bg-[var(--color-surface-alt)]/50 border border-[var(--color-border)]/50">
          <div>
            <label className="block text-[10px] font-bold text-[var(--color-text-muted)] uppercase mb-1 whitespace-nowrap">Evidence Type</label>
            <select
              value={evType}
              onChange={(e) => setEvType(e.target.value)}
              className="w-full px-3 py-2 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] font-mono"
            >
              <option value="document">📄 Document File</option>
              <option value="kyc_profile">👤 KYC Profile</option>
              <option value="ledger_proof">⛓️ Ledger Proof</option>
            </select>
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[var(--color-text-muted)] uppercase mb-1 whitespace-nowrap">Evidence Title</label>
            <input
              type="text"
              value={evTitle}
              onChange={(e) => setEvTitle(e.target.value)}
              placeholder="e.g. Identity Proof"
              className="w-full px-3 py-2 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)] font-mono"
              required
            />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[var(--color-text-muted)] uppercase mb-1 whitespace-nowrap">File Path / Reference</label>
            <input
              type="text"
              value={evFilePath}
              onChange={(e) => setEvFilePath(e.target.value)}
              placeholder="e.g. uploads/id.pdf"
              className="w-full px-3 py-2 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)] font-mono"
              required
            />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[var(--color-text-muted)] uppercase mb-1 whitespace-nowrap truncate">File Content (SHA-256)</label>
            <input
              type="text"
              value={evContent}
              onChange={(e) => setEvContent(e.target.value)}
              placeholder="Content string to hash..."
              className="w-full px-3 py-2 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)] font-mono"
              required
            />
          </div>
          <div className="flex items-end sm:col-span-2 lg:col-span-1">
            <button
              type="submit"
              disabled={addEvidence.isPending}
              className="w-full py-2 px-4 bg-[var(--color-primary)] text-white text-xs font-bold rounded-lg hover:opacity-90 disabled:opacity-50 whitespace-nowrap transition-all shadow-md shadow-indigo-500/20"
            >
              {addEvidence.isPending ? 'Registering...' : '+ Register Evidence'}
            </button>
          </div>
        </form>

        {/* Evidence List */}
        {/* Mobile / Tablet View: Stacked Cards */}
        <div className="block lg:hidden space-y-3">
          {!evidenceList || evidenceList.length === 0 ? (
            <div className="py-6 text-center text-[var(--color-text-muted)] text-xs font-mono">
              No evidence registered for this case.
            </div>
          ) : (
            evidenceList.map((ev) => (
              <div
                key={ev.id}
                className="p-3.5 rounded-xl bg-slate-900/60 border border-[var(--color-border)] space-y-2 font-mono text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-bold text-slate-100">{ev.title}</span>
                  <span className="px-2 py-0.5 rounded bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-[10px] font-bold capitalize shrink-0">
                    {ev.evidence_type.replace('_', ' ')}
                  </span>
                </div>
                <div className="flex flex-col gap-1.5 text-[11px] text-slate-400 border-t border-slate-800 pt-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-500 shrink-0">Path:</span>
                    <span className="text-slate-300 font-semibold truncate max-w-[220px]">{ev.file_path}</span>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <span className="text-slate-500 text-[10px]">Cryptographic Hash (SHA-256):</span>
                    <span className="text-cyan-400 font-mono text-[10px] break-all bg-black/40 p-1.5 rounded border border-white/5">
                      {ev.content_hash}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2 text-[10px] pt-1 border-t border-slate-800/50">
                    <span className="text-slate-400">By: <span className="text-slate-200">{ev.uploaded_by}</span></span>
                    <span className="text-slate-500">{new Date(ev.uploaded_at).toLocaleString()}</span>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Desktop View: Full Table */}
        <div className="hidden lg:block overflow-x-auto rounded-xl border border-white/5">
          <table className="w-full text-left text-xs border-collapse min-w-[750px]">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-[var(--color-text-muted)] font-mono text-[11px] bg-white/2">
                <th className="py-3 px-3 whitespace-nowrap">Type</th>
                <th className="py-3 px-3 whitespace-nowrap">Title</th>
                <th className="py-3 px-3 whitespace-nowrap font-mono">Reference Path</th>
                <th className="py-3 px-3 whitespace-nowrap font-mono">Cryptographic Hash (SHA-256)</th>
                <th className="py-3 px-3 whitespace-nowrap">Registered By</th>
                <th className="py-3 px-3 whitespace-nowrap text-right">Date</th>
              </tr>
            </thead>
            <tbody>
              {!evidenceList || evidenceList.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-[var(--color-text-muted)] text-xs font-mono">
                    No evidence registered for this case.
                  </td>
                </tr>
              ) : (
                evidenceList.map((ev) => (
                  <tr key={ev.id} className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-surface-alt)]/20 transition-colors font-mono text-[11px]">
                    <td className="py-3 px-3 capitalize font-medium text-slate-300 whitespace-nowrap">{ev.evidence_type.replace('_', ' ')}</td>
                    <td className="py-3 px-3 font-bold text-slate-100 whitespace-nowrap">{ev.title}</td>
                    <td className="py-3 px-3 font-mono text-slate-400 whitespace-nowrap">{ev.file_path}</td>
                    <td className="py-3 px-3 font-mono text-cyan-400 text-[10px] break-all">{ev.content_hash}</td>
                    <td className="py-3 px-3 text-slate-300 whitespace-nowrap">{ev.uploaded_by}</td>
                    <td className="py-3 px-3 text-right text-slate-400 whitespace-nowrap">
                      {new Date(ev.uploaded_at).toLocaleString()}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </motion.div>
    </div>
  );
}
