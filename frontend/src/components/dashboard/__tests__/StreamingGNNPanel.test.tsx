import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StreamingGNNPanel from '../StreamingGNNPanel';
import type { SimulationDetail } from '../../../api/types';

describe('StreamingGNNPanel Component Test Suite', () => {
  it('renders streaming graph neural network risk score metrics', () => {
    const mockSimulation: SimulationDetail = {
      config: {
        total_rounds: 10,
        clients_per_round: 3,
        min_clients: 3,
        privacy_budget_epsilon: 0.5,
        privacy_budget_delta: 0.00001,
        differential_privacy: true,
        secure_aggregation: true,
        enable_streaming_gnn: true,
        enable_secure_hardware: true,
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
