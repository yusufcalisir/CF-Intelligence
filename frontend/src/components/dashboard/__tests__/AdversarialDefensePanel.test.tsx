import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AdversarialDefensePanel } from '../AdversarialDefensePanel';
import type { EvaluationMetrics } from '../../../api/types';

describe('AdversarialDefensePanel Component', () => {
  it('renders defense metrics with active FGSM and PGD defense indicators', () => {
    const mockMetrics: EvaluationMetrics = {
      accuracy: 0.94,
      precision: 0.91,
      recall: 0.88,
      f1_score: 0.895,
      auc_roc: 0.95,
      loss: 0.15,
      confusion_matrix: [
        [900, 100],
        [50, 950],
      ],
      roc_fpr: [0, 0.1, 1],
      roc_tpr: [0, 0.9, 1],
      roc_thresholds: [1, 0.5, 0],
      feature_importance: { amount: 0.4, velocity: 0.6 },
      clean_accuracy: 0.95,
      robust_accuracy: 0.89,
      fgsm_evasion_rate: 0.03,
      pgd_evasion_rate: 0.06,
      adversarial_robustness_score: 0.91,
    };

    render(
      <AdversarialDefensePanel
        metrics={mockMetrics}
        isEnabled={true}
        attackType="pgd"
        epsilon={0.05}
      />
    );

    expect(screen.getByText(/Active Defense & Adversarial ML Training/i)).toBeInTheDocument();
    expect(screen.getByText(/ADV-TRAINING ENABLED \(PGD\)/i)).toBeInTheDocument();
    expect(screen.getByText(/L_inf Noise Bounds/i)).toBeInTheDocument();
  });
});
