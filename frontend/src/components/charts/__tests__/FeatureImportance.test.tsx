import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import FeatureImportance from '../FeatureImportance';
import type { BankResult } from '../../../api/types';

describe('FeatureImportance Chart Component Test Suite', () => {
  it('renders feature importance bar chart cleanly', () => {
    const mockBank: BankResult = {
      id: 'jpmorgan_chase',
      name: 'JPMorgan Chase',
      tier: 'tier1',
      fraud_ratio: 0.05,
      num_transactions: 5000,
      status: 'completed',
      local_metrics: {
        accuracy: 0.95,
        precision: 0.92,
        recall: 0.88,
        f1_score: 0.90,
        auc_roc: 0.94,
        loss: 0.08,
        confusion_matrix: [[4700, 50], [30, 220]],
        roc_fpr: [0, 0.1, 1],
        roc_tpr: [0, 0.9, 1],
        feature_importance: {
          transaction_amount: 0.35,
          velocity_1h: 0.25,
          device_risk_score: 0.20,
        },
      },
      federated_metrics: null,
      improvement: null,
      data_profile: null,
    };

    render(<FeatureImportance bank={mockBank} modelType="local" />);

    expect(screen.getByText(/Feature Importance/i)).toBeDefined();
  });
});
