import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import type { BankResult } from '../../api/types';
import { BANK_COLORS } from '../../api/types';
import { METRIC_LABELS } from '../../utils/constants';

export interface MetricsComparisonBarChartProps {
  banks: BankResult[];
}

const METRICS_KEYS = ['accuracy', 'precision', 'recall', 'f1_score', 'auc_roc'] as const;

export default function MetricsComparisonBarChart({ banks }: MetricsComparisonBarChartProps) {
  // Build grouped bar chart data: each metric has local + federated bars per bank
  const data = METRICS_KEYS.map((metric) => {
    const point: Record<string, string | number> = {
      metric: METRIC_LABELS[metric] ?? metric,
    };
    banks.forEach((bank) => {
      point[`${bank.id}_local`] = bank.local_metrics?.[metric] ?? 0;
      point[`${bank.id}_fed`] = bank.federated_metrics?.[metric] ?? 0;
    });
    return point;
  });

  return (
    <div className="glass-card p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Model Performance Comparison — All Banks
          </h3>
          <p className="text-[11px] text-[var(--color-text-muted)] mt-0.5">
            Grouped evaluation metrics comparing siloed local baselines vs collaborative federated models
          </p>
        </div>
        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
          Grouped Bar Chart
        </span>
      </div>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 5, right: 20, bottom: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" />
            <XAxis
              dataKey="metric"
              tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
              axisLine={{ stroke: 'var(--color-border)' }}
            />
            <YAxis
              domain={[0, 1]}
              tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
              axisLine={{ stroke: 'var(--color-border)' }}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                borderRadius: '8px',
                fontSize: '11px',
                color: 'var(--color-text-primary)',
              }}
              formatter={(value: number) => [(value * 100).toFixed(1) + '%']}
            />
            <Legend wrapperStyle={{ fontSize: '10px' }} />
            {banks.map((bank) => (
              <Bar
                key={`${bank.id}_local`}
                dataKey={`${bank.id}_local`}
                fill={BANK_COLORS[bank.id] || '#6366f1'}
                fillOpacity={0.4}
                name={`${bank.name?.split(' ')[0] || bank.id} Local`}
                radius={[2, 2, 0, 0]}
                barSize={8}
              />
            ))}
            {banks.map((bank) => (
              <Bar
                key={`${bank.id}_fed`}
                dataKey={`${bank.id}_fed`}
                fill={BANK_COLORS[bank.id] || '#14b8a6'}
                name={`${bank.name?.split(' ')[0] || bank.id} Fed.`}
                radius={[2, 2, 0, 0]}
                barSize={8}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export { MetricsComparisonBarChart as MetricsRadar };
