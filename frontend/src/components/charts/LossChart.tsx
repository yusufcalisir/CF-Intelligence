import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import type { TrainingRound } from '../../api/types';

interface LossChartProps {
  rounds: TrainingRound[];
  totalRounds?: number;
}

export default function LossChart({ rounds, totalRounds }: LossChartProps) {
  const hasData = rounds && rounds.length > 0;
  const total = totalRounds ?? 5;

  const placeholderData = Array.from({ length: total }, (_, i) => ({
    round: i + 1,
    loss: null as number | null,
    participants: 0,
    dropped: false,
  }));

  const data = placeholderData.map((placeholder) => {
    const actual = (rounds ?? []).find((r) => r.round_number === placeholder.round);
    if (actual) {
      return {
        round: actual.round_number,
        loss: parseFloat(actual.global_loss.toFixed(4)),
        participants: actual.participating_banks.length,
        dropped: actual.dropped_banks.length > 0,
      };
    }
    return placeholder;
  });

  return (
    <div className="glass-card p-5 min-h-[420px] lg:h-[430px] flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Training Loss Convergence
        </h3>
        {!hasData && (
          <span className="text-[10px] px-2 py-0.5 rounded bg-[var(--color-status-warning)]/15 text-[var(--color-status-warning)] font-medium animate-pulse">
            Waiting for Round 1...
          </span>
        )}
      </div>
      <div className="h-64 relative flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 5, right: 20, bottom: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" />
            <XAxis
              dataKey="round"
              tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--color-border)' }}
              label={{ value: 'Round', position: 'insideBottom', offset: -5, fill: 'var(--color-text-muted)', fontSize: 10 }}
            />
            <YAxis
              tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--color-border)' }}
              label={{ value: 'Loss', angle: -90, position: 'insideLeft', fill: 'var(--color-text-muted)', fontSize: 10 }}
              domain={hasData ? ['auto', 'auto'] : [0, 1]}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                borderRadius: '8px',
                fontSize: '12px',
                color: 'var(--color-text-primary)',
              }}
              formatter={(value: any) => [Number(value ?? 0).toFixed(4), 'Loss']}
              labelFormatter={(label) => `Round ${label}`}
            />
            <Line
              type="monotone"
              dataKey="loss"
              stroke="var(--color-accent-indigo)"
              strokeWidth={2}
              dot={(props: Record<string, unknown>) => {
                const { cx, cy, payload, index } = props as { cx: number; cy: number; payload: { loss: number | null; dropped: boolean }; index: number };
                if (payload?.loss === null || payload?.loss === undefined) {
                  return <g />;
                }
                const isDrop = payload?.dropped;
                return (
                  <circle
                     key={`dot-${index}`}
                     cx={cx}
                     cy={cy}
                     r={isDrop ? 5 : 3}
                     fill={isDrop ? 'var(--color-accent-rose)' : 'var(--color-accent-indigo)'}
                     stroke={isDrop ? 'var(--color-accent-rose)' : 'var(--color-accent-indigo)'}
                     strokeWidth={isDrop ? 2 : 0}
                  />
                );
              }}
              activeDot={{ r: 5, fill: 'var(--color-accent-indigo-light)' }}
              name="Global Loss"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap justify-center items-center gap-x-6 gap-y-2 mt-2 pt-2 border-t border-[var(--color-border-subtle)] text-[11px] text-[var(--color-text-muted)]">
        <div className="flex items-center gap-2">
          <span className="w-3 h-0.5 bg-[var(--color-accent-indigo)]"></span>
          <span>Global Loss</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-[var(--color-accent-rose)]"></span>
          <span>Client Dropout (Anomaly Round)</span>
        </div>
      </div>
    </div>
  );
}
