import { describe, it, expect, vi, beforeEach } from 'vitest';
import { checkSystemHealth, fetchCounterfactual, runFLSimulation } from '../api';

describe('services/api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('checkSystemHealth returns offline fallback when fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Network offline'));
    const health = await checkSystemHealth();
    expect(health.status).toBe('offline');
    expect(health.service).toBe('cfi-backend');
    expect(health.redis_connected).toBe(false);
  });

  it('fetchCounterfactual returns high-fidelity fallback when fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Endpoint down'));
    const cf = await fetchCounterfactual('ALT_999');
    expect(cf.alert_id).toBe('ALT_999');
    expect(cf.is_cleared).toBe(true);
    expect(cf.changes.length).toBeGreaterThan(0);
    expect(cf.original_score).toBe(780.0);
    expect(cf.remediated_score).toBe(310.0);
  });

  it('runFLSimulation generates multi-round results when offline', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('FL service down'));
    const rounds = await runFLSimulation({
      num_rounds: 3,
      learning_rate: 0.01,
      batch_size: 64,
      local_epochs: 2,
      dp_epsilon: 1.0,
      dp_delta: 1e-5,
      dp_max_grad_norm: 1.0,
      aggregation_method: 'fed_avg',
      byzantine_tolerance: 0,
    });

    expect(rounds).toHaveLength(3);
    expect(rounds[0].round_number).toBe(1);
    expect(rounds[2].round_number).toBe(3);
    expect(rounds[0].global_loss).toBeGreaterThan(0);
  });
});
