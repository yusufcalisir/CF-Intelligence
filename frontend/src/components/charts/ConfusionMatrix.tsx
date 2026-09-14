import type { BankResult } from '../../api/types';
import { BANK_COLORS } from '../../api/types';

export interface ConfusionMatrixAtThresholdData {
  threshold?: number;
  true_positives: number;
  false_positives: number;
  true_negatives: number;
  false_negatives: number;
  precision?: number;
  recall?: number;
  fpr?: number;
  fnr?: number;
  f1_score?: number;
}

export interface ConfusionMatrixProps {
  bank?: BankResult;
  modelType?: 'local' | 'federated';
  matrix?: ConfusionMatrixAtThresholdData;
  title?: string;
  subtitle?: string;
}

export default function ConfusionMatrix({
  bank,
  modelType = 'federated',
  matrix,
  title,
  subtitle,
}: ConfusionMatrixProps) {
  let tn = 0;
  let fp = 0;
  let fn = 0;
  let tp = 0;
  const heading = title || 'Confusion Matrix';
  let subheading = subtitle;
  let color = '#6366f1';

  if (matrix) {
    tn = matrix.true_negatives ?? 0;
    fp = matrix.false_positives ?? 0;
    fn = matrix.false_negatives ?? 0;
    tp = matrix.true_positives ?? 0;
    if (!subheading && matrix.threshold !== undefined) {
      subheading = `Decision Threshold τ = ${matrix.threshold.toFixed(2)}`;
    }
  } else if (bank) {
    const metrics = modelType === 'local' ? bank.local_metrics : bank.federated_metrics;
    if (!metrics) return null;

    const cm = metrics.confusion_matrix;
    tn = cm[0]?.[0] ?? 0;
    fp = cm[0]?.[1] ?? 0;
    fn = cm[1]?.[0] ?? 0;
    tp = cm[1]?.[1] ?? 0;
    subheading = subheading || `${bank.name} - ${modelType === 'local' ? 'Local' : 'Federated'}`;
    color = BANK_COLORS[bank.id] ?? '#6366f1';
  } else {
    return null;
  }

  const total = tn + fp + fn + tp || 1;

  const cells = [
    { label: 'TN', value: tn, row: 0, col: 0, intensity: tn / total },
    { label: 'FP', value: fp, row: 0, col: 1, intensity: fp / total, isError: true },
    { label: 'FN', value: fn, row: 1, col: 0, intensity: fn / total, isError: true },
    { label: 'TP', value: tp, row: 1, col: 1, intensity: tp / total },
  ];

  return (
    <div className="glass-card p-5">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          {heading}
        </h3>
        {matrix?.threshold !== undefined && (
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-400 border border-indigo-500/30">
            τ = {matrix.threshold.toFixed(2)}
          </span>
        )}
      </div>
      {subheading && (
        <p className="text-[10px] text-[var(--color-text-muted)] mb-3">
          {subheading}
        </p>
      )}

      <div className="flex justify-center my-2">
        <div>
          {/* Column labels */}
          <div className="flex ml-16">
            <div className="w-24 text-center text-[10px] text-[var(--color-text-muted)] font-medium">Pred: Legit</div>
            <div className="w-24 text-center text-[10px] text-[var(--color-text-muted)] font-medium">Pred: Fraud</div>
          </div>

          {/* Rows */}
          {[0, 1].map((row) => (
            <div key={row} className="flex items-center">
              <div className="w-16 text-right pr-2 text-[10px] text-[var(--color-text-muted)] font-medium">
                {row === 0 ? 'Actual: Legit' : 'Actual: Fraud'}
              </div>
              {[0, 1].map((col) => {
                const cell = cells.find((c) => c.row === row && c.col === col)!;
                const bgOpacity = Math.min(0.65, Math.max(0.12, cell.intensity * 2.5));
                const bgColor = cell.isError
                  ? `rgba(244, 63, 94, ${bgOpacity})`
                  : `rgba(${parseInt(color.slice(1, 3), 16) || 99}, ${parseInt(color.slice(3, 5), 16) || 102}, ${parseInt(color.slice(5, 7), 16) || 241}, ${bgOpacity})`;

                return (
                  <div
                    key={col}
                    className="w-24 h-16 flex flex-col items-center justify-center rounded-md m-0.5 border border-[var(--color-border-subtle)] transition-colors"
                    style={{ background: bgColor }}
                  >
                    <span className="text-base font-bold font-mono text-[var(--color-text-primary)]">
                      {cell.value.toLocaleString()}
                    </span>
                    <span className="text-[9px] text-[var(--color-text-muted)] font-mono">{cell.label}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Operational metrics summary if provided */}
      {matrix && matrix.precision !== undefined && (
        <div className="grid grid-cols-4 gap-2 mt-4 pt-3 border-t border-[var(--color-border-subtle)] text-center text-[10px]">
          <div>
            <div className="text-[var(--color-text-muted)]">Precision</div>
            <div className="font-mono font-bold text-[var(--color-text-primary)]">{(matrix.precision * 100).toFixed(1)}%</div>
          </div>
          <div>
            <div className="text-[var(--color-text-muted)]">Recall</div>
            <div className="font-mono font-bold text-[var(--color-text-primary)]">{((matrix.recall ?? 0) * 100).toFixed(1)}%</div>
          </div>
          <div>
            <div className="text-[var(--color-text-muted)]">FPR</div>
            <div className="font-mono font-bold text-rose-400">{((matrix.fpr ?? 0) * 100).toFixed(3)}%</div>
          </div>
          <div>
            <div className="text-[var(--color-text-muted)]">F1 Score</div>
            <div className="font-mono font-bold text-indigo-400">{(matrix.f1_score ?? 0).toFixed(4)}</div>
          </div>
        </div>
      )}
    </div>
  );
}
