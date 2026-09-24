import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import type { BankResult } from '../../api/types';

interface FeatureImportanceProps {
  bank: BankResult;
  modelType: 'local' | 'federated';
}

export default function FeatureImportance({ bank, modelType }: FeatureImportanceProps) {
  const metrics = modelType === 'local' ? bank.local_metrics : bank.federated_metrics;
  if (!metrics || !metrics.feature_importance) return null;

  const data = Object.entries(metrics.feature_importance)
    .map(([name, value]) => ({
      name: name.replace(/_/g, ' '),
      importance: parseFloat(value.toFixed(3)),
    }))
    .sort((a, b) => b.importance - a.importance);

  return (
    <div className="glass-card p-5 h-full min-h-[360px] flex flex-col">
      <h3 className="text-sm font-semibold text-[var(--color-text-primary)] mb-1">
        Feature Importance
      </h3>
      <p className="text-[10px] text-[var(--color-text-muted)] mb-3">
        {bank.name} - {modelType === 'local' ? 'Local' : 'Federated'} | First-layer weight magnitude
      </p>
      <div className="h-64 relative flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 5, right: 20, bottom: 5, left: 90 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" horizontal={false} />
            <XAxis
              type="number"
              domain={[0, 1]}
              tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }}
              axisLine={{ stroke: 'var(--color-border)' }}
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fill: 'var(--color-text-secondary)', fontSize: 10 }}
              axisLine={{ stroke: 'var(--color-border)' }}
              width={85}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                borderRadius: '8px',
                fontSize: '12px',
                color: 'var(--color-text-primary)',
              }}
              formatter={(value: any) => [Number(value ?? 0).toFixed(3), 'Importance']}
            />
            <Bar
              dataKey="importance"
              fill="var(--color-accent-indigo)"
              radius={[0, 4, 4, 0]}
              barSize={14}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
