import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PrivacyMonitor from '../PrivacyMonitor';
import type { SimulationDetail, TrainingRound } from '../../../api/types';

describe('PrivacyMonitor Component', () => {
  const mockSimulation: SimulationDetail = {
    id: 'sim_test_01',
    status: 'COMPLETED' as const,
    current_round: 5,
    config: {
      num_rounds: 10,
      local_epochs: 3,
      learning_rate: 0.001,
      batch_size: 32,
      model_type: 'graph_neural_network' as const,
      aggregation_strategy: 'federated_averaging' as const,
      privacy_mechanism: 'differential_privacy' as const,
      dp_epsilon: 2.0,
      dp_delta: 1e-5,
      dp_mode: 'opacus' as const,
      dp_noise_multiplier: 1.1,
      dp_max_grad_norm: 1.0,
      poison_ratio: 0,
      defense_mechanism: 'none' as const,
      krum_m: 1,
      trimmed_beta: 0.1,
      active_banks: ['bank_a', 'bank_b', 'bank_c'],
    },
    metrics: {} as any,
    bank_results: [],
    start_time: '2026-08-14T00:00:00Z',
  };

  const mockRounds: TrainingRound[] = [
    {
      round_number: 1,
      global_loss: 0.5,
      privacy_budget: 0.45,
      is_anomaly: false,
      anomaly_score: 0.1,
      bank_metrics: {},
    },
    {
      round_number: 5,
      global_loss: 0.2,
      privacy_budget: 1.25,
      is_anomaly: false,
      anomaly_score: 0.05,
      bank_metrics: {},
    },
  ];

  it('renders differential privacy monitor, accountant mode, and spent epsilon', () => {
    render(<PrivacyMonitor simulation={mockSimulation} rounds={mockRounds} />);

    expect(screen.getByText(/Differential Privacy \(DP\) Monitor/i)).toBeInTheDocument();
    expect(screen.getByText(/Opacus \(Moments Accountant\)/i)).toBeInTheDocument();
    expect(screen.getByText('1.2500')).toBeInTheDocument();
    expect(screen.getByText(/Mathematical guarantees bounding individual transaction leakage/i)).toBeInTheDocument();
  });
});
