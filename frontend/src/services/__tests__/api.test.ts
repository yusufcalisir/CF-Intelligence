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

  it('runFLSimulation throws error when service is offline or unreachable', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('FL service down'));
    await expect(
      runFLSimulation({
        num_rounds: 3,
        local_epochs: 2,
        learning_rate: 0.01,
        algorithm: 'FED_AVG',
        dp_epsilon: 1.0,
        dp_delta: 1e-5,
      })
    ).rejects.toThrow(/FL service down/i);
  });
});
