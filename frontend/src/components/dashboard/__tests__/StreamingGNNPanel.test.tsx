import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StreamingGNNPanel from '../StreamingGNNPanel';
import type { SimulationDetail } from '../../../api/types';

describe('StreamingGNNPanel Component Test Suite', () => {
  it('renders streaming graph neural network risk score metrics', () => {
    const mockSimulation: SimulationDetail = {
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
      global_accuracy: 0.984,
      global_loss: 0.042,
      accuracy_history: [0.88, 0.92, 0.984],
      loss_history: [0.18, 0.08, 0.042],
      clients: [],
      rounds: [],
      streaming_gnn_node_count: 50,
      streaming_gnn_edge_count: 120,
      streaming_gnn_loss_history: [0.2, 0.1, 0.05],
      hardware_enclave_status: {
        attestation_verified: true,
        enclave_type: 'intel_sgx',
        epc_size_mb: 256,
        page_faults: 0,
      },
    };

    render(<StreamingGNNPanel simulation={mockSimulation} />);

    const headings = screen.getAllByText(/Streaming GNN|Anomaly|Risk|Graph|Node/i);
    expect(headings.length).toBeGreaterThan(0);
  });
});
