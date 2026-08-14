import type { SimulationSummary, SimulationDetail, SimulationConfig, TrainingRound } from '../../api/types';

/**
 * Creates a mock SimulationSummary.
 */
export function createMockSimulationSummary(overrides: Partial<SimulationSummary> = {}): SimulationSummary {
  return {
    id: overrides.id || 'sim_20260814_01',
    status: overrides.status || 'completed',
    current_round: overrides.current_round ?? 10,
    total_rounds: overrides.total_rounds ?? 10,
    progress_pct: overrides.progress_pct ?? 100,
    created_at: '2026-08-14T08:00:00Z',
    completed_at: '2026-08-14T08:25:00Z',
    duration_seconds: 1500,
    ...overrides,
  };
}

/**
 * Creates mock Training Round Metrics.
 */
export function createMockTrainingRounds(count: number = 5): TrainingRound[] {
  return Array.from({ length: count }, (_, i) => ({
    round_number: i + 1,
    total_rounds: count,
    global_loss: Math.max(0.08, 0.45 - i * 0.07),
    participating_banks: ['bank_a', 'bank_b', 'bank_c'],
    dropped_banks: [],
    duration_ms: 1200 + i * 50,
    privacy_budget: 0.15 * (i + 1),
    feature_importance: {
      amount: 0.38,
      velocity: 0.32,
      country_code: 0.18,
      device_risk: 0.12,
    },
  }));
}

/**
 * Creates a mock SimulationDetail.
 */
export function createMockSimulationDetail(overrides: Partial<SimulationDetail> = {}): SimulationDetail {
  const defaultConfig: SimulationConfig = {
    num_rounds: 10,
    local_epochs: 2,
    learning_rate: 0.001,
    batch_size: 32,
    min_clients_per_round: 3,
    enable_latency_simulation: false,
    latency_min_ms: 50,
    latency_max_ms: 200,
    enable_dropout_simulation: false,
    dropout_probability: 0.1,
    enable_reconnect_simulation: true,
    privacy_mechanism: 'differential_privacy',
    dp_epsilon: 1.5,
    dp_delta: 1e-5,
    dp_max_grad_norm: 1.0,
    bank_a_transactions: 1000,
    bank_b_transactions: 800,
    bank_c_transactions: 600,
    aggregation_method: 'krum',
    enable_poisoning_simulation: false,
    poisoning_bank_id: 'bank_c',
    poisoning_scale: 1.0,
    fl_engine_type: 'custom',
    ...overrides.config,
  };

  return {
    id: overrides.id || 'sim_20260814_01',
    status: overrides.status || 'completed',
    current_round: overrides.current_round ?? 10,
    total_rounds: overrides.total_rounds ?? 10,
    progress_pct: overrides.progress_pct ?? 100,
    created_at: '2026-08-14T08:00:00Z',
    started_at: '2026-08-14T08:01:00Z',
    completed_at: '2026-08-14T08:25:00Z',
    duration_seconds: 1440,
    error_message: null,
    config: defaultConfig,
    banks: overrides.banks || [],
    rounds: overrides.rounds || createMockTrainingRounds(10),
    ...overrides,
  };
}
