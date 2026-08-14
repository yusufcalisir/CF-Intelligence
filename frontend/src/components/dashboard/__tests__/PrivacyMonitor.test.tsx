import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PrivacyMonitor from '../PrivacyMonitor';
import type { SimulationDetail, TrainingRound } from '../../../api/types';

describe('PrivacyMonitor Component', () => {
  const mockSimulation: SimulationDetail = {
    id: 'sim_test_01',
    status: 'COMPLETED' as const,
    current_round: 5,
    total_rounds: 10,
    progress_pct: 100,
    created_at: '2026-08-14T00:00:00Z',
    started_at: '2026-08-14T00:00:00Z',
    completed_at: '2026-08-14T00:05:00Z',
    duration_seconds: 300,
    error_message: null,
    banks: [],
    rounds: [],
    config: {
      num_rounds: 10,
      local_epochs: 3,
      learning_rate: 0.001,
      batch_size: 32,
      min_clients_per_round: 2,
      enable_latency_simulation: false,
      latency_min_ms: 50,
      latency_max_ms: 200,
      enable_dropout_simulation: false,
      dropout_probability: 0.0,
      enable_reconnect_simulation: false,
      privacy_mechanism: 'differential_privacy' as const,
      dp_epsilon: 2.0,
      dp_delta: 1e-5,
      dp_mode: 'opacus' as const,
      dp_max_grad_norm: 1.0,
      bank_a_transactions: 1000,
      bank_b_transactions: 1000,
      bank_c_transactions: 1000,
      aggregation_method: 'fed_avg',
      enable_poisoning_simulation: false,
      poisoning_bank_id: 'none',
      poisoning_scale: 1.0,
      fl_engine_type: 'custom',
    },
  };

  const mockRounds: TrainingRound[] = [
    {
      round_number: 1,
      total_rounds: 10,
      global_loss: 0.5,
      participating_banks: ['bank_a', 'bank_b', 'bank_c'],
      dropped_banks: [],
      duration_ms: 250,
      privacy_budget: 0.45,
    },
    {
      round_number: 5,
      total_rounds: 10,
      global_loss: 0.2,
      participating_banks: ['bank_a', 'bank_b', 'bank_c'],
      dropped_banks: [],
      duration_ms: 240,
      privacy_budget: 1.25,
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
