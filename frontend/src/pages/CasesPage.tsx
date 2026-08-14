import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { useCases, useCreateCase } from '../api/queries';
import { CASE_STATUS_LABELS, PRIORITY_LABELS } from '../api/types';
import { useModalA11y } from '../hooks/useModalA11y';

const PRIORITY_COLORS: Record<string, string> = {
  p1_critical: '#ef4444',
  p2_high: '#f97316',
  p3_medium: '#f59e0b',
  p4_low: '#3b82f6',
};

export default function CasesPage() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const { data: cases, isLoading, refetch } = useCases({
    status: statusFilter || undefined,
  });
  const createCase = useCreateCase();

  const handleCreate = async (title: string, priority: string) => {
    const result = await createCase.mutateAsync({ title, priority });
    setShowCreateModal(false);
    refetch();
    navigate(`/cases/${result.id}`);
  };

  return (
    <div className="space-y-6">
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        <h1 className="text-2xl font-bold gradient-text mb-1">
          Case Management
        </h1>
        <p className="text-sm text-[var(--color-text-muted)] max-w-2xl">
          Track, triage, and manage suspicious activity report (SAR) investigation cases
          across consortium participants with cryptographically chained evidence.
        </p>
      </motion.div>

      {/* Filter and Create Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <label htmlFor="case-status-filter" className="text-xs text-[var(--color-text-muted)]">Status:</label>
          <select
            id="case-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-1.5 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)]"
          >
            <option value="">All Statuses</option>
            {Object.entries(CASE_STATUS_LABELS).map(([val, label]) => (
              <option key={val} value={val}>{label}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-[var(--color-text-muted)]">
            {cases?.length ?? 0} cases
          </span>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 text-xs font-semibold rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity flex items-center gap-1.5"
          >
            + New Case
          </button>
        </div>
      </div>

      {/* Cases Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="glass-card p-5 h-44 animate-pulse" />
          ))}
        </div>
      ) : !cases || cases.length === 0 ? (
        <div className="glass-card p-12 text-center text-[var(--color-text-muted)]">
          <p className="text-lg font-semibold mb-1">No cases yet</p>
          <p className="text-xs">Create a case to start tracking investigations.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {cases.map((c) => (
            <motion.div
              key={c.id}
              whileHover={{ y: -2 }}
              onClick={() => navigate(`/cases/${c.id}`)}
              className="glass-card p-5 cursor-pointer hover:border-[var(--color-primary)] transition-colors flex flex-col justify-between"
            >
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span
                    className="text-[10px] font-bold px-2 py-0.5 rounded-full uppercase"
                    style={{
                      backgroundColor: `${PRIORITY_COLORS[c.priority] || '#3b82f6'}20`,
                      color: PRIORITY_COLORS[c.priority] || '#3b82f6',
                    }}
                  >
                    {PRIORITY_LABELS[c.priority] || c.priority}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-muted)] font-mono">
                    #{c.id.slice(0, 8)}
                  </span>
                </div>
                <h3 className="font-semibold text-sm line-clamp-2">{c.title}</h3>
              </div>

              <div className="mt-4 pt-3 border-t border-[var(--color-border)] flex items-center justify-between text-xs text-[var(--color-text-muted)]">
                <span className="capitalize">{c.status.replace('_', ' ')}</span>
                <span>{(c as any).alert_count ?? (c as any).alerts_count ?? 0} alerts linked</span>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      <AnimatePresence>
        {showCreateModal && (
          <CreateCaseModal
            onClose={() => setShowCreateModal(false)}
            onCreate={handleCreate}
            isLoading={createCase.isPending}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

function CreateCaseModal({
  onClose,
  onCreate,
  isLoading,
}: {
  onClose: () => void;
  onCreate: (title: string, priority: string) => void;
  isLoading: boolean;
}) {
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState('p3_medium');

  const { containerRef } = useModalA11y<HTMLDivElement>({
    isOpen: true,
    onClose,
    closeOnEscape: true,
    trapFocus: true,
    restoreFocus: true,
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <motion.div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-case-modal-title"
        tabIndex={-1}
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="glass-card p-6 w-full max-w-md space-y-4 focus:outline-none"
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-2">
          <h2 id="create-case-modal-title" className="text-lg font-bold">New Investigation Case</h2>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="text-[var(--color-text-muted)] hover:text-white p-1 rounded focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            ✕
          </button>
        </div>

        <div>
          <label htmlFor="case-title-input" className="block text-xs text-[var(--color-text-muted)] mb-1">Title</label>
          <input
            id="case-title-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Suspicious transaction cluster at Meridian..."
            className="w-full px-3 py-2 text-sm rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </div>

        <div>
          <label htmlFor="case-priority-select" className="block text-xs text-[var(--color-text-muted)] mb-1">Priority</label>
          <select
            id="case-priority-select"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            {Object.entries(PRIORITY_LABELS).map(([val, label]) => (
              <option key={val} value={val}>{label}</option>
            ))}
          </select>
        </div>

        <div className="flex gap-3 justify-end pt-2 border-t border-[var(--color-border)]">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-[var(--color-border)] hover:bg-[var(--color-surface-alt)] focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => title && onCreate(title, priority)}
            disabled={!title || isLoading}
            className="px-4 py-2 text-sm font-semibold rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            {isLoading ? 'Creating...' : 'Create Case'}
          </button>
        </div>
      </motion.div>
    </div>
  );
}
