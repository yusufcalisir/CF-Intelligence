import { useState, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { usePilotStatus } from '../api/pilots';
import type { PilotStatus } from '../api/types';
import PilotStatusBadge from '../components/pilots/PilotStatusBadge';
import PilotScopeSection from '../components/pilots/PilotScopeSection';
import PilotPreflightSection from '../components/pilots/PilotPreflightSection';
import PilotApprovalsSection from '../components/pilots/PilotApprovalsSection';
import PilotControlsSection from '../components/pilots/PilotControlsSection';
import PilotTelemetrySection from '../components/pilots/PilotTelemetrySection';
import PilotDossierSection from '../components/pilots/PilotDossierSection';
import {
  ArrowLeft,
  RefreshCw,
  AlertCircle,
  Clock,
  Layers,
  FileCheck2,
  Users,
  Sliders,
  Activity,
  FileText,
  AlertTriangle,
  Building,
} from 'lucide-react';

type TabKey = 'overview' | 'preflight' | 'approvals' | 'controls' | 'telemetry' | 'dossier';

export default function PilotDetailPage() {
  const { pilotId } = useParams<{ pilotId: string }>();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TabKey>('overview');

  const {
    data: pilot,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = usePilotStatus(pilotId);

  // Tab definitions with dynamic badges/indicators
  const tabs = useMemo(() => {
    const missingCount = pilot?.missing_approvals?.length ?? 0;
    const hasPreflight = (pilot?.preflight_checks?.length ?? 0) > 0;
    const allPreflightPassed = hasPreflight && pilot?.preflight_checks.every((c) => c.passed);

    return [
      {
        key: 'overview' as TabKey,
        label: 'Scope & Configuration',
        icon: Layers,
      },
      {
        key: 'preflight' as TabKey,
        label: 'Preflight Readiness',
        icon: FileCheck2,
        badge: hasPreflight ? (allPreflightPassed ? 'Passed' : 'Failed') : undefined,
        badgeColor: allPreflightPassed ? 'emerald' : 'amber',
      },
      {
        key: 'approvals' as TabKey,
        label: 'Institutional Approvals',
        icon: Users,
        badge: missingCount > 0 ? `${missingCount} Pending` : 'Complete',
        badgeColor: missingCount > 0 ? 'amber' : 'emerald',
      },
      {
        key: 'controls' as TabKey,
        label: 'Operational Controls',
        icon: Sliders,
      },
      {
        key: 'telemetry' as TabKey,
        label: 'Telemetry & Execution',
        icon: Activity,
      },
      {
        key: 'dossier' as TabKey,
        label: 'Audit & Dossier',
        icon: FileText,
      },
    ];
  }, [pilot]);

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
          <span className="text-xs font-mono text-[var(--color-text-muted)]">
            Loading pilot lifecycle data...
          </span>
        </div>
      </div>
    );
  }

  if (isError || !pilot) {
    return (
      <div className="max-w-4xl mx-auto p-6 mt-8">
        <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-6 text-center">
          <AlertCircle className="mx-auto h-8 w-8 text-rose-400 mb-2" />
          <h2 className="text-base font-semibold text-rose-300">Consortium Pilot Not Accessible</h2>
          <p className="text-xs text-rose-400/80 mt-1 max-w-md mx-auto">
            {error instanceof Error
              ? error.message
              : 'Pilot could not be loaded. Please ensure the pilot ID is valid and your institution is authorized to view this record.'}
          </p>
          <div className="mt-5 flex items-center justify-center gap-3">
            <button
              onClick={() => refetch()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-rose-700/50 bg-rose-900/40 px-3 py-1.5 text-xs font-medium text-rose-200 hover:bg-rose-900/60 transition-colors"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              <span>Retry</span>
            </button>
            <Link
              to="/pilots"
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>Back to Pilots</span>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const isExpired = pilot.has_expired || (pilot.expires_at && new Date(pilot.expires_at) <= new Date());

  return (
    <div className="space-y-6 p-4 md:p-6 max-w-7xl mx-auto">
      {/* Top Navigation & Breadcrumb */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-3">
        <button
          onClick={() => navigate('/pilots')}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>All Consortium Pilots</span>
        </button>

        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2.5 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:border-slate-500 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${isFetching ? 'animate-spin' : ''}`} />
          <span>Sync State</span>
        </button>
      </div>

      {/* Main Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl md:text-2xl font-bold font-mono text-[var(--color-text-primary)]">
              {pilot.pilot_id}
            </h1>
            <PilotStatusBadge status={pilot.status as PilotStatus} />
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-[var(--color-text-muted)]">
            <span>
              Consortium: <strong className="font-mono text-slate-300">{pilot.consortium_id}</strong>
            </span>
            <span>•</span>
            <span>
              Purposes: <span className="font-mono text-slate-300">{pilot.allowed_purposes?.join(', ') || 'N/A'}</span>
            </span>
            <span>•</span>
            <div className="flex items-center gap-1">
              <Building className="h-3 w-3 text-slate-400" />
              <span className="font-mono text-slate-300">{pilot.participating_institutions?.[0] || 'Bank A'}</span>
              <span className="text-[10px] text-slate-500 font-bold">⇄</span>
              <span className="font-mono text-slate-300">{pilot.participating_institutions?.[1] || 'Bank B'}</span>
            </div>
          </div>
        </div>

        {/* Quick Quota Pill */}
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-4 py-2 text-right">
          <div className="text-[11px] text-[var(--color-text-muted)]">Quota Consumed</div>
          <div className="font-mono text-sm font-semibold text-[var(--color-text-primary)]">
            {pilot.queries_executed} / {pilot.queries_total_quota} queries
          </div>
        </div>
      </div>

      {/* Alert Notices */}
      {pilot.status === 'PAUSED' && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-950/20 p-4 text-xs text-amber-200 flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-amber-300">Pilot Execution Paused</div>
            <div className="text-amber-200/80 mt-0.5">
              Live operational queries between {pilot.participating_institutions?.join(' and ') || 'participants'} are temporarily halted.
              Authorized operators can resume once conditions are cleared.
            </div>
          </div>
        </div>
      )}

      {pilot.status === 'ABORTED' && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-4 text-xs text-rose-200 flex items-start gap-3">
          <AlertCircle className="h-5 w-5 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-rose-300">Pilot Permanently Aborted</div>
            <div className="text-rose-200/80 mt-0.5">
              This pilot was aborted via emergency intervention. No further queries, approvals or transitions are permitted.
            </div>
          </div>
        </div>
      )}

      {isExpired && pilot.status !== 'ABORTED' && pilot.status !== 'COMPLETED' && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-4 text-xs text-rose-200 flex items-start gap-3">
          <Clock className="h-5 w-5 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-rose-300">Pilot Scope Expired</div>
            <div className="text-rose-200/80 mt-0.5">
              The operational validity window for this pilot has expired ({pilot.expires_at}). Query execution is prohibited.
            </div>
          </div>
        </div>
      )}

      {/* Navigation Tabs */}
      <div className="border-b border-[var(--color-border)]">
        <nav className="flex space-x-1 md:space-x-2 overflow-x-auto pb-px" aria-label="Tabs">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs font-medium transition-all ${
                  isActive
                    ? 'border-indigo-500 text-indigo-400'
                    : 'border-transparent text-[var(--color-text-muted)] hover:border-slate-500 hover:text-[var(--color-text-primary)]'
                }`}
              >
                <Icon className={`h-4 w-4 ${isActive ? 'text-indigo-400' : 'text-slate-400'}`} />
                <span>{tab.label}</span>
                {tab.badge && (
                  <span
                    className={`ml-1 rounded-full px-2 py-0.5 text-[10px] font-mono font-medium ${
                      tab.badgeColor === 'emerald'
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                        : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
                    }`}
                  >
                    {tab.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab Content Panes */}
      <div className="mt-4">
        {activeTab === 'overview' && <PilotScopeSection pilot={pilot} />}
        {activeTab === 'preflight' && <PilotPreflightSection pilot={pilot} />}
        {activeTab === 'approvals' && <PilotApprovalsSection pilot={pilot} />}
        {activeTab === 'controls' && <PilotControlsSection pilot={pilot} />}
        {activeTab === 'telemetry' && <PilotTelemetrySection pilot={pilot} />}
        {activeTab === 'dossier' && <PilotDossierSection pilot={pilot} />}
      </div>
    </div>
  );
}
