import { useState, useMemo, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useCases, useCreateCase, useUpdateAlertStatus } from '../api/queries';
import { CASE_STATUS_LABELS, PRIORITY_LABELS } from '../api/types';
import { useModalA11y } from '../hooks/useModalA11y';
import { Search, X } from 'lucide-react';

export const CASES_STATUS_FILTER_KEY = 'cfi_cases_status_filter';
export const CASES_PRIORITY_FILTER_KEY = 'cfi_cases_priority_filter';
export const CASES_SEARCH_KEY = 'cfi_cases_search_query';

const PRIORITY_COLORS: Record<string, string> = {
  p1_critical: '#ef4444',
  p2_high: '#f97316',
  p3_medium: '#f59e0b',
  p4_low: '#3b82f6',
};

export default function CasesPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Lazy initialize status filter from URL params -> sessionStorage -> default
  const [statusFilter, setStatusFilter] = useState<string>(() => {
    const fromUrl = searchParams.get('status')?.toLowerCase();
    if (fromUrl && (fromUrl in CASE_STATUS_LABELS || fromUrl === 'all')) {
      return fromUrl === 'all' ? '' : fromUrl;
    }
    try {
      const stored = sessionStorage.getItem(CASES_STATUS_FILTER_KEY);
      if (stored && (stored in CASE_STATUS_LABELS || stored === '')) return stored;
    } catch { /* ignore storage errors */ }
    return '';
  });

  // Lazy initialize priority filter from URL params -> sessionStorage -> default
  const [priorityFilter, setPriorityFilter] = useState<string>(() => {
    const fromUrl = searchParams.get('priority')?.toLowerCase();
    if (fromUrl && (fromUrl in PRIORITY_LABELS || fromUrl === 'all')) {
      return fromUrl === 'all' ? '' : fromUrl;
    }
    try {
      const stored = sessionStorage.getItem(CASES_PRIORITY_FILTER_KEY);
      if (stored && (stored in PRIORITY_LABELS || stored === '')) return stored;
    } catch { /* ignore storage errors */ }
    return '';
  });

  // Lazy initialize search query from URL params -> sessionStorage -> default
  const [searchQuery, setSearchQuery] = useState<string>(() => {
    const fromUrl = searchParams.get('q');
    if (fromUrl) return fromUrl;
    try {
      const stored = sessionStorage.getItem(CASES_SEARCH_KEY);
      if (stored) return stored;
    } catch { /* ignore storage errors */ }
    return '';
  });

  // Check for incoming alert escalation parameters (?from_alert=... / ?alert_id=... / ?create=true)
  const fromAlertId = searchParams.get('from_alert') || searchParams.get('alert_id') || undefined;
  const initialTitleParam = searchParams.get('title') || '';
  const initialPriorityParam = searchParams.get('priority') || '';
  const initialRiskScoreParam = searchParams.get('risk_score');
  const initialRiskScore = initialRiskScoreParam ? parseFloat(initialRiskScoreParam) : undefined;

  const [showCreateModal, setShowCreateModal] = useState<boolean>(() => {
    return Boolean(searchParams.get('create') === 'true' || searchParams.get('from_alert') || searchParams.get('alert_id'));
  });

  // Auto-open modal if escalation query params change
  useEffect(() => {
    if (searchParams.get('create') === 'true' || searchParams.get('from_alert') || searchParams.get('alert_id')) {
      setShowCreateModal(true);
    }
  }, [searchParams]);

  const updateAlertStatus = useUpdateAlertStatus();

  // Sync state to URL and sessionStorage
  const updateUrlAndStorage = (newStatus: string, newPriority: string, newSearch: string) => {
    try {
      if (newStatus) sessionStorage.setItem(CASES_STATUS_FILTER_KEY, newStatus);
      else sessionStorage.removeItem(CASES_STATUS_FILTER_KEY);

      if (newPriority) sessionStorage.setItem(CASES_PRIORITY_FILTER_KEY, newPriority);
      else sessionStorage.removeItem(CASES_PRIORITY_FILTER_KEY);

      if (newSearch) sessionStorage.setItem(CASES_SEARCH_KEY, newSearch);
      else sessionStorage.removeItem(CASES_SEARCH_KEY);
    } catch { /* ignore storage errors */ }

    const nextParams = new URLSearchParams();
    if (newStatus) nextParams.set('status', newStatus);
    if (newPriority) nextParams.set('priority', newPriority);
    if (newSearch.trim()) nextParams.set('q', newSearch.trim());
    setSearchParams(nextParams, { replace: true });
  };

  const handleStatusChange = (val: string) => {
    setStatusFilter(val);
    updateUrlAndStorage(val, priorityFilter, searchQuery);
  };

  const handlePriorityChange = (val: string) => {
    setPriorityFilter(val);
    updateUrlAndStorage(statusFilter, val, searchQuery);
  };

  const handleSearchChange = (val: string) => {
    setSearchQuery(val);
    updateUrlAndStorage(statusFilter, priorityFilter, val);
  };

  const handleClearAllFilters = () => {
    setStatusFilter('');
    setPriorityFilter('');
    setSearchQuery('');
    updateUrlAndStorage('', '', '');
  };

  // If URL has no search params on mount but state was restored from sessionStorage, sync to URL
  useEffect(() => {
    const hasUrlParams = searchParams.has('status') || searchParams.has('priority') || searchParams.has('q');
    if (!hasUrlParams && (statusFilter || priorityFilter || searchQuery)) {
      const nextParams = new URLSearchParams();
      if (statusFilter) nextParams.set('status', statusFilter);
      if (priorityFilter) nextParams.set('priority', priorityFilter);
      if (searchQuery.trim()) nextParams.set('q', searchQuery.trim());
      setSearchParams(nextParams, { replace: true });
    }
  }, []);

  // Synchronize state when browser navigation occurs (back/forward)
  useEffect(() => {
    const rawStatus = searchParams.get('status');
    if (rawStatus !== null) {
      const urlStatus = rawStatus.toLowerCase();
      if (urlStatus !== statusFilter && (urlStatus in CASE_STATUS_LABELS || urlStatus === '')) {
        setStatusFilter(urlStatus);
      }
    }

    const rawPriority = searchParams.get('priority');
    if (rawPriority !== null) {
      const urlPriority = rawPriority.toLowerCase();
      if (urlPriority !== priorityFilter && (urlPriority in PRIORITY_LABELS || urlPriority === '')) {
        setPriorityFilter(urlPriority);
      }
    }

    const rawQ = searchParams.get('q');
    if (rawQ !== null) {
      if (rawQ !== searchQuery) {
        setSearchQuery(rawQ);
      }
    }
  }, [searchParams]);

  const { data: cases, isLoading, refetch } = useCases({
    status: statusFilter || undefined,
    priority: priorityFilter || undefined,
  });

  const createCase = useCreateCase();

  // Filter cases client-side by text query (matching title, ID, or investigator)
  const filteredCases = useMemo(() => {
    if (!cases) return [];
    if (!searchQuery.trim()) return cases;
    const q = searchQuery.toLowerCase().trim();
    return cases.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q) ||
        (c.assigned_to && c.assigned_to.toLowerCase().includes(q))
    );
  }, [cases, searchQuery]);

  const handleCloseCreateModal = () => {
    setShowCreateModal(false);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('create');
    nextParams.delete('from_alert');
    nextParams.delete('alert_id');
    nextParams.delete('title');
    nextParams.delete('priority');
    nextParams.delete('risk_score');
    setSearchParams(nextParams, { replace: true });
  };

  const handleCreate = async (
    title: string,
    priority: string,
    alertIds?: string[],
    totalRiskScore?: number
  ) => {
    const result = await createCase.mutateAsync({
      title,
      priority,
      alert_ids: alertIds,
      total_risk_score: totalRiskScore,
    });

    if (alertIds && alertIds.length > 0) {
      for (const aId of alertIds) {
        try {
          await updateAlertStatus.mutateAsync({
            alertId: aId,
            payload: {
              status: 'escalated',
              resolution_notes: `Escalated directly to AML Case #${result.id.slice(0, 8)} (${title})`,
            },
          });
        } catch {
          // ignore non-blocking alert status update
        }
      }
    }

    setShowCreateModal(false);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('create');
    nextParams.delete('from_alert');
    nextParams.delete('alert_id');
    nextParams.delete('title');
    nextParams.delete('priority');
    nextParams.delete('risk_score');
    setSearchParams(nextParams, { replace: true });

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
      <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 rounded-xl bg-[var(--color-surface-subtle)] border border-[var(--color-border-subtle)]">
        <div className="flex flex-wrap items-center gap-3 flex-1 min-w-0">
          {/* Status Filter */}
          <div className="flex items-center gap-1.5 shrink-0">
            <label htmlFor="case-status-filter" className="text-xs font-semibold text-[var(--color-text-muted)]">
              Status:
            </label>
            <select
              id="case-status-filter"
              value={statusFilter}
              onChange={(e) => handleStatusChange(e.target.value)}
              className="px-2.5 py-1.5 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
            >
              <option value="">All Statuses</option>
              {Object.entries(CASE_STATUS_LABELS).map(([val, label]) => (
                <option key={val} value={val}>{label}</option>
              ))}
            </select>
          </div>

          {/* Priority Filter */}
          <div className="flex items-center gap-1.5 shrink-0">
            <label htmlFor="case-priority-filter" className="text-xs font-semibold text-[var(--color-text-muted)]">
              Priority:
            </label>
            <select
              id="case-priority-filter"
              value={priorityFilter}
              onChange={(e) => handlePriorityChange(e.target.value)}
              className="px-2.5 py-1.5 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
            >
              <option value="">All Priorities</option>
              {Object.entries(PRIORITY_LABELS).map(([val, label]) => (
                <option key={val} value={val}>{label}</option>
              ))}
            </select>
          </div>

          {/* Search Input */}
          <div className="relative flex-1 min-w-[180px] max-w-xs">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] pointer-events-none" />
            <input
              id="case-search-input"
              type="text"
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder="Search case, ID, lead..."
              className="w-full pl-7 pr-7 py-1.5 text-xs rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => handleSearchChange('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white cursor-pointer"
                aria-label="Clear search"
              >
                <X size={12} />
              </button>
            )}
          </div>

          {/* Clear Filters Button */}
          {(statusFilter || priorityFilter || searchQuery) && (
            <button
              type="button"
              id="clear-all-case-filters-btn"
              onClick={handleClearAllFilters}
              className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold px-2 py-1 rounded bg-indigo-500/10 hover:bg-indigo-500/20 border border-indigo-500/30 transition-all shrink-0 cursor-pointer"
            >
              Clear Filters
            </button>
          )}
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <span className="text-xs text-[var(--color-text-muted)] font-mono">
            {filteredCases.length} {filteredCases.length === 1 ? 'case' : 'cases'}
            {(statusFilter || priorityFilter || searchQuery) && cases && ` (of ${cases.length})`}
          </span>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white shadow-sm transition-all flex items-center gap-1.5 cursor-pointer shrink-0"
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
      ) : !filteredCases || filteredCases.length === 0 ? (
        <div className="glass-card p-12 text-center text-[var(--color-text-muted)] space-y-3">
          <div className="p-3.5 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 inline-block">
            <span className="text-3xl">📁</span>
          </div>
          {statusFilter || priorityFilter || searchQuery ? (
            <div>
              <p className="text-base font-semibold text-slate-200 mb-1">
                No cases matching filter criteria
              </p>
              <p className="text-xs text-slate-400 mb-4 max-w-md mx-auto">
                {statusFilter && `Status: ${CASE_STATUS_LABELS[statusFilter] || statusFilter}. `}
                {priorityFilter && `Priority: ${PRIORITY_LABELS[priorityFilter] || priorityFilter}. `}
                {searchQuery && `Search: "${searchQuery}". `}
                No investigation cases currently match these criteria.
              </p>
              <button
                type="button"
                id="clear-case-status-filter-btn"
                onClick={handleClearAllFilters}
                className="px-4 py-2 text-xs font-semibold rounded-lg bg-white/10 hover:bg-white/15 text-slate-200 border border-white/10 transition-all cursor-pointer"
              >
                Clear Status Filter
              </button>
            </div>
          ) : (
            <div>
              <p className="text-base font-semibold text-slate-200 mb-1">No cases yet</p>
              <p className="text-xs text-slate-400 mb-4">Create a case to start tracking investigations.</p>
              <button
                type="button"
                id="create-first-case-btn"
                onClick={() => setShowCreateModal(true)}
                className="px-4 py-2 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white shadow-sm transition-all cursor-pointer"
              >
                + Create First Case
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredCases.map((c) => (
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

      {/* Inbound Alert Escalation Banner */}
      {fromAlertId && !showCreateModal && (
        <div className="p-3.5 bg-gradient-to-r from-indigo-950/80 via-purple-950/70 to-slate-900/80 rounded-xl border border-indigo-500/30 flex items-center justify-between gap-3 text-xs shadow-md">
          <div className="flex items-center gap-2">
            <span className="text-base">⚡</span>
            <span className="text-slate-200">
              Inbound alert escalation pending for Alert <span className="font-mono font-bold text-indigo-300">{fromAlertId}</span>
              {initialRiskScore !== undefined && (
                <span className="ml-2 font-mono text-amber-300">
                  (Score: {initialRiskScore.toFixed(1)})
                </span>
              )}
            </span>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-3 py-1.5 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white shadow-sm transition-colors cursor-pointer"
          >
            Open Escalation Wizard
          </button>
        </div>
      )}

      {/* Create Modal */}
      <AnimatePresence>
        {showCreateModal && (
          <CreateCaseModal
            onClose={handleCloseCreateModal}
            onCreate={handleCreate}
            isLoading={createCase.isPending}
            initialTitle={initialTitleParam}
            initialPriority={initialPriorityParam}
            fromAlertId={fromAlertId}
            initialRiskScore={initialRiskScore}
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
  initialTitle = '',
  initialPriority = 'p3_medium',
  fromAlertId,
  initialRiskScore,
}: {
  onClose: () => void;
  onCreate: (title: string, priority: string, alertIds?: string[], totalRiskScore?: number) => void;
  isLoading: boolean;
  initialTitle?: string;
  initialPriority?: string;
  fromAlertId?: string;
  initialRiskScore?: number;
}) {
  const [title, setTitle] = useState(
    initialTitle || (fromAlertId ? `AML Escalation: Alert #${fromAlertId.slice(0, 8)}` : '')
  );
  const [priority, setPriority] = useState(initialPriority || 'p3_medium');
  const [linkedAlertId, setLinkedAlertId] = useState(fromAlertId || '');
  const [riskScore] = useState<number | undefined>(initialRiskScore);

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
          <div className="flex items-center gap-2">
            <span className="text-indigo-400 text-lg">⚡</span>
            <h2 id="create-case-modal-title" className="text-lg font-bold">New Investigation Case</h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="text-[var(--color-text-muted)] hover:text-white p-1 rounded focus:outline-none focus:ring-2 focus:ring-indigo-400 cursor-pointer"
          >
            ✕
          </button>
        </div>

        {fromAlertId && (
          <div className="p-3 bg-gradient-to-r from-indigo-950/70 via-purple-950/60 to-slate-900/80 rounded-lg border border-indigo-500/30 text-xs space-y-1">
            <div className="flex items-center justify-between font-semibold text-indigo-300">
              <span className="flex items-center gap-1.5">
                <span>⚡</span> Escalating from Inbound Alert
              </span>
              {riskScore !== undefined && (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-500/20 text-rose-300 border border-rose-500/30">
                  Risk: {riskScore.toFixed(1)} / 1000
                </span>
              )}
            </div>
            <div className="font-mono text-[11px] text-slate-300 truncate">
              Alert ID: <span className="text-white font-bold">{fromAlertId}</span>
            </div>
          </div>
        )}

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
            className="w-full px-3 py-2 text-sm rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-indigo-400 cursor-pointer"
          >
            {Object.entries(PRIORITY_LABELS).map(([val, label]) => (
              <option key={val} value={val}>{label}</option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="case-alert-id-input" className="block text-xs text-[var(--color-text-muted)] mb-1">
            Linked Alert ID (Optional)
          </label>
          <input
            id="case-alert-id-input"
            value={linkedAlertId}
            onChange={(e) => setLinkedAlertId(e.target.value)}
            placeholder="alert_..."
            className="w-full px-3 py-2 text-sm rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-[var(--color-text)] font-mono focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </div>

        <div className="flex gap-3 justify-end pt-2 border-t border-[var(--color-border)]">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-[var(--color-border)] hover:bg-[var(--color-surface-alt)] focus:outline-none focus:ring-2 focus:ring-indigo-400 cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() =>
              title &&
              onCreate(
                title,
                priority,
                linkedAlertId ? [linkedAlertId] : undefined,
                riskScore
              )
            }
            disabled={!title || isLoading}
            className="px-4 py-2 text-sm font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white shadow-sm transition-all disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-indigo-400 cursor-pointer"
          >
            {isLoading ? 'Creating...' : 'Create Case'}
          </button>
        </div>
      </motion.div>
    </div>
  );
}
