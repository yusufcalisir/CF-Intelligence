import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StreamingGNNPanel from '../StreamingGNNPanel';
import type { SimulationDetail } from '../../../api/types';

describe('StreamingGNNPanel Component Test Suite', () => {
  it('renders streaming graph neural network risk score metrics', () => {
    const mockSimulation: SimulationDetail = {
      id: 'sim_test_01',
      config: {
        num_rounds: 10,
        local_epochs: 3,
        learning_rate: 0.01,
        batch_size: 32,
        min_clients_per_round: 3,
        enable_latency_simulation: false,
        latency_min_ms: 5,
        latency_max_ms: 50,
        enable_dropout_simulation: false,
        dropout_probability: 0.0,
        enable_reconnect_simulation: false,
        privacy_mechanism: 'both',
        dp_epsilon: 0.5,
        dp_delta: 0.00001,
        dp_max_grad_norm: 1.0,
        dp_mode: 'post_hoc',
        bank_a_transactions: 1000,
        bank_b_transactions: 800,
        bank_c_transactions: 600,
        aggregation_method: 'fed_avg_weighted',
        enable_poisoning_simulation: false,
        poisoning_bank_id: '',
        poisoning_scale: 1.0,
        fl_engine_type: 'custom',
        enable_streaming_gnn: true,
        hardware_isolation_mode: 'tee',
      },
      status: 'completed',
      current_round: 10,
      total_rounds: 10,
      progress_pct: 100,
      created_at: '2026-07-30T12:00:00Z',
      started_at: '2026-07-30T12:00:00Z',
      completed_at: '2026-07-30T12:05:00Z',
      duration_seconds: 300,
      error_message: null,
      banks: [],
      rounds: [],
      streaming_gnn_node_count: 50,
      streaming_gnn_edge_count: 120,
      streaming_gnn_loss_history: [0.2, 0.1, 0.05],
      tee_mrenclave: '0x99a8b1c4',
      tee_mrsigner: '0x12b4f5a6',
      tee_attestation_signature: 'ed25519_verified_signature',
    };

    render(<StreamingGNNPanel simulation={mockSimulation} />);

    const headings = screen.getAllByText(/Streaming GNN|Anomaly|Risk|Graph|Node/i);
    expect(headings.length).toBeGreaterThan(0);
  });
});
