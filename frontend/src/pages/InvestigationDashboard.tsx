import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import {
  useDashboardStats,
  useAlertsBySeverity,
  useAlertsByBank,
  useIntelligenceStats,
  useAuditLogs,
} from '../api/queries';
import { BANK_NAMES, SEVERITY_COLORS } from '../api/types';

export default function InvestigationDashboard() {
  const { data: stats, isLoading } = useDashboardStats();
  const { data: alertsBySeverity } = useAlertsBySeverity();
  const { data: alertsByBank } = useAlertsByBank();
  const { data: intelStats } = useIntelligenceStats();
  const { data: auditLogs } = useAuditLogs();

  const statCards = stats ? [
    {
      id: 'alerts',
      title: 'Total Alerts',
      value: stats.total_alerts,
      badge: 'LIVE INGESTION',
      trend: '↑ Real-time Stream',
      subtext: 'Cross-bank 9-signal risk scoring feeds',
      gradient: 'from-amber-500/20 via-amber-500/5 to-transparent',
      borderColor: 'border-amber-500/30 hover:border-amber-400/60',
      textColor: 'from-amber-200 via-amber-400 to-yellow-500',
      badgeColor: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
      barColor: 'from-amber-500 to-yellow-400',
      glow: 'rgba(245, 158, 11, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-amber-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      ),
      href: '/alerts',
    },
    {
      id: 'critical',
      title: 'Critical Alerts',
      value: stats.critical_alerts,
      badge: 'SEV1-P1 CRITICAL',
      trend: 'Risk Score ≥ 700',
      subtext: 'Immediate block & supervisor triage',
      gradient: 'from-rose-500/20 via-rose-500/5 to-transparent',
      borderColor: 'border-rose-500/30 hover:border-rose-400/60',
      textColor: 'from-rose-200 via-rose-400 to-red-500',
      badgeColor: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
      barColor: 'from-rose-500 to-red-400',
      glow: 'rgba(244, 63, 94, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-rose-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      ),
      href: '/alerts',
    },
    {
      id: 'cases',
      title: 'Open Cases',
      value: stats.open_cases,
      badge: 'WORKBENCH 4-EYES',
      trend: 'Pending Dual Sig',
      subtext: '6-stage investigation state machine',
      gradient: 'from-indigo-500/20 via-indigo-500/5 to-transparent',
      borderColor: 'border-indigo-500/30 hover:border-indigo-400/60',
      textColor: 'from-indigo-200 via-indigo-400 to-purple-400',
      badgeColor: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30',
      barColor: 'from-indigo-500 to-purple-400',
      glow: 'rgba(99, 102, 241, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-indigo-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />
        </svg>
      ),
      href: '/cases',
    },
    {
      id: 'entities',
      title: 'Entities',
      value: stats.total_entities,
      badge: 'LSH FUZZY PSI',
      trend: 'Privacy Bounded',
      subtext: 'Resolved cross-bank identity nodes',
      gradient: 'from-teal-500/20 via-teal-500/5 to-transparent',
      borderColor: 'border-teal-500/30 hover:border-teal-400/60',
      textColor: 'from-teal-200 via-teal-400 to-emerald-400',
      badgeColor: 'bg-teal-500/10 text-teal-400 border-teal-500/30',
      barColor: 'from-teal-500 to-emerald-400',
      glow: 'rgba(20, 184, 166, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-teal-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" />
          <path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
        </svg>
      ),
      href: '/graph',
    },
    {
      id: 'intel',
      title: 'Intelligence Items',
      value: stats.shared_intelligence_items,
      badge: 'FL CONSORTIUM',
      trend: '(ε, δ)-DP Bounded',
      subtext: 'Privacy-preserving threat indicators',
      gradient: 'from-violet-500/20 via-violet-500/5 to-transparent',
      borderColor: 'border-violet-500/30 hover:border-violet-400/60',
      textColor: 'from-violet-200 via-violet-400 to-purple-500',
      badgeColor: 'bg-violet-500/10 text-violet-400 border-violet-500/30',
      barColor: 'from-violet-500 to-purple-400',
      glow: 'rgba(139, 92, 246, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-violet-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
          <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
        </svg>
      ),
      href: '/alerts',
    },
    {
      id: 'clusters',
      title: 'Graph Clusters',
      value: stats.graph_clusters,
      badge: 'NEO4J / GraphSAGE',
      trend: 'Community Detect',
      subtext: 'Multi-bank transaction ring topologies',
      gradient: 'from-pink-500/20 via-pink-500/5 to-transparent',
      borderColor: 'border-pink-500/30 hover:border-pink-400/60',
      textColor: 'from-pink-200 via-pink-400 to-rose-400',
      badgeColor: 'bg-pink-500/10 text-pink-400 border-pink-500/30',
      barColor: 'from-pink-500 to-rose-400',
      glow: 'rgba(236, 72, 153, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-pink-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="6" cy="6" r="3" /><circle cx="18" cy="6" r="3" /><circle cx="12" cy="18" r="3" />
          <line x1="8.5" y1="7.5" x2="15.5" y2="7.5" /><line x1="7.5" y1="8.5" x2="10.5" y2="15.5" />
          <line x1="16.5" y1="8.5" x2="13.5" y2="15.5" />
        </svg>
      ),
      href: '/graph',
    },
    {
      id: 'scenarios',
      title: 'Active Scenarios',
      value: stats.active_scenarios,
      badge: 'SIMULATION LAB',
      trend: 'Streaming Edge',
      subtext: 'Typology simulation scenario generators',
      gradient: 'from-cyan-500/20 via-cyan-500/5 to-transparent',
      borderColor: 'border-cyan-500/30 hover:border-cyan-400/60',
      textColor: 'from-cyan-200 via-cyan-400 to-blue-400',
      badgeColor: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
      barColor: 'from-cyan-500 to-blue-400',
      glow: 'rgba(6, 182, 212, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-cyan-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polygon points="5 3 19 12 5 21 5 3" />
        </svg>
      ),
      href: '/scenarios',
    },
    {
      id: 'cross',
      title: 'Cross-Institution',
      value: stats.cross_institution_matches,
      badge: 'MULTI-BANK SYNC',
      trend: '3 Bank Nodes',
      subtext: 'Simultaneous velocity & layering hits',
      gradient: 'from-orange-500/20 via-orange-500/5 to-transparent',
      borderColor: 'border-orange-500/30 hover:border-orange-400/60',
      textColor: 'from-orange-200 via-orange-400 to-amber-400',
      badgeColor: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
      barColor: 'from-orange-500 to-amber-400',
      glow: 'rgba(249, 115, 22, 0.15)',
      icon: (
        <svg className="w-3.5 h-3.5 text-orange-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 21h18" /><path d="M3 10h18" /><path d="M5 6l7-3 7 3" /><path d="M4 10v11" />
          <path d="M20 10v11" /><path d="M8 14v3" /><path d="M12 14v3" /><path d="M16 14v3" />
        </svg>
      ),
      href: '/graph',
    },
  ] : [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-white/6 pb-5"
      >
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 uppercase tracking-widest">
              AML Intelligence Control
            </span>
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-100 tracking-tight">
            Investigation Dashboard
          </h1>
          <p className="text-xs sm:text-sm text-slate-400 max-w-2xl mt-1 leading-relaxed">
            Aggregated real-time metrics across multi-bank alerts, cases, entities, and privacy-preserving federated intelligence feeds.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Link
            to="/scenarios"
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 transition-all shadow-[0_0_20px_rgba(99,102,241,0.4)]"
          >
            ▶ Run Scenario
          </Link>
          <Link
            to="/cases"
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold text-slate-300 hover:text-white bg-white/5 border border-white/10 hover:bg-white/10 transition-all"
          >
            📋 Workbench
          </Link>
        </div>
      </motion.div>

      {/* High-Impact Stat Cards */}
      {isLoading ? (
        <div className="p-12 text-center text-slate-400 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl">
          <div className="inline-block w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin mb-2" />
          <div className="text-xs font-mono">Synchronizing consortium intelligence metrics...</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 sm:gap-5">
          {statCards.map((card, i) => (
            <motion.div
              key={card.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05, duration: 0.4 }}
            >
              <Link
                to={card.href}
                className={`relative overflow-hidden rounded-2xl bg-[#090a1f]/80 border ${card.borderColor} p-4 sm:p-5 shadow-[0_0_40px_rgba(0,0,0,0.4)] backdrop-blur-xl transition-all duration-300 hover:scale-[1.02] hover:-translate-y-1 block group min-w-0`}
                style={{ boxShadow: `0 0 35px ${card.glow}` }}
              >
                {/* Ambient Top Radial Gradient Glow */}
                <div className={`absolute -top-12 -right-12 w-32 h-32 rounded-full bg-gradient-to-br ${card.gradient} blur-2xl opacity-60 group-hover:opacity-100 transition-opacity`} />

                {/* Top Header Row: Dedicated Icon Container, Badge & Trend Pill */}
                <div className="flex items-center justify-between gap-2 mb-3 relative z-10 min-w-0">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <div className={`p-1.5 rounded-lg border ${card.badgeColor} shrink-0 flex items-center justify-center`}>
                      {card.icon}
                    </div>
                    <span className="text-[9.5px] font-mono font-bold uppercase tracking-wider text-slate-300 truncate min-w-0">
                      {card.badge}
                    </span>
                  </div>
                  <div className="text-[9.5px] font-mono text-slate-300 bg-white/5 border border-white/10 px-2 py-0.5 rounded-full shrink-0 font-medium whitespace-nowrap">
                    {card.trend}
                  </div>
                </div>

                {/* Main Number Display */}
                <div className="my-2.5 relative z-10">
                  <div className={`text-3xl sm:text-4xl font-extrabold font-mono tracking-tight bg-gradient-to-r ${card.textColor} bg-clip-text text-transparent group-hover:scale-105 transition-transform origin-left`}>
                    {card.value.toLocaleString()}
                  </div>
                  <div className="text-xs font-bold text-slate-200 mt-1 uppercase tracking-wider">
                    {card.title}
                  </div>
                </div>

                {/* Subtext description */}
                <div className="text-[10.5px] font-mono text-slate-400 line-clamp-1 relative z-10 mt-1">
                  {card.subtext}
                </div>

                {/* Bottom Accent Indicator Line */}
                <div className="w-full h-1 bg-white/5 rounded-full overflow-hidden mt-3 relative z-10">
                  <div className={`h-full rounded-full bg-gradient-to-r ${card.barColor} w-2/3 group-hover:w-full transition-all duration-500`} />
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      )}

      {/* Analytics Breakdown Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Alerts by Severity */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl space-y-4 flex flex-col justify-between min-w-0"
        >
          <div className="flex items-center justify-between border-b border-white/6 pb-3 gap-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-1.5 min-w-0">
              <span className="w-2 h-2 rounded-full bg-rose-400 shrink-0" />
              <span className="truncate">Alerts by Severity</span>
            </h3>
            <span className="text-[10px] font-mono text-slate-500 shrink-0 bg-white/5 px-2 py-0.5 rounded-full border border-white/5">Live Breakdown</span>
          </div>

          {alertsBySeverity && Object.keys(alertsBySeverity).length > 0 ? (
            <div className="space-y-3">
              {Object.entries(alertsBySeverity)
                .sort(([a], [b]) => {
                  const order = ['critical', 'high', 'medium', 'low', 'info'];
                  return order.indexOf(a) - order.indexOf(b);
                })
                .map(([severity, count]) => {
                  const total = Object.values(alertsBySeverity).reduce((a, b) => a + b, 0);
                  const pct = total > 0 ? (count / total) * 100 : 0;
                  const color = SEVERITY_COLORS[severity] || '#6b7280';
                  return (
                    <div key={severity} className="space-y-1">
                      <div className="flex justify-between text-xs">
                        <span className="capitalize font-semibold text-slate-300 flex items-center gap-1.5">
                          <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
                          {severity}
                        </span>
                        <span className="font-mono font-bold text-slate-200">
                          {count.toLocaleString()} <span className="text-[10px] text-slate-500 font-normal">({pct.toFixed(1)}%)</span>
                        </span>
                      </div>
                      <div className="h-2 bg-white/5 rounded-full overflow-hidden p-0.5 border border-white/5">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${pct}%` }}
                          transition={{ duration: 0.8, ease: 'easeOut' }}
                          className="h-full rounded-full"
                          style={{ backgroundColor: color, boxShadow: `0 0 10px ${color}` }}
                        />
                      </div>
                    </div>
                  );
                })}
            </div>
          ) : (
            <p className="text-xs text-slate-500 py-4 text-center font-mono">No alert severity data available</p>
          )}
        </motion.div>

        {/* Alerts by Bank */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl space-y-4 flex flex-col justify-between min-w-0"
        >
          <div className="flex items-center justify-between border-b border-white/6 pb-3 gap-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-1.5 min-w-0">
              <span className="w-2 h-2 rounded-full bg-indigo-400 shrink-0" />
              <span className="truncate">Consortium Distribution</span>
            </h3>
            <span className="text-[10px] font-mono text-slate-500 shrink-0 bg-white/5 px-2 py-0.5 rounded-full border border-white/5">3 Bank Nodes</span>
          </div>

          {alertsByBank && Object.keys(alertsByBank).length > 0 ? (
            <div className="space-y-3">
              {Object.entries(alertsByBank).map(([bankId, count]) => {
                const total = Object.values(alertsByBank).reduce((a, b) => a + b, 0);
                const pct = total > 0 ? (count / total) * 100 : 0;
                const bankColor = bankId === 'bank_a' ? '#6366f1' : bankId === 'bank_b' ? '#14b8a6' : '#f59e0b';
                return (
                  <div key={bankId} className="space-y-1">
                    <div className="flex justify-between text-xs">
                      <span className="font-semibold text-slate-300 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: bankColor }} />
                        {BANK_NAMES[bankId] || bankId}
                      </span>
                      <span className="font-mono font-bold text-slate-200">
                        {count.toLocaleString()} <span className="text-[10px] text-slate-500 font-normal">({pct.toFixed(1)}%)</span>
                      </span>
                    </div>
                    <div className="h-2 bg-white/5 rounded-full overflow-hidden p-0.5 border border-white/5">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${pct}%` }}
                        transition={{ duration: 0.8, ease: 'easeOut' }}
                        className="h-full rounded-full"
                        style={{ backgroundColor: bankColor, boxShadow: `0 0 10px ${bankColor}` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-slate-500 py-4 text-center font-mono">No bank distribution data available</p>
          )}
        </motion.div>

        {/* Intelligence Summary */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl space-y-4 flex flex-col justify-between min-w-0"
        >
          <div className="flex items-center justify-between border-b border-white/6 pb-3 gap-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-1.5 min-w-0">
              <span className="w-2 h-2 rounded-full bg-violet-400 shrink-0" />
              <span className="truncate">Shared Intelligence Matrix</span>
            </h3>
            <span className="text-[10px] font-mono text-slate-500 shrink-0 bg-white/5 px-2 py-0.5 rounded-full border border-white/5">DP Guarded</span>
          </div>

          {intelStats && intelStats.total_items > 0 ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2.5 text-center">
                <div className="p-2.5 rounded-xl bg-violet-500/10 border border-violet-500/20 overflow-hidden">
                  <div className="text-xl sm:text-2xl font-black font-mono text-violet-300 truncate">
                    {intelStats.total_items.toLocaleString()}
                  </div>
                  <div className="text-[9.5px] font-mono text-slate-400 mt-0.5 truncate">Total Shared Items</div>
                </div>

                <div className="p-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 overflow-hidden">
                  <div className="text-lg sm:text-xl font-black font-mono text-emerald-300 truncate">
                    {(intelStats.avg_risk_indicator * 100).toFixed(1)}%
                  </div>
                  <div className="text-[9.5px] font-mono text-slate-400 mt-0.5 truncate">Avg Risk Indicator</div>
                </div>
              </div>

              <div>
                <h4 className="text-[10px] font-mono uppercase text-slate-400 mb-2 font-semibold tracking-wider">Breakdown By Threat Object</h4>
                <div className="space-y-1.5">
                  {Object.entries(intelStats.items_by_type).map(([type, count]) => (
                    <div key={type} className="flex justify-between items-center text-xs p-2 rounded-lg bg-white/3 border border-white/5">
                      <span className="text-slate-300 capitalize text-[11px] font-mono truncate mr-2">
                        {type.replace(/_/g, ' ')}
                      </span>
                      <span className="font-mono font-bold text-violet-300 shrink-0 px-1.5 py-0.5 rounded bg-violet-500/10 border border-violet-500/20">{count}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-6 space-y-2">
              <div className="text-3xl">🔗</div>
              <p className="text-xs text-slate-400 font-mono">
                No shared intelligence yet. Trigger a scenario to generate cross-bank threat objects.
              </p>
            </div>
          )}
        </motion.div>
      </div>

      {/* Quick Actions */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
        className="p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl"
      >
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 mb-3.5 flex items-center gap-2">
          ⚡ Operational Quick Shortcuts
        </h3>
        <div className="flex flex-wrap gap-2.5">
          {[
            { label: '▶ Run Fraud Ring Scenario', href: '/scenarios', color: '#6366f1' },
            { label: '🔍 View Ingestion Alerts', href: '/alerts', color: '#f59e0b' },
            { label: '📋 Open Case Workbench', href: '/cases', color: '#14b8a6' },
            { label: '🕸️ Explore Identity Graph', href: '/graph', color: '#ec4899' },
            { label: '🌐 Return to Simulator', href: '/', color: '#3b82f6' },
          ].map((link) => (
            <Link
              key={link.label}
              to={link.href}
              className="px-4 py-2 text-xs font-semibold rounded-xl border border-white/10 bg-white/3 hover:bg-white/10 hover:border-white/20 transition-all text-slate-200"
              style={{ boxShadow: `0 0 15px ${link.color}20` }}
            >
              {link.label}
            </Link>
          ))}
        </div>
      </motion.div>

      {/* Audit Logs */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.6 }}
        className="p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl mt-6"
      >
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-white/6 pb-3 mb-4">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-1.5 flex-wrap min-w-0">
            <span>🕵️</span>
            <span>Investigator Activity Audit Trail</span>
            <span className="text-slate-500 text-[10px] font-normal font-mono">(Immutable Log Chain)</span>
          </h3>
          <span className="self-start sm:self-auto text-[10px] font-mono font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-0.5 rounded-full whitespace-nowrap shrink-0 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            SHA-256 Verified
          </span>
        </div>

        {/* Mobile View: Stacked Cards (No horizontal scrolling, details fully visible) */}
        <div className="block md:hidden space-y-3">
          {!auditLogs || auditLogs.length === 0 ? (
            <div className="py-6 text-center text-slate-500 font-mono text-xs">
              No investigator activity logs recorded yet.
            </div>
          ) : (
            auditLogs.map((log: any) => (
              <div
                key={log.id}
                className="p-3 rounded-xl bg-slate-900/60 border border-white/5 space-y-2 font-mono text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-bold text-indigo-400">{log.investigator}</span>
                  <span className="px-2 py-0.5 rounded bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-[10px] font-bold uppercase">
                    {log.action.replace(/_/g, ' ')}
                  </span>
                </div>
                <div className="flex flex-col gap-1 text-[11px] text-slate-400 border-t border-white/5 pt-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-500">Target ID:</span>
                    <span className="text-slate-200 font-semibold truncate max-w-[180px]">{log.target_id}</span>
                  </div>
                  <div className="flex items-center justify-between gap-2 text-[10px]">
                    <span className="text-slate-500">Timestamp:</span>
                    <span className="text-slate-400">{new Date(log.timestamp).toLocaleString()}</span>
                  </div>
                </div>
                {log.metadata && (
                  <div className="text-[10px] text-slate-400 bg-black/40 p-2 rounded border border-white/5 break-all mt-1">
                    <span className="text-slate-500 font-semibold block mb-0.5">Details:</span>
                    {typeof log.metadata === 'object' ? JSON.stringify(log.metadata) : String(log.metadata)}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {/* Desktop View: Full Table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-white/10 text-slate-400 font-mono text-[11px]">
                <th className="py-2.5 px-3">Investigator</th>
                <th className="py-2.5 px-3">Action</th>
                <th className="py-2.5 px-3">Target ID</th>
                <th className="py-2.5 px-3">Timestamp</th>
                <th className="py-2.5 px-3 text-right">Details</th>
              </tr>
            </thead>
            <tbody>
              {!auditLogs || auditLogs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-slate-500 font-mono">
                    No investigator activity logs recorded yet.
                  </td>
                </tr>
              ) : (
                auditLogs.map((log: any) => (
                  <tr key={log.id} className="border-b border-white/5 hover:bg-white/3 transition-colors font-mono text-[11px]">
                    <td className="py-2.5 px-3 font-semibold text-indigo-400">{log.investigator}</td>
                    <td className="py-2.5 px-3 uppercase font-bold text-slate-200">{log.action.replace(/_/g, ' ')}</td>
                    <td className="py-2.5 px-3 text-slate-400">{log.target_id.slice(0, 10)}...</td>
                    <td className="py-2.5 px-3 text-slate-500">{new Date(log.timestamp).toLocaleString()}</td>
                    <td className="py-2.5 px-3 text-right text-[10px] text-slate-500 max-w-xs truncate">
                      {JSON.stringify(log.metadata)}
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
