import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import type { BankResult } from '../../api/types';
import { BANK_COLORS } from '../../api/types';

interface ROCCurveProps {
  banks: BankResult[];
  modelType: 'local' | 'federated';
}

export default function ROCCurve({ banks, modelType }: ROCCurveProps) {
  // Build overlaid ROC curve data
  const maxPoints = 50; // Downsample for performance

  const buildCurveData = () => {
    // Collect all unique FPR values as x-axis
    const allFpr = new Set<number>();
    banks.forEach((bank) => {
      const metrics = modelType === 'local' ? bank.local_metrics : bank.federated_metrics;
      if (metrics) {
        const step = Math.max(1, Math.floor(metrics.roc_fpr.length / maxPoints));
        for (let i = 0; i < metrics.roc_fpr.length; i += step) {
          allFpr.add(parseFloat((metrics.roc_fpr[i] ?? 0).toFixed(4)));
        }
      }
    });

    const sortedFpr = Array.from(allFpr).sort((a, b) => a - b);

    return sortedFpr.map((fpr) => {
      const point: Record<string, number> = { fpr };
      banks.forEach((bank) => {
        const metrics = modelType === 'local' ? bank.local_metrics : bank.federated_metrics;
        if (metrics) {
          // Find closest TPR for this FPR
          let closestIdx = 0;
          let closestDist = Infinity;
          for (let i = 0; i < metrics.roc_fpr.length; i++) {
            const dist = Math.abs((metrics.roc_fpr[i] ?? 0) - fpr);
            if (dist < closestDist) {
              closestDist = dist;
              closestIdx = i;
            }
          }
          point[bank.id] = parseFloat((metrics.roc_tpr[closestIdx] ?? 0).toFixed(4));
        }
      });
      return point;
    });
  };

  const data = buildCurveData();
  const title = modelType === 'local' ? 'ROC Curve - Local Models' : 'ROC Curve - Federated Model';

  return (
    <div className="glass-card p-5 min-h-[420px] lg:h-[430px] flex flex-col">
      <div className="flex items-center justify-between mb-4 min-h-[22px]">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</h3>
      </div>
      <div className="h-64 relative flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 5, right: 20, bottom: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" />
            <XAxis
              dataKey="fpr"
              tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--color-border)' }}
              label={{ value: 'False Positive Rate', position: 'insideBottom', offset: -5, fill: 'var(--color-text-muted)', fontSize: 10 }}
              type="number"
              domain={[0, 1]}
            />
            <YAxis
              tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
              axisLine={{ stroke: 'var(--color-border)' }}
              label={{ value: 'True Positive Rate', angle: -90, position: 'insideLeft', fill: 'var(--color-text-muted)', fontSize: 10 }}
              domain={[0, 1]}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                borderRadius: '8px',
                fontSize: '12px',
                color: 'var(--color-text-primary)',
              }}
            />
            {/* Diagonal reference line */}
            <Line
              type="linear"
              dataKey="fpr"
              stroke="var(--color-text-muted)"
              strokeDasharray="5 5"
              strokeWidth={1}
              dot={false}
              name="Random"
              legendType="none"
            />
            {banks.map((bank) => (
              <Line
                key={bank.id}
                type="monotone"
                dataKey={bank.id}
                stroke={BANK_COLORS[bank.id]}
                strokeWidth={2}
                dot={false}
                name={bank.name}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap justify-center items-center gap-x-4 gap-y-2 mt-2 pt-2 border-t border-[var(--color-border-subtle)] text-[11px] text-[var(--color-text-muted)]">
        <div className="flex items-center gap-2">
          <span className="w-3 border-b border-dashed border-[var(--color-text-muted)]"></span>
          <span>Random Guess</span>
        </div>
        {banks.map((bank) => (
          <div key={bank.id} className="flex items-center gap-2">
            <span className="w-3 h-0.5" style={{ backgroundColor: BANK_COLORS[bank.id] }}></span>
            <span>{bank.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
