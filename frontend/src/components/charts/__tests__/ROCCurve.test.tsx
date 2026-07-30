import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ROCCurve from '../ROCCurve';
import type { BankResult } from '../../../api/types';

describe('ROCCurve Component Test Suite', () => {
  it('renders ROC curve performance overlay chart', () => {
    const mockBanks: BankResult[] = [
      {
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
          roc_fpr: [0, 0.5, 1],
          roc_tpr: [0, 0.8, 1],
        },
        federated_metrics: null,
        improvement: null,
        data_profile: null,
      },
    ];

    render(<ROCCurve banks={mockBanks} modelType="local" />);

    expect(screen.getByText(/ROC Curve - Local Models/i)).toBeDefined();
  });
});
