/**
 * PrivacyBudgetWidget — Phase 15.1
 *
 * Renders the empirical DP-SGD privacy-utility frontier data:
 *   - Epsilon vs PR-AUC scatter with Pareto frontier overlay
 *   - Noise multiplier calibration summary card
 *   - Budget exhaustion status indicators per configuration
 *
 * Accepts static sweep result data (from dp_sweep_results.json) or
 * live simulation budget data from the PrivacyMonitor telemetry stream.
 */

import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  ReferenceLine,
  Legend,
} from 'recharts';

// ---------------------------------------------------------------------------
// Type definitions (mirrors DPConfigResult from run_dp_noise_sweep.py)
// ---------------------------------------------------------------------------

export interface DPConfigResult {
  sigma: number;
  num_rounds: number;
  epsilon: number;
  optimal_alpha: number;
  delta: number;
  budget_exhausted: boolean;
  pr_auc: number;
  roc_auc: number;
  runtime_seconds: number;
}

export interface ParetoPoint {
  epsilon: number;
  pr_auc: number;
}

export interface DPSweepData {
  configurations: DPConfigResult[];
  calibrated_sigma: number | null;
  target_epsilon: number;
  delta: number;
  q: number;
  pareto_frontier: ParetoPoint[];
}

interface PrivacyBudgetWidgetProps {
  sweepData: DPSweepData;
  /** Compact mode hides the secondary chart and shows only the KPI cards */
  compact?: boolean;
}

// ---------------------------------------------------------------------------
// Colour mappings
// ---------------------------------------------------------------------------

const SIGMA_COLOURS: Record<string, string> = {
  '0.5': '#e74c3c',
  '1.0': '#e67e22',
  '1.5': '#27ae60',
  '2.0': '#2980b9',
};

