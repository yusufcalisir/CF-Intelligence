import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePilotsList } from '../api/pilots';
import type { PilotStatusResponse, PilotStatus } from '../api/types';
import PilotStatusBadge from '../components/pilots/PilotStatusBadge';
import PilotProvisionModal from '../components/pilots/PilotProvisionModal';
import {
  Shield,
  Plus,
  Search,
  Filter,
  RefreshCw,
  AlertCircle,
  Clock,
  Layers,
  ArrowRight,
  Building,
} from 'lucide-react';

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: 'ALL', label: 'All Statuses' },
  { value: 'DRAFT', label: 'Draft' },
  { value: 'PENDING_APPROVAL', label: 'Pending Approval' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'PREFLIGHT_READY', label: 'Preflight Ready' },
  { value: 'ACTIVE', label: 'Active' },
  { value: 'PAUSED', label: 'Paused' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'ABORTED', label: 'Aborted' },
];

export default function PilotOperationsPage() {
  const navigate = useNavigate();
  const { data: pilots, isLoading, isError, error, refetch, isFetching } = usePilotsList();

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [isProvisionModalOpen, setIsProvisionModalOpen] = useState(false);

  // Compute summary metrics
  const metrics = useMemo(() => {
    if (!pilots) return { total: 0, active: 0, pending: 0, halted: 0 };
    return {
      total: pilots.length,
      active: pilots.filter((p) => p.status === 'ACTIVE').length,
      pending: pilots.filter((p) => ['DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'PREFLIGHT_READY'].includes(p.status)).length,
      halted: pilots.filter((p) => ['PAUSED', 'ABORTED', 'COMPLETED', 'QUOTA_EXCEEDED'].includes(p.status)).length,
    };
  }, [pilots]);

  // Filter pilots based on search and status
  const filteredPilots = useMemo(() => {
    if (!pilots) return [];
    return pilots.filter((p) => {
      const matchesStatus = statusFilter === 'ALL' || p.status === statusFilter;
      const q = searchQuery.toLowerCase().trim();
      const matchesSearch =
        !q ||
        p.pilot_id.toLowerCase().includes(q) ||
        p.consortium_id.toLowerCase().includes(q) ||
        p.participating_institutions?.some((inst) => inst.toLowerCase().includes(q));
      return matchesStatus && matchesSearch;
    });
  }, [pilots, statusFilter, searchQuery]);

  const handlePilotCreated = (newPilotId: string) => {
    setIsProvisionModalOpen(false);
    refetch();
    navigate(`/pilots/${newPilotId}`);
  };

  return (
    <div className="space-y-6 p-4 md:p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-[var(--color-border)] pb-5">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="rounded-lg bg-indigo-500/10 p-2 text-indigo-400">
              <Shield className="h-6 w-6" />
            </div>
            <div>
              <h1 className="text-xl md:text-2xl font-bold tracking-tight text-[var(--color-text-primary)]">
                Consortium Pilot Operations
              </h1>
              <p className="text-xs md:text-sm text-[var(--color-text-muted)] mt-0.5">
                Controlled two-bank pilot execution, authorization verification, and real-time operational telemetry.
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 text-xs font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:border-slate-500 transition-colors disabled:opacity-50"
            title="Refresh pilots list"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>

          <button
            onClick={() => setIsProvisionModalOpen(true)}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 transition-colors"
          >
            <Plus className="h-4 w-4" />
            <span>Provision Pilot</span>
          </button>
        </div>
      </div>

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)]">
            <span>Total Pilots</span>
            <Layers className="h-4 w-4 text-indigo-400" />
          </div>
          <div className="mt-2 text-2xl font-bold font-mono text-[var(--color-text-primary)]">
            {isLoading ? '...' : metrics.total}
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">Registered consortium agreements</div>
        </div>

        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)]">
            <span>Active Pilots</span>
            <div className="h-2 w-2 rounded-full bg-emerald-400 animate-ping" />
          </div>
          <div className="mt-2 text-2xl font-bold font-mono text-emerald-400">
            {isLoading ? '...' : metrics.active}
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">Executing live query traffic</div>
        </div>

        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)]">
            <span>Pending Governance</span>
            <Clock className="h-4 w-4 text-amber-400" />
          </div>
          <div className="mt-2 text-2xl font-bold font-mono text-amber-400">
            {isLoading ? '...' : metrics.pending}
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">Draft, approvals or preflight</div>
        </div>

        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)]">
            <span>Halted / Finished</span>
            <AlertCircle className="h-4 w-4 text-slate-400" />
          </div>
          <div className="mt-2 text-2xl font-bold font-mono text-[var(--color-text-primary)]">
            {isLoading ? '...' : metrics.halted}
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">Paused, aborted or completed</div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-text-muted)]" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by pilot ID, consortium, or participant bank..."
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-primary)] pl-9 pr-3 py-2 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        <div className="flex items-center gap-2">
          <Filter className="h-3.5 w-3.5 text-[var(--color-text-muted)] hidden sm:block" />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-3 py-2 text-xs text-[var(--color-text-primary)] focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Content Area */}
      {isLoading ? (
        <div className="flex min-h-[360px] items-center justify-center rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
            <span className="text-xs font-mono text-[var(--color-text-muted)]">Loading consortium pilots...</span>
          </div>
        </div>
      ) : isError ? (
        <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-6 text-center">
          <AlertCircle className="mx-auto h-8 w-8 text-rose-400 mb-2" />
          <h3 className="text-sm font-semibold text-rose-300">Failed to Load Consortium Pilots</h3>
          <p className="text-xs text-rose-400/80 mt-1 max-w-md mx-auto">
            {error instanceof Error ? error.message : 'An unexpected error occurred while communicating with the pilot registry.'}
          </p>
          <button
            onClick={() => refetch()}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-500 transition-colors"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            <span>Try Again</span>
          </button>
        </div>
      ) : pilots && pilots.length === 0 ? (
        /* Honest empty state when caller's tenant participates in no pilots */
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-bg-secondary)] py-16 px-4 text-center">
          <div className="rounded-full bg-indigo-500/10 p-4 text-indigo-400 mb-3">
            <Shield className="h-10 w-10 stroke-1" />
          </div>
          <h3 className="text-base font-semibold text-[var(--color-text-primary)]">
            No Registered Consortium Pilots
          </h3>
          <p className="mt-1 text-xs text-[var(--color-text-muted)] max-w-md">
            Your institution is not currently participating in any active or drafted pilots.
            Authorized consortium operators can provision a new controlled pilot to begin bilateral collaboration.
          </p>
          <button
            onClick={() => setIsProvisionModalOpen(true)}
            className="mt-5 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 transition-colors"
          >
            <Plus className="h-4 w-4" />
            <span>Provision First Pilot</span>
          </button>
        </div>
      ) : filteredPilots.length === 0 ? (
        /* Filter returned zero hits */
        <div className="flex flex-col items-center justify-center rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] py-12 px-4 text-center">
          <Filter className="h-8 w-8 text-slate-500 mb-2" />
          <h3 className="text-sm font-medium text-[var(--color-text-primary)]">No Matching Pilots Found</h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            No pilots match your current filter or search criteria.
          </p>
          <button
            onClick={() => {
              setSearchQuery('');
              setStatusFilter('ALL');
            }}
            className="mt-3 text-xs text-indigo-400 hover:text-indigo-300 underline"
          >
            Clear Filters
          </button>
        </div>
      ) : (
        /* Pilot Grid / Cards */
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredPilots.map((pilot: PilotStatusResponse) => {
            const usagePercent =
              pilot.queries_total_quota > 0
                ? Math.min(100, Math.round((pilot.queries_executed / pilot.queries_total_quota) * 100))
                : 0;

            return (
              <div
                key={pilot.pilot_id}
                onClick={() => navigate(`/pilots/${pilot.pilot_id}`)}
                className="group relative flex flex-col justify-between rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-5 hover:border-indigo-500/50 hover:bg-[var(--color-bg-secondary)]/80 transition-all cursor-pointer shadow-sm"
              >
                <div>
                  {/* Card Header: Pilot ID & Status */}
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-mono text-xs font-bold text-indigo-400 truncate group-hover:text-indigo-300">
                        {pilot.pilot_id}
                      </div>
                      <div className="text-[11px] text-[var(--color-text-muted)] mt-0.5 truncate">
                        Consortium: <span className="font-mono">{pilot.consortium_id}</span>
                      </div>
                    </div>
                    <PilotStatusBadge status={pilot.status as PilotStatus} />
                  </div>

                  {/* Institutions Badge Strip */}
                  <div className="mt-4 flex items-center gap-1.5 text-xs text-[var(--color-text-primary)]">
                    <Building className="h-3.5 w-3.5 text-slate-400 shrink-0" />
                    <span className="font-mono text-[11px] bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700">
                      {pilot.participating_institutions?.[0] || 'Bank A'}
                    </span>
                    <span className="text-[10px] text-slate-500 font-bold">⇄</span>
                    <span className="font-mono text-[11px] bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700">
                      {pilot.participating_institutions?.[1] || 'Bank B'}
                    </span>
                  </div>

                  {/* Scope Digest preview */}
                  <div className="mt-3.5 pt-3 border-t border-[var(--color-border)] text-[11px] text-[var(--color-text-muted)] space-y-1">
                    <div className="flex items-center justify-between font-mono">
                      <span>Scope Digest:</span>
                      <span className="text-slate-400">
                        {pilot.scope_digest ? `${pilot.scope_digest.slice(0, 10)}...` : 'N/A'}
                      </span>
                    </div>

                    {/* Quota Progress */}
                    <div className="mt-2 space-y-1">
                      <div className="flex items-center justify-between text-[10px]">
                        <span>Quota Utilization:</span>
                        <span className="font-mono font-medium text-[var(--color-text-primary)]">
                          {pilot.queries_executed} / {pilot.queries_total_quota} ({usagePercent}%)
                        </span>
                      </div>
                      <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${
                            usagePercent >= 90
                              ? 'bg-rose-500'
                              : usagePercent >= 70
                              ? 'bg-amber-500'
                              : 'bg-indigo-500'
                          }`}
                          style={{ width: `${usagePercent}%` }}
                        />
                      </div>
                    </div>
                  </div>
                </div>

                {/* Card Footer: Navigation link */}
                <div className="mt-5 pt-3 border-t border-[var(--color-border)] flex items-center justify-between text-xs">
                  <span className="text-[10px] text-[var(--color-text-muted)] font-mono">
                    {pilot.created_at ? new Date(pilot.created_at).toLocaleDateString() : ''}
                  </span>
                  <div className="flex items-center gap-1 font-medium text-indigo-400 group-hover:translate-x-0.5 transition-transform">
                    <span>Manage Pilot</span>
                    <ArrowRight className="h-3.5 w-3.5" />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Provision Pilot Modal */}
      <PilotProvisionModal
        isOpen={isProvisionModalOpen}
        onClose={() => setIsProvisionModalOpen(false)}
        onSuccess={handlePilotCreated}
      />
    </div>
  );
}
