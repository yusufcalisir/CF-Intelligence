import { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { useBankDistributions } from '../../api/queries';
import { BANK_COLORS, BANK_NAMES } from '../../api/types';

// Tab definitions for data distributions
const DIST_TABS = [
  { id: 'amount', label: 'Transaction Amount', icon: '💰' },
  { id: 'hourly', label: 'Hourly Fraud Pattern', icon: '🕐' },
  { id: 'merchant', label: 'Merchant Risk', icon: '🏪' },
] as const;

type DistTabId = (typeof DIST_TABS)[number]['id'];
type ViewMode = 'distributions' | 'feature_drift' | 'concept_drift';

export default function DataDriftPanel() {
  const { data, isLoading } = useBankDistributions();
  const [viewMode, setViewMode] = useState<ViewMode>('distributions');
  const [activeDistTab, setActiveDistTab] = useState<DistTabId>('amount');
  const [showEduGuide, setShowEduGuide] = useState(false);

  // Prepare chart data for distributions
  const amountData = useMemo(() => {
    if (!data) return [];
    const bankA = data.banks.bank_a?.amount_histogram;
    const bankB = data.banks.bank_b?.amount_histogram;
    const bankC = data.banks.bank_c?.amount_histogram;
    if (!bankA || !bankB || !bankC) return [];

    return bankA.bins.slice(0, -1).map((bin, i) => ({
      bin: bin < 1000 ? `$${Math.round(bin)}` : `$${(bin / 1000).toFixed(1)}k`,
      binValue: bin,
      bank_a: bankA.counts[i] ?? 0,
      bank_b: bankB.counts[i] ?? 0,
      bank_c: bankC.counts[i] ?? 0,
    }));
  }, [data]);

  const hourlyData = useMemo(() => {
    if (!data) return [];
    const bankA = data.banks.bank_a?.hourly_fraud_rate;
    const bankB = data.banks.bank_b?.hourly_fraud_rate;
    const bankC = data.banks.bank_c?.hourly_fraud_rate;
    if (!bankA || !bankB || !bankC) return [];

    return bankA.hours.map((h, i) => {
      const aTotal = bankA.total[i] ?? 0;
      const bTotal = bankB.total[i] ?? 0;
      const cTotal = bankC.total[i] ?? 0;
      const aFraud = bankA.fraud[i] ?? 0;
      const bFraud = bankB.fraud[i] ?? 0;
      const cFraud = bankC.fraud[i] ?? 0;
      return {
        hour: `${h.toString().padStart(2, '0')}:00`,
        bank_a: aTotal > 0 ? +((aFraud / aTotal) * 100).toFixed(2) : 0,
        bank_b: bTotal > 0 ? +((bFraud / bTotal) * 100).toFixed(2) : 0,
        bank_c: cTotal > 0 ? +((cFraud / cTotal) * 100).toFixed(2) : 0,
      };
    });
  }, [data]);

  const merchantData = useMemo(() => {
    if (!data) return [];
    const allCategories = new Set<string>();
    Object.values(data.banks).forEach((bank) => {
      bank.merchant_risk.categories.forEach((c) => allCategories.add(c));
    });

    return Array.from(allCategories)
      .slice(0, 10)
      .map((cat) => {
        const row: Record<string, string | number> = {
          category: cat.replace(/_/g, ' '),
        };
        for (const [bankId, bankData] of Object.entries(data.banks)) {
          const idx = bankData.merchant_risk.categories.indexOf(cat);
          const rate = idx >= 0 ? (bankData.merchant_risk.fraud_rates[idx] ?? 0) : 0;
          row[bankId] = +(rate * 100).toFixed(2);
        }
        return row;
      })
      .sort((a, b) => {
        const sumA = (a.bank_a as number) + (a.bank_b as number) + (a.bank_c as number);
        const sumB = (b.bank_a as number) + (b.bank_b as number) + (b.bank_c as number);
        return sumB - sumA;
      });
  }, [data]);

  const nonIIDScore = data?.divergence_summary?.overall_non_iid_score ?? 0;
  const featureDrift = data?.divergence_summary?.feature_drift ?? {};
  const conceptDrift = data?.divergence_summary?.concept_drift ?? {};

  // Find overall average PSI/JS drift metrics for summary cards
  const summaryMetrics = useMemo(() => {
    if (!data || !data.divergence_summary?.feature_drift) {
      return { avgFeaturePsi: 0, avgFeatureJs: 0, avgConceptPsi: 0, avgConceptJs: 0 };
    }
    const fdList = Object.values(data.divergence_summary.feature_drift);
    const cdList = Object.values(data.divergence_summary.concept_drift ?? {});
    
    const avgFeaturePsi = fdList.reduce((acc, curr) => acc + curr.overall_psi, 0) / Math.max(fdList.length, 1);
    const avgFeatureJs = fdList.reduce((acc, curr) => acc + curr.overall_js, 0) / Math.max(fdList.length, 1);
    
    const avgConceptPsi = cdList.reduce((acc, curr) => acc + curr.overall_psi, 0) / Math.max(cdList.length, 1);
    const avgConceptJs = cdList.reduce((acc, curr) => acc + curr.overall_js, 0) / Math.max(cdList.length, 1);
    
    return { avgFeaturePsi, avgFeatureJs, avgConceptPsi, avgConceptJs };
  }, [data]);

  // Helper for status badge styling
  const getStatusStyle = (status: 'stable' | 'moderate' | 'drifted') => {
    switch (status) {
      case 'stable':
        return { bg: 'rgba(16, 185, 129, 0.1)', text: '#10B981', border: 'rgba(16, 185, 129, 0.2)' };
      case 'moderate':
        return { bg: 'rgba(245, 158, 11, 0.1)', text: '#F59E0B', border: 'rgba(245, 158, 11, 0.2)' };
      case 'drifted':
        return { bg: 'rgba(239, 68, 68, 0.1)', text: '#EF4444', border: 'rgba(239, 68, 68, 0.2)' };
      default:
        return { bg: 'rgba(255, 255, 255, 0.05)', text: '#FFF', border: 'rgba(255, 255, 255, 0.1)' };
    }
  };

  if (isLoading) {
    return (
      <div className="glass-card p-4">
        <div className="flex items-center gap-3 mb-4">
          <div className="h-4 w-48 bg-[var(--color-bg-elevated)] rounded animate-pulse" />
          <div className="h-6 w-20 bg-[var(--color-bg-elevated)] rounded-full animate-pulse ml-auto" />
        </div>
        <div className="h-48 bg-[var(--color-bg-elevated)] rounded-lg animate-pulse" />
      </div>
    );
  }

  if (!data) return null;


  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.2 }}
      className="glass-card p-4 overflow-hidden flex flex-col gap-4"
    >
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xs font-semibold text-[var(--color-text-primary)] uppercase tracking-wider">
              Data Drift & Heterogeneity Analytics
            </h2>
            <button
              onClick={() => setShowEduGuide(!showEduGuide)}
              className="text-[10px] px-2 py-0.5 rounded border border-[var(--color-border-subtle)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] transition"
              title="Learn about PSI and JS Divergence"
            >
              📖 Guide
            </button>
          </div>
          <p className="text-[10px] text-[var(--color-text-muted)] mt-0.5">
            Statistical divergence and drift metrics between Bank cohorts A, B, and C
          </p>
        </div>

        {/* Global Drift Summary Badges */}
        <div className="flex flex-wrap gap-2 items-center">
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-medium border cursor-help"
            style={{
              background: 'rgba(99,102,241,0.05)',
              borderColor: 'rgba(99,102,241,0.2)',
              color: 'var(--color-accent-indigo-light)'
            }}
            title="Average PSI for features. Value >= 0.25 suggests major structural difference."
          >
            <span>Feature Drift:</span>
            <span className="font-mono font-bold text-[var(--color-accent-indigo-light)]">PSI {summaryMetrics.avgFeaturePsi.toFixed(2)}</span>
          </div>
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-medium border cursor-help"
            style={{
              background: 'rgba(20,184,166,0.05)',
              borderColor: 'rgba(20,184,166,0.2)',
              color: 'var(--color-accent-teal-light)'
            }}
            title="Jensen-Shannon prediction probability divergence. Values closer to 1 are highly drifted."
          >
            <span>Concept Drift:</span>
            <span className="font-mono font-bold text-[var(--color-accent-teal-light)]">JS {summaryMetrics.avgConceptJs.toFixed(2)}</span>
          </div>
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-semibold border cursor-help bg-[rgba(255,255,255,0.03)] border-[var(--color-border-subtle)] text-[var(--color-text-primary)]"
            title="Average Kolmogorov-Smirnov distance of amounts across banks."
          >
            Non-IID: {(nonIIDScore * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      {/* Educational Guide Overlay */}
      <AnimatePresence>
        {showEduGuide && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden bg-[var(--color-bg-primary)] rounded-lg border border-[var(--color-border-subtle)] p-3 text-[10px] leading-relaxed flex flex-col gap-2 text-[var(--color-text-secondary)] shadow-inner"
          >
            <div className="flex items-center justify-between border-b border-[var(--color-border-subtle)] pb-1.5 mb-1">
              <span className="font-semibold text-[var(--color-text-primary)]">📊 Statistical Metric Guide</span>
              <button onClick={() => setShowEduGuide(false)} className="text-[9px] hover:text-[var(--color-text-primary)]">✖ Close</button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <p className="font-medium text-[var(--color-text-primary)] mb-0.5">🧠 PSI (Population Stability Index)</p>
                <p className="text-[9px] text-[var(--color-text-muted)] space-y-1">
                  <span>Measures distribution change over populations.</span>
                  <br />
                  <span className="inline-block mt-0.5">
                    Formula: <code className="font-mono text-cyan-400 bg-cyan-950/40 px-1.5 py-0.5 rounded border border-cyan-500/20 text-[9px]">∑ (A<sub>i</sub> - E<sub>i</sub>) × ln(A<sub>i</sub> / E<sub>i</sub>)</code>
                  </span>
                  <br />
                  <span className="inline-block mt-1">
                    🟢 <span className="text-[#10B981]">&lt;0.10</span> Stable | 🟡 <span className="text-[#F59E0B]">0.10–0.25</span> Moderate Shift | 🔴 <span className="text-[#EF4444]">&gt;0.25</span> Significant Drift (requires Federated Training).
                  </span>
                </p>
              </div>
              <div>
                <p className="font-medium text-[var(--color-text-primary)] mb-0.5">🔬 Jensen-Shannon (JS) Divergence</p>
                <p className="text-[9px] text-[var(--color-text-muted)] leading-relaxed">
                  Symmetric, smooth measure of distribution divergence bounded between{' '}
                  <code className="font-mono text-cyan-400 bg-cyan-950/40 px-1 py-0.2 rounded border border-cyan-500/20 text-[9px]">0.0</code> (identical) and{' '}
                  <code className="font-mono text-cyan-400 bg-cyan-950/40 px-1 py-0.2 rounded border border-cyan-500/20 text-[9px]">1.0</code> (disjoint).
                  Helps identify local feature alignment. Used directly to capture concept drift on model outputs{' '}
                  <code className="font-mono text-cyan-400 bg-cyan-950/40 px-1 py-0.2 rounded border border-cyan-500/20 text-[9px]">P(Y|X)</code>.
                </p>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main Mode View Selector Toggles */}
      <div className="flex gap-1 bg-[var(--color-bg-primary)] rounded-lg p-1">
        {(['distributions', 'feature_drift', 'concept_drift'] as const).map((mode) => (
          <button
            key={mode}
            onClick={() => setViewMode(mode)}
            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-[10px] font-medium transition-all duration-200 ${
              viewMode === mode
                ? 'bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)] shadow-sm border border-[var(--color-border-subtle)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]'
            }`}
          >
            <span>
              {mode === 'distributions' ? '📊 Distributions' : mode === 'feature_drift' ? '🧬 Feature Drift' : '🧠 Concept Drift'}
            </span>
          </button>
        ))}
      </div>

      {/* Dynamic Content Panel */}
      <AnimatePresence mode="wait">
        <motion.div
          key={viewMode}
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -5 }}
          transition={{ duration: 0.2 }}
          className="flex-1"
        >
          {/* VIEW: DISTRIBUTIONS */}
          {viewMode === 'distributions' && (
            <div className="flex flex-col gap-4">
              <div className="flex gap-1 bg-[var(--color-bg-primary)] rounded-md p-0.5 w-max">
                {DIST_TABS.map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveDistTab(tab.id)}
                    className={`flex items-center justify-center gap-1 px-2.5 py-1 rounded text-[9px] font-medium transition-all duration-150 ${
                      activeDistTab === tab.id
                        ? 'bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)] shadow-sm border border-[var(--color-border-subtle)]'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]'
                    }`}
                  >
                    <span>{tab.icon}</span>
                    <span>{tab.label}</span>
                  </button>
                ))}
              </div>
              <div className="h-56 sm:h-64">
                {activeDistTab === 'amount' && <AmountChart data={amountData} />}
                {activeDistTab === 'hourly' && <HourlyChart data={hourlyData} />}
                {activeDistTab === 'merchant' && <MerchantChart data={merchantData} />}
              </div>
            </div>
          )}

          {/* VIEW: FEATURE DRIFT */}
          {viewMode === 'feature_drift' && (
            <div className="flex flex-col gap-3">
              <div className="overflow-x-auto rounded-lg border border-[var(--color-border-subtle)]">
                <table className="w-full border-collapse text-left text-[10px]">
                  <thead>
                    <tr className="bg-[var(--color-bg-primary)] border-b border-[var(--color-border-subtle)] text-[var(--color-text-muted)]">
                      <th className="p-2.5 font-semibold">Feature Name</th>
                      <th className="p-2.5 font-semibold">Type</th>
                      <th className="p-2.5 font-semibold">Meridian ↔ Nexus</th>
                      <th className="p-2.5 font-semibold">Meridian ↔ Heritage</th>
                      <th className="p-2.5 font-semibold">Nexus ↔ Heritage</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--color-border-subtle)]">
                    {['transaction_amount', 'velocity', 'hour_of_day', 'merchant_category', 'device_type'].map((feature) => {
                      const getCellData = (pairKey: string) => {
                        const info = featureDrift[pairKey]?.features?.[feature];
                        return info || { psi: 0, js_divergence: 0, ks: null, status: 'stable' };
                      };
                      return (
                        <tr key={feature} className="hover:bg-[var(--color-bg-primary)] transition-colors">
                          <td className="p-2.5 font-medium text-[var(--color-text-primary)]">
                            {feature.replace(/_/g, ' ')}
                          </td>
                          <td className="p-2.5 text-[9px] text-[var(--color-text-muted)] italic capitalize">
                            {feature === 'merchant_category' || feature === 'device_type' ? 'Categorical' : 'Continuous'}
                          </td>
                          {['a_vs_b', 'a_vs_c', 'b_vs_c'].map((pair) => {
                            const val = getCellData(pair);
                            const style = getStatusStyle(val.status);
                            return (
                              <td key={pair} className="p-2.5">
                                <div className="flex flex-col gap-0.5">
                                  <div className="flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ backgroundColor: style.text }} />
                                    <span className="font-semibold text-[var(--color-text-primary)] font-mono">
                                      PSI {val.psi.toFixed(2)}
                                    </span>
                                  </div>
                                  <div className="text-[9px] text-[var(--color-text-muted)] flex gap-1 font-mono">
                                    <span>JS: {val.js_divergence.toFixed(2)}</span>
                                    {val.ks !== undefined && val.ks !== null && (
                                      <span>| KS: {val.ks.toFixed(2)}</span>
                                    )}
                                  </div>
                                </div>
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="text-[9px] text-[var(--color-text-muted)] italic text-right">
                * Note: Categorical features do not support Kolmogorov-Smirnov (KS) testing.
              </div>
            </div>
          )}

          {/* VIEW: CONCEPT DRIFT */}
          {viewMode === 'concept_drift' && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {['a_vs_b', 'a_vs_c', 'b_vs_c'].map((pair) => {
                const info = conceptDrift[pair];
                if (!info) return null;
                
                const pairName = pair === 'a_vs_b'
                  ? 'Meridian ↔ Nexus'
                  : pair === 'a_vs_c'
                  ? 'Meridian ↔ Heritage'
                  : 'Nexus ↔ Heritage';
                
                const predDrift = info.model_prediction_drift;
                const statusStyle = getStatusStyle(predDrift.status);
                
                return (
                  <div
                    key={pair}
                    className="p-3 rounded-lg border bg-[rgba(255,255,255,0.01)] flex flex-col gap-2.5 transition border-[var(--color-border-subtle)]"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-semibold text-[var(--color-text-primary)]">{pairName}</span>
                      <span
                        className="px-1.5 py-0.5 rounded text-[8px] font-bold border tracking-wide uppercase font-mono"
                        style={{
                          backgroundColor: statusStyle.bg,
                          color: statusStyle.text,
                          borderColor: statusStyle.border,
                        }}
                      >
                        {predDrift.status}
                      </span>
                    </div>

                    {/* Prediction Probability Shift Card */}
                    <div className="bg-[var(--color-bg-primary)] p-2 rounded border border-[var(--color-border-subtle)] flex flex-col gap-1.5">
                      <div className="text-[9px] text-[var(--color-text-muted)] uppercase tracking-wider font-semibold">
                        Fraud Prediction Probability Shift
                      </div>
                      <div className="flex items-end justify-between font-mono">
                        <span className="text-xs text-[var(--color-text-primary)] font-bold">
                          PSI: {predDrift.psi.toFixed(2)}
                        </span>
                        <span className="text-[9px] text-[var(--color-text-secondary)]">
                          JS Divergence: {predDrift.js_divergence.toFixed(2)}
                        </span>
                      </div>
                      <div className="w-full bg-[var(--color-bg-elevated)] h-1 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{
                            width: `${Math.min(predDrift.psi * 100, 100)}%`,
                            backgroundColor: statusStyle.text,
                          }}
                        />
                      </div>
                    </div>

                    {/* Conditional Segment Drifts */}
                    <div className="flex flex-col gap-2">
                      <div className="text-[9px] text-[var(--color-text-muted)] uppercase tracking-wider font-semibold">
                        Fraud Pattern Shifts by Segment
                      </div>
                      <div className="flex flex-col gap-1.5 text-[9px] font-mono">
                        <div className="flex justify-between items-center">
                          <span className="text-[var(--color-text-secondary)]">Hour of Day (JS):</span>
                          <span className="text-[var(--color-text-primary)] font-semibold">
                            {(info.conditional_drifts.hour_of_day ?? 0).toFixed(2)}
                          </span>
                        </div>
                        <div className="flex justify-between items-center font-mono">
                          <span className="text-[var(--color-text-secondary)]">Merchant Type (JS):</span>
                          <span className="text-[var(--color-text-primary)] font-semibold">
                            {(info.conditional_drifts.merchant_category ?? 0).toFixed(2)}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </motion.div>
      </AnimatePresence>

      {/* Legend */}
      <div className="flex items-center justify-center gap-4 mt-1 pt-3 border-t border-[var(--color-border-subtle)]">
        {(['bank_a', 'bank_b', 'bank_c'] as const).map((bankId) => (
          <div key={bankId} className="flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
            <div
              className="w-2.5 h-2.5 rounded-sm animate-pulse"
              style={{ backgroundColor: BANK_COLORS[bankId] }}
            />
            {BANK_NAMES[bankId]}
          </div>
        ))}
      </div>
    </motion.div>
  );
}

// ── Sub-Charts ──────────────────────────────────────

function AmountChart({ data }: { data: Record<string, unknown>[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
        <defs>
          {(['bank_a', 'bank_b', 'bank_c'] as const).map((bankId) => (
            <linearGradient key={bankId} id={`grad-${bankId}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={BANK_COLORS[bankId]} stopOpacity={0.3} />
              <stop offset="95%" stopColor={BANK_COLORS[bankId]} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" opacity={0.5} />
        <XAxis
          dataKey="bin"
          tick={{ fontSize: 9, fill: 'var(--color-text-muted)' }}
          interval="preserveStartEnd"
          tickLine={false}
          axisLine={{ stroke: 'var(--color-border-subtle)' }}
        />
        <YAxis
          tick={{ fontSize: 9, fill: 'var(--color-text-muted)' }}
          tickLine={false}
          axisLine={false}
          width={35}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--color-bg-elevated)',
            border: '1px solid var(--color-border-subtle)',
            borderRadius: '8px',
            fontSize: '10px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
          labelStyle={{ color: 'var(--color-text-primary)', fontWeight: 600, marginBottom: 4 }}
          itemStyle={{ padding: 0 }}
        />
        {(['bank_a', 'bank_b', 'bank_c'] as const).map((bankId) => (
          <Area
            key={bankId}
            type="monotone"
            dataKey={bankId}
            stroke={BANK_COLORS[bankId]}
            strokeWidth={2}
            fill={`url(#grad-${bankId})`}
            name={BANK_NAMES[bankId]}
            animationDuration={800}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

function HourlyChart({ data }: { data: Record<string, unknown>[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }} barGap={1} barCategoryGap="20%">
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" opacity={0.5} />
        <XAxis
          dataKey="hour"
          tick={{ fontSize: 8, fill: 'var(--color-text-muted)' }}
          interval={2}
          tickLine={false}
          axisLine={{ stroke: 'var(--color-border-subtle)' }}
        />
        <YAxis
          tick={{ fontSize: 9, fill: 'var(--color-text-muted)' }}
          tickLine={false}
          axisLine={false}
          width={35}
          tickFormatter={(v: number) => `${v}%`}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--color-bg-elevated)',
            border: '1px solid var(--color-border-subtle)',
            borderRadius: '8px',
            fontSize: '10px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
          labelStyle={{ color: 'var(--color-text-primary)', fontWeight: 600, marginBottom: 4 }}
          formatter={(value: number) => [`${value}%`, '']}
        />
        {(['bank_a', 'bank_b', 'bank_c'] as const).map((bankId) => (
          <Bar
            key={bankId}
            dataKey={bankId}
            fill={BANK_COLORS[bankId]}
            radius={[2, 2, 0, 0]}
            name={BANK_NAMES[bankId]}
            animationDuration={800}
            opacity={0.85}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function MerchantChart({ data }: { data: Record<string, unknown>[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 5, right: 10, left: 5, bottom: 5 }}
        barGap={1}
        barCategoryGap="15%"
      >
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" opacity={0.5} horizontal={false} />
        <XAxis
          type="number"
          tick={{ fontSize: 9, fill: 'var(--color-text-muted)' }}
          tickLine={false}
          axisLine={{ stroke: 'var(--color-border-subtle)' }}
          tickFormatter={(v: number) => `${v}%`}
        />
        <YAxis
          type="category"
          dataKey="category"
          tick={{ fontSize: 9, fill: 'var(--color-text-muted)' }}
          tickLine={false}
          axisLine={false}
          width={80}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--color-bg-elevated)',
            border: '1px solid var(--color-border-subtle)',
            borderRadius: '8px',
            fontSize: '10px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
          labelStyle={{ color: 'var(--color-text-primary)', fontWeight: 600, marginBottom: 4, textTransform: 'capitalize' }}
          formatter={(value: number) => [`${value}%`, '']}
        />
        <Legend content={() => null} />
        {(['bank_a', 'bank_b', 'bank_c'] as const).map((bankId) => (
          <Bar
            key={bankId}
            dataKey={bankId}
            fill={BANK_COLORS[bankId]}
            radius={[0, 3, 3, 0]}
            name={BANK_NAMES[bankId]}
            animationDuration={800}
            opacity={0.85}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