function sigmaColour(sigma: number): string {
  return SIGMA_COLOURS[sigma.toFixed(1)] ?? '#8884d8';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function KPICard({
  label,
  value,
  sub,
  alert,
}: {
  label: string;
  value: string;
  sub?: string;
  alert?: boolean;
}) {
  return (
    <div
      className="p-3.5 rounded-xl border"
      style={{
        background: alert
          ? 'color-mix(in srgb, #e74c3c 8%, transparent)'
          : 'rgba(0,0,0,0.18)',
        borderColor: alert ? 'rgba(231,76,60,0.3)' : 'var(--color-border-subtle)',
      }}
    >
      <p
        className="text-[9px] uppercase tracking-wider font-medium"
        style={{ color: 'var(--color-text-muted)' }}
      >
        {label}
      </p>
      <p
        className="text-lg font-bold font-mono mt-1"
        style={{ color: alert ? '#e74c3c' : 'var(--color-text-primary)' }}
      >
        {value}
      </p>
      {sub && (
        <p className="text-[9px] mt-0.5" style={{ color: 'var(--color-text-muted)' }}>
          {sub}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main widget
// ---------------------------------------------------------------------------

export default function PrivacyBudgetWidget({
  sweepData,
  compact = false,
}: PrivacyBudgetWidgetProps) {
  const { configurations, calibrated_sigma, target_epsilon, delta, pareto_frontier } = sweepData;

  // Scatter data: each configuration is one point, coloured by sigma
  const scatterPoints = configurations.map((c) => ({
    x: c.epsilon,
    y: c.pr_auc,
    sigma: c.sigma,
    num_rounds: c.num_rounds,
    budget_exhausted: c.budget_exhausted,
    colour: sigmaColour(c.sigma),
  }));

  // Epsilon vs sigma curves (for each round count)
  const roundCounts = [...new Set(configurations.map((c) => c.num_rounds))].sort(
    (a, b) => a - b,
  );
  const sigmas = [...new Set(configurations.map((c) => c.sigma))].sort((a, b) => a - b);

  const convergenceData = sigmas.map((sigma) => {
    const row: Record<string, number> = { sigma };
    for (const T of roundCounts) {
      const match = configurations.find((c) => c.sigma === sigma && c.num_rounds === T);
      if (match) row[`T${T}`] = parseFloat(match.epsilon.toFixed(4));
    }
    return row;
  });

  // Summary stats
  const nExhausted = configurations.filter((c) => c.budget_exhausted).length;
  const bestConfig = configurations.length > 0
    ? configurations.reduce<DPConfigResult | undefined>(
        (best, c) => (!best || c.pr_auc > best.pr_auc ? c : best),
        configurations[0],
      )
    : undefined;

  const roundColors: Record<number, string> = {
    5: '#9b59b6',
    10: '#3498db',
    20: '#1abc9c',
    50: '#e74c3c',
  };

  return (
    <div className="glass-card p-6 flex flex-col gap-6">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div
        className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b"
        style={{ borderColor: 'var(--color-border-subtle)' }}
      >
        <div className="flex items-center gap-2.5">
          <div className="text-xl">🔏</div>
          <div>
            <h3
              className="text-sm font-semibold"
              style={{ color: 'var(--color-text-primary)' }}
            >
              DP-SGD Noise Calibration &amp; Privacy-Utility Frontier
            </h3>
            <p className="text-[10px]" style={{ color: 'var(--color-text-muted)' }}>
              Phase 15.1 — RDP Moments Accountant · δ={delta.toExponential(0)} fixed
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="text-[10px] px-2 py-0.5 rounded-full font-medium"
            style={{
              background: 'color-mix(in srgb, var(--color-accent-indigo) 15%, transparent)',
              color: 'var(--color-accent-indigo-light)',
            }}
          >
            Target ε ≤ {target_epsilon}
          </span>
          {calibrated_sigma !== null && (
            <span
              className="text-[10px] px-2 py-0.5 rounded-full font-medium"
              style={{
                background: 'color-mix(in srgb, #27ae60 15%, transparent)',
                color: '#27ae60',
              }}
            >
              σ* = {calibrated_sigma.toFixed(4)}
            </span>
          )}
        </div>
      </div>

      {/* ── KPI Cards ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KPICard
          label="Calibrated σ*"
          value={calibrated_sigma !== null ? calibrated_sigma.toFixed(4) : '—'}
          sub={`ε ≤ ${target_epsilon} at T=${Math.max(...roundCounts)}`}
        />
        <KPICard
          label="Best PR-AUC"
          value={bestConfig ? bestConfig.pr_auc.toFixed(4) : '—'}
          sub={bestConfig ? `σ=${bestConfig.sigma.toFixed(1)}, T=${bestConfig.num_rounds}` : '—'}
        />
        <KPICard
          label="Configurations"
          value={`${configurations.length}`}
          sub={`${sigmas.length} σ × ${roundCounts.length} T grids`}
        />
        <KPICard
          label="Budget Overruns"
          value={`${nExhausted}`}
          sub={`of ${configurations.length} configs`}
          alert={nExhausted > 0}
        />
      </div>

      {/* ── Charts ─────────────────────────────────────────────────────── */}
      {!compact && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Panel A: Privacy-Utility Scatter */}
          <div className="flex flex-col gap-2">
            <p
              className="text-xs font-semibold"
              style={{ color: 'var(--color-text-primary)' }}
            >
              Privacy-Utility Frontier (ε vs PR-AUC)
            </p>
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--color-border-subtle)"
                  />
                  <XAxis
                    dataKey="x"
                    type="number"
                    name="ε"
                    tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
                    label={{
                      value: 'Privacy Loss ε (↓ more private)',
                      position: 'insideBottom',
                      offset: -8,
                      fill: 'var(--color-text-muted)',
                      fontSize: 9,
                    }}
                  />
                  <YAxis
                    dataKey="y"
                    type="number"
                    name="PR-AUC"
                    tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
                    label={{
                      value: 'PR-AUC',
                      angle: -90,
                      position: 'insideLeft',
                      fill: 'var(--color-text-muted)',
                      fontSize: 9,
                    }}
                  />
                  <Tooltip
                    cursor={{ strokeDasharray: '3 3' }}
                    contentStyle={{
                      background: 'var(--color-bg-card)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 8,
                      fontSize: 11,
                    }}
                    formatter={(value: any, name: any) => [
                      typeof value === 'number' ? value.toFixed(4) : String(value ?? ''),
                      String(name ?? ''),
                    ]}
                  />
                  {/* Pareto frontier line */}
                  {pareto_frontier.length > 1 && (
                    <Scatter
                      name="Pareto frontier"
                      data={pareto_frontier.map((p) => ({ x: p.epsilon, y: p.pr_auc }))}
                      line={{ stroke: '#95a5a6', strokeDasharray: '4 2', strokeWidth: 1.5 }}
                      fill="transparent"
                    />
                  )}
                  {/* Group by sigma */}
                  {sigmas.map((sigma) => (
                    <Scatter
                      key={sigma}
                      name={`σ=${sigma.toFixed(1)}`}
                      data={scatterPoints.filter((p) => p.sigma === sigma)}
                      fill={sigmaColour(sigma)}
                      opacity={0.85}
                    />
                  ))}
                  <Legend
                    verticalAlign="top"
                    wrapperStyle={{ fontSize: 10, paddingBottom: 4 }}
                  />
                  {target_epsilon && (
                    <ReferenceLine
                      x={target_epsilon}
                      stroke="#e74c3c"
                      strokeDasharray="4 2"
                      label={{ value: `ε=${target_epsilon}`, fill: '#e74c3c', fontSize: 9 }}
                    />
                  )}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Panel B: ε vs σ curves */}
          <div className="flex flex-col gap-2">
            <p
              className="text-xs font-semibold"
              style={{ color: 'var(--color-text-primary)' }}
            >
              RDP Composition Curves (ε vs σ)
            </p>
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={convergenceData}
                  margin={{ top: 10, right: 20, bottom: 20, left: 10 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--color-border-subtle)"
                  />
                  <XAxis
                    dataKey="sigma"
                    tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
                    label={{
                      value: 'Noise Multiplier σ',
                      position: 'insideBottom',
                      offset: -8,
                      fill: 'var(--color-text-muted)',
                      fontSize: 9,
                    }}
                  />
                  <YAxis
                    tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
                    label={{
                      value: 'Privacy Loss ε',
                      angle: -90,
                      position: 'insideLeft',
                      fill: 'var(--color-text-muted)',
                      fontSize: 9,
                    }}
                  />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--color-bg-card)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 8,
                      fontSize: 11,
                    }}
                    formatter={(value: any) => [
                      typeof value === 'number' ? value.toFixed(4) : String(value ?? ''),
                      'ε',
                    ]}
                    labelFormatter={(label) => `σ = ${label}`}
                  />
                  <Legend
                    verticalAlign="top"
                    wrapperStyle={{ fontSize: 10, paddingBottom: 4 }}
                  />
                  {roundCounts.map((T) => (
                    <Line
                      key={T}
                      type="monotone"
                      dataKey={`T${T}`}
                      name={`T=${T} rounds`}
                      stroke={roundColors[T] ?? '#888'}
                      strokeWidth={2}
                      dot={{ r: 4 }}
                      activeDot={{ r: 6 }}
                    />
                  ))}
                  {target_epsilon && (
                    <ReferenceLine
                      y={target_epsilon}
                      stroke="#e74c3c"
                      strokeDasharray="4 2"
                      label={{
                        value: `Target ε=${target_epsilon}`,
                        fill: '#e74c3c',
                        fontSize: 9,
                        position: 'right',
                      }}
                    />
                  )}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* ── Interpretation callout ─────────────────────────────────────── */}
      <div
        className="p-3 rounded-lg border text-[10px] leading-relaxed"
        style={{
          background: 'color-mix(in srgb, var(--color-accent-indigo) 5%, transparent)',
          borderColor: 'color-mix(in srgb, var(--color-accent-indigo) 20%, transparent)',
          color: 'var(--color-text-muted)',
        }}
      >
        <strong style={{ color: 'var(--color-text-primary)' }}>RDP Moments Accountant: </strong>
        Privacy bounds are computed via Rényi Differential Privacy composition
        (Mironov 2017) with optimal-order convex-dual conversion to (ε,δ)-DP.
        Higher σ → stronger privacy guarantee (lower ε) → minor utility penalty.
        {calibrated_sigma !== null && (
          <>
            {' '}Calibrated σ* = {calibrated_sigma.toFixed(4)} achieves ε ≤ {target_epsilon}{' '}
            at T={Math.max(...roundCounts)} rounds with δ={delta.toExponential(0)}.
          </>
        )}
      </div>
    </div>
  );
}
