import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import type { SimulationDetail, TrainingRound } from '../../api/types';

interface PrivacyMonitorProps {
  simulation: SimulationDetail;
  rounds: TrainingRound[];
}

export default function PrivacyMonitor({ simulation, rounds }: PrivacyMonitorProps) {
  const { config, current_round } = simulation;
  const isSaEnabled = config.privacy_mechanism === 'secure_aggregation' || config.privacy_mechanism === 'both';
  const isDpEnabled = config.privacy_mechanism === 'differential_privacy' || config.privacy_mechanism === 'both';

  if (!isDpEnabled) {
    return null;
  }

  const isOpacus = config.dp_mode === 'opacus';
  const targetEpsilon = config.dp_epsilon;
  const targetDelta = config.dp_delta;

  // Retrieve current spent privacy budget
  const hasData = rounds && rounds.length > 0;
  const currentEpsilon = rounds?.[rounds.length - 1]?.privacy_budget ?? 0;
  
  // Under composition, total delta accumulates per round
  const currentDelta = targetDelta * current_round;

  // Calculate percentage of budget consumed
  const consumptionPercentage = Math.min(100, (currentEpsilon / targetEpsilon) * 100);

  // Generate chart data representing cumulative epsilon spent at each round
  const chartPlaceholder = Array.from({ length: config.num_rounds }, (_, i) => ({
    round: i + 1,
    epsilon: null as number | null,
  }));

  const data = chartPlaceholder.map((placeholder) => {
    const actual = rounds.find((r) => r.round_number === placeholder.round);
    if (actual) {
      return {
        round: actual.round_number,
        epsilon: parseFloat(actual.privacy_budget.toFixed(4)),
      };
    }
    return placeholder;
  });

  return (
    <div className="glass-card p-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-[var(--color-border-subtle)]">
        <div className="flex items-center gap-2.5">
          <div className="text-xl">🛡️</div>
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
              Differential Privacy (DP) Monitor
            </h3>
            <p className="text-[10px] text-[var(--color-text-muted)]">
              Mathematical guarantees bounding individual transaction leakage
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] px-2 py-0.5 rounded-full font-medium"
            style={{
              background: 'color-mix(in srgb, var(--color-accent-indigo) 15%, transparent)',
              color: 'var(--color-accent-indigo-light)',
            }}
          >
            {isOpacus ? 'Opacus (Moments Accountant)' : 'Post-Hoc (Gaussian Mechanism)'}
          </span>
          {isSaEnabled && (
            <span
              className="text-[10px] px-2 py-0.5 rounded-full font-medium"
              style={{
                background: 'color-mix(in srgb, var(--color-accent-teal) 15%, transparent)',
                color: 'var(--color-accent-teal)',
              }}
            >
              + SecAgg Active
            </span>
          )}
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Side: Stats and Consumption */}
        <div className="lg:col-span-5 flex flex-col gap-5 justify-between">
          <div className="space-y-4">
            {/* Stats Boxes */}
            <div className="grid grid-cols-2 gap-3">
              <div className="p-3.5 rounded-xl bg-black/20 border border-[var(--color-border-subtle)]">
                <p className="text-[9px] text-[var(--color-text-muted)] uppercase tracking-wider">Spent Epsilon (ε)</p>
                <p className="text-xl font-bold font-mono text-[var(--color-text-primary)] mt-1">
                  {currentEpsilon.toFixed(4)}
                </p>
                <p className="text-[9px] text-[var(--color-text-muted)] mt-0.5">Limit: {targetEpsilon.toFixed(2)}</p>
              </div>

              <div className="p-3.5 rounded-xl bg-black/20 border border-[var(--color-border-subtle)]">
                <p className="text-[9px] text-[var(--color-text-muted)] uppercase tracking-wider">Total Delta (δ)</p>
                <p className="text-xl font-bold font-mono text-[var(--color-text-primary)] mt-1">
                  {currentDelta === 0 ? '0' : currentDelta.toExponential(2)}
                </p>
                <p className="text-[9px] text-[var(--color-text-muted)] mt-0.5">Round δ: {targetDelta.toExponential(1)}</p>
              </div>
            </div>

            {/* Consumption Progress Bar */}
            <div className="space-y-1.5 p-4 rounded-xl bg-black/10 border border-[var(--color-border-subtle)]/40">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-[var(--color-text-muted)]">Privacy Budget Spent</span>
                <span className="font-bold font-mono text-[var(--color-text-primary)]">
                  {consumptionPercentage.toFixed(1)}%
                </span>
              </div>
              <div className="w-full h-2 rounded-full bg-black/30 overflow-hidden relative">
                <div
                  className="h-full rounded-full transition-all duration-500 ease-out"
                  style={{
                    width: `${consumptionPercentage}%`,
                    background: 'linear-gradient(90deg, var(--color-accent-indigo), var(--color-accent-teal))',
                  }}
                />
              </div>
              <p className="text-[9px] text-[var(--color-text-muted)] leading-normal mt-1.5">
                {consumptionPercentage >= 100
                  ? '⚠️ Privacy budget is completely exhausted. Future rounds could degrade differential privacy guarantees.'
                  : '🔒 Privacy parameters are within safe bounds. Gradients remain mathematically masked.'}
              </p>
            </div>
          </div>

          {/* Details callout */}
          <div className="p-3 rounded-lg bg-[var(--color-accent-indigo)]/5 border border-[var(--color-accent-indigo)]/15">
            <p className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">
              <strong>DP Guarantee:</strong> Ensures models do not memorize training records. Even with complete access to the trained global model weights, an adversary cannot reliably guess if a specific transaction (e.g. Bank A's card index) was included in training.
            </p>
          </div>
        </div>

        {/* Right Side: Epsilon History Chart */}
        <div className="lg:col-span-7 flex flex-col min-h-[260px]">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-semibold text-[var(--color-text-primary)]">
              Privacy Budget Consumption Curve
            </p>
            {!hasData && (
              <span className="text-[9px] px-2 py-0.5 rounded bg-[var(--color-status-warning)]/15 text-[var(--color-status-warning)] font-medium animate-pulse">
                Waiting for Round 1...
              </span>
            )}
          </div>
          <div className="flex-1 h-full min-h-0 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 5, right: 10, bottom: 20, left: 10 }}>
                <defs>
                  <linearGradient id="epsilonGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--color-accent-indigo)" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="var(--color-accent-indigo)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" />
                <XAxis
                  dataKey="round"
                  tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  label={{ value: 'Round', position: 'insideBottom', offset: -5, fill: 'var(--color-text-muted)', fontSize: 9 }}
                />
                <YAxis
                  tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
                  axisLine={{ stroke: 'var(--color-border)' }}
                  label={{ value: 'Spent ε', angle: -90, position: 'insideLeft', fill: 'var(--color-text-muted)', fontSize: 9 }}
                  domain={[0, Math.max(targetEpsilon, currentEpsilon + 0.1)]}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--color-bg-card)',
                    border: '1px solid var(--color-border)',
                    borderRadius: '8px',
                    fontSize: '11px',
                    color: 'var(--color-text-primary)',
                  }}
                  formatter={(value: any) => [Number(value ?? 0).toFixed(4), 'Spent Epsilon (ε)']}
                  labelFormatter={(label) => `Round ${label}`}
                />
                <Area
                  type="monotone"
                  dataKey="epsilon"
                  stroke="var(--color-accent-indigo)"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#epsilonGradient)"
                  name="Spent Epsilon (ε)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
