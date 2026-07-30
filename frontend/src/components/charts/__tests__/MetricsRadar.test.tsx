import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import MetricsRadar from '../MetricsRadar';
import type { BankResult } from '../../../api/types';

describe('MetricsRadar Component Test Suite', () => {
  it('renders metrics comparison bar chart across bank consortium', () => {
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
          roc_fpr: [0, 1],
          roc_tpr: [0, 1],
        },
        federated_metrics: {
          accuracy: 0.98,
          precision: 0.96,
          recall: 0.94,
          f1_score: 0.95,
          auc_roc: 0.99,
          loss: 0.03,
          confusion_matrix: [[4730, 20], [10, 240]],
          roc_fpr: [0, 1],
          roc_tpr: [0, 1],
        },
        improvement: null,
        data_profile: null,
      },
    ];

    render(<MetricsRadar banks={mockBanks} />);

    expect(screen.getByText(/Metrics Overview - All Banks/i)).toBeDefined();
  });
});
