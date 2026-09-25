import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StreamingGNNPanel, {
  computeDynamicGATAttentionWeights,
  DATASET_GAT_RELATIONS,
} from '../StreamingGNNPanel';
import type { SimulationDetail } from '../../../api/types';
import { DATASET_PROFILES } from '../../../utils/datasetProfiles';

describe('StreamingGNNPanel Component Test Suite', () => {
  const baseMockSimulation: SimulationDetail = {
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

  it('renders streaming graph neural network risk score metrics', () => {
    render(<StreamingGNNPanel simulation={baseMockSimulation} />);

    const headings = screen.getAllByText(/Streaming GNN|Anomaly|Risk|Graph|Node/i);
    expect(headings.length).toBeGreaterThan(0);
    expect(screen.getByText('Active Graph Nodes')).toBeInTheDocument();
    expect(screen.getByText('Active Graph Edges')).toBeInTheDocument();
  });

  it('computes dynamic PaySim GAT attention weights focusing on cash-out and mule transfer conduits', () => {
    render(<StreamingGNNPanel simulation={baseMockSimulation} datasetProfile={DATASET_PROFILES.paysim} />);

    // PaySim schema items
    expect(screen.getByText(/Cash-Out Agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Destination Account/i)).toBeInTheDocument();
    expect(screen.getByText(/CASH_OUT_DRAIN/i)).toBeInTheDocument();
    expect(screen.getAllByText(/PaySim Mobile Money/i).length).toBeGreaterThan(0);
  });

  it('switches GAT attention topology to IEEE-CIS e-commerce hardware fingerprints and proxy IPs when datasetProfile is changed', () => {
    render(<StreamingGNNPanel simulation={baseMockSimulation} datasetProfile={DATASET_PROFILES.ieee_cis} />);

    // IEEE-CIS schema items
    expect(screen.getByText(/Device Fingerprint/i)).toBeInTheDocument();
    expect(screen.getByText(/Proxy \/ IP CIDR/i)).toBeInTheDocument();
    expect(screen.getByText(/HARDWARE_FINGERPRINT/i)).toBeInTheDocument();
    expect(screen.getAllByText(/IEEE-CIS Fraud Detection/i).length).toBeGreaterThan(0);
  });

  it('switches GAT attention topology to Elliptic Bitcoin peel chains and tumblers when elliptic dataset is provided', () => {
    render(<StreamingGNNPanel simulation={baseMockSimulation} datasetProfile={DATASET_PROFILES.elliptic} />);

    // Elliptic Bitcoin schema items
    expect(screen.getByText(/Peel Chain Hop/i)).toBeInTheDocument();
    expect(screen.getByText(/Mixer \/ Tumbler Node/i)).toBeInTheDocument();
    expect(screen.getByText(/PEELING_CHAIN/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Elliptic Bitcoin AML Graph/i).length).toBeGreaterThan(0);
  });

  it('sharpens GAT attention distribution when online loss converges to lower values', () => {
    const highLossWeights = computeDynamicGATAttentionWeights({
      datasetId: 'paysim',
      lossHistory: [0.85],
    });
    const lowLossWeights = computeDynamicGATAttentionWeights({
      datasetId: 'paysim',
      lossHistory: [0.02],
    });

    // Lowest loss should yield higher concentration on the top primary fraud conduit (Cash-Out Agent)
    const topHighLoss = highLossWeights[0]?.weight ?? 0;
    const topLowLoss = lowLossWeights[0]?.weight ?? 0;

    expect(topLowLoss).toBeGreaterThan(topHighLoss);
    // Both must sum to 1.0 (within float rounding tolerance)
    const sumHigh = highLossWeights.reduce((a, b) => a + b.weight, 0);
    const sumLow = lowLossWeights.reduce((a, b) => a + b.weight, 0);
    expect(Math.abs(sumHigh - 1.0)).toBeLessThan(0.01);
    expect(Math.abs(sumLow - 1.0)).toBeLessThan(0.01);
  });

  it('prioritizes genuine backend streaming_gnn_attention_weights when provided in simulation telemetry', () => {
    const customBackendWeights = [
      { source: 'BankA_Node', target: 'Syndicate_Hub', weight: 0.70, relation: 'CIRCULAR_SMURF' },
      { source: 'BankB_Node', target: 'Mule_Collector', weight: 0.30, relation: 'RAPID_DISPERSAL' },
    ];

    const simWithWeights: SimulationDetail = {
      ...baseMockSimulation,
      streaming_gnn_attention_weights: customBackendWeights,
    };

    render(<StreamingGNNPanel simulation={simWithWeights} />);

    expect(screen.getByText('BankA_Node')).toBeInTheDocument();
    expect(screen.getByText('Syndicate_Hub')).toBeInTheDocument();
    expect(screen.getByText('CIRCULAR_SMURF')).toBeInTheDocument();
    expect(screen.getByText('70.0% attention')).toBeInTheDocument();
    expect(screen.getByText('BACKEND TELEMETRY')).toBeInTheDocument();
  });

  it('computeDynamicGATAttentionWeights normalizes composite weights across 4 GAT heads to 1.0', () => {
    for (const datasetKey of Object.keys(DATASET_GAT_RELATIONS)) {
      const weights = computeDynamicGATAttentionWeights({
        datasetId: datasetKey,
        nodeCount: 1500,
        edgeCount: 6000,
        lossHistory: [0.35, 0.28, 0.15],
      });

      expect(weights.length).toBe(6);
      const sum = weights.reduce((acc, w) => acc + w.weight, 0);
      expect(Math.abs(sum - 1.0)).toBeLessThan(0.005);

      for (const w of weights) {
        expect(w.weight).toBeGreaterThan(0);
        expect(w.headScores?.length).toBe(4);
      }
    }
  });
});
