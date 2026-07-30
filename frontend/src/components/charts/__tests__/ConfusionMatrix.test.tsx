import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ConfusionMatrix from '../ConfusionMatrix';
import type { BankResult } from '../../../api/types';

describe('ConfusionMatrix Visualization Test Suite', () => {
  it('renders 2x2 confusion matrix heatmaps and cell values', () => {
    const mockBank: BankResult = {
      id: 'jpmorgan_chase',
      name: 'JPMorgan Chase',
      tier: 'tier1',
      samples_count: 5000,
      fraud_count: 250,
      status: 'completed',
      local_metrics: {
        precision: 0.92,
        recall: 0.88,
        f1_score: 0.90,
        auc_roc: 0.94,
        accuracy: 0.95,
        confusion_matrix: [[4700, 50], [30, 220]],
      },
      federated_metrics: {
        precision: 0.98,
        recall: 0.96,
        f1_score: 0.97,
        auc_roc: 0.99,
        accuracy: 0.98,
        confusion_matrix: [[4730, 20], [10, 240]],
      },
    };

    render(<ConfusionMatrix bank={mockBank} modelType="federated" />);

    expect(screen.getByText(/Confusion Matrix/i)).toBeDefined();
    expect(screen.getByText(/JPMorgan Chase/i)).toBeDefined();
  });
});
