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

  it('fetchCounterfactual throws error when fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Endpoint down'));
    await expect(fetchCounterfactual('ALT_999')).rejects.toThrow(/Endpoint down/i);
  });

  it('fetchCounterfactual passes dynamic simulation parameters and maps response', async () => {
    const mockRes = {
      alert_id: 'alt_1001',
      original_score: 820.0,
      remediated_score: 340.0,
      is_cleared: true,
      changes: [
        {
          feature: 'transaction_amount',
          original_value: 15000,
          remediated_value: 2000,
          suggested_value: 2000,
          delta: -13000,
          delta_explanation: 'Reduce single transfer amount',
          description: 'Reduce single transfer amount',
        },
      ],
      summary_text: 'REMEDIATED: Risk score reduced from 820.0 to 340.0.',
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => mockRes,
    } as any);

    const cf = await fetchCounterfactual({
      alert_id: 'alt_1001',
      target_score: 350.0,
      amount: 15000,
      velocity: 28,
      merchant_risk: 0.95,
    });
    expect(cf.alert_id).toBe('alt_1001');
    expect(cf.remediated_score).toBe(340.0);
    expect(cf.changes[0]?.suggested_value).toBe(2000);
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
